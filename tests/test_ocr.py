from __future__ import annotations

import json
import tempfile
import subprocess
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ocr import (
    extract_frames,
    frame_index_to_seconds,
    is_near_duplicate,
    render_screen_text,
    resolve_ocr_languages,
    run_tesseract,
    run_ocr,
    visual_frame_signature_pair,
)


def make_ocr_settings(
    *,
    cleanup_raw_frames: bool = True,
    ocr_workers: int = 1,
    visual_dedupe: bool = True,
    frame_interval_seconds: int = 10,
    max_snapshot_gap_seconds: int = 120,
) -> SimpleNamespace:
    return SimpleNamespace(
        ocr_enabled=True,
        ocr_languages="auto",
        ocr_frame_interval_seconds=frame_interval_seconds,
        ocr_ffmpeg_threads=1,
        ocr_workers=ocr_workers,
        ocr_frame_extract_cpu_limit_percent=0,
        ocr_frame_extract_cpu_limit_period_seconds=0.2,
        ocr_frame_extract_cpu_limit_fallback_burst_cores=1.5,
        ocr_visual_dedupe_enabled=visual_dedupe,
        ocr_visual_dedupe_ignore_bottom_ratio=0.18,
        ocr_visual_dedupe_ignore_right_ratio=0.20,
        ocr_visual_dedupe_max_mean_delta=6.0,
        ocr_max_snapshot_gap_seconds=max_snapshot_gap_seconds,
        ocr_visual_only_min_mean_delta=12.0,
        ocr_signature_cpu_limit_percent=0,
        ocr_signature_cpu_limit_period_seconds=0.2,
        ocr_signature_cpu_limit_fallback_burst_cores=2.5,
        ocr_tesseract_thread_limit=1,
        ocr_tesseract_nice=0,
        ocr_tesseract_cpu_limit_percent=0,
        ocr_tesseract_cpu_limit_period_seconds=0.2,
        ocr_tesseract_cpu_limit_fallback_burst_cores=2.5,
        ocr_frame_pause_seconds=0.0,
        cleanup_job_ocr_images_after_archive=cleanup_raw_frames,
    )


class OcrRegressionTests(unittest.TestCase):
    def test_frame_timestamp_uses_configured_interval(self) -> None:
        from pathlib import Path

        self.assertEqual(frame_index_to_seconds(Path("frame_000004.jpg"), 10), 30)

    def test_duplicate_text_ignores_spacing_and_punctuation(self) -> None:
        self.assertTrue(is_near_duplicate("회의 안건: 배포", "회의안건 배포"))

    def test_screen_text_keeps_frame_timestamps(self) -> None:
        rendered = render_screen_text(
            [
                {"timestamp": "00:00:10", "text": "첫 화면"},
                {"timestamp": "00:00:20", "text": "두 번째 화면"},
            ]
        )
        self.assertEqual(
            rendered,
            "[00:00:10]\n첫 화면\n\n[00:00:20]\n두 번째 화면\n",
        )

    def test_auto_ocr_language_follows_stt_language(self) -> None:
        self.assertEqual(resolve_ocr_languages("auto", "en"), "eng")
        self.assertEqual(resolve_ocr_languages("auto", "ko"), "kor+eng")
        self.assertEqual(resolve_ocr_languages("auto", None), "eng+kor")

    def test_explicit_ocr_language_is_preserved(self) -> None:
        self.assertEqual(resolve_ocr_languages("eng", "ko"), "eng")

    def test_successful_ocr_retains_raw_frames_and_selected_snapshots(self) -> None:
        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            (frames_dir / "frame_000001.jpg").write_bytes(b"first-frame")
            (frames_dir / "frame_000002.jpg").write_bytes(b"second-frame")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            with (
                patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                patch(
                    "scripts.ocr.visual_frame_signature_pair",
                    side_effect=[(b"\x00", b"\x00"), (b"\xff", b"\xff")],
                ),
                patch("scripts.ocr.ocr_frame", side_effect=["첫 화면", "두 번째 화면"]),
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(),
                    detected_language="ko",
                )

            self.assertTrue((root / "job" / "frames").exists())
            self.assertEqual(len(list((root / "job" / "snapshots").glob("*.jpg"))), 2)
            self.assertFalse(result["raw_frames_cleaned"])
            self.assertEqual(result["raw_frame_count"], 2)
            self.assertEqual(result["raw_frames_reclaimed_bytes"], 0)
            self.assertEqual(result["raw_frames_retention"], "completed_job_retention")
            self.assertEqual(sum(result["selection_reason_counts"].values()), 2)
            self.assertTrue(
                all("frame" not in item and "source_frame" in item for item in result["frames"])
            )

    def test_raw_frames_can_be_retained_for_diagnostics(self) -> None:
        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            (frames_dir / "frame_000001.jpg").write_bytes(b"frame")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            with (
                patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                patch(
                    "scripts.ocr.visual_frame_signature_pair",
                    return_value=(b"\x00", b"\x00"),
                ),
                patch("scripts.ocr.ocr_frame", return_value="화면"),
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(cleanup_raw_frames=False),
                    detected_language="ko",
                )

            self.assertTrue((root / "job" / "frames").exists())
            self.assertFalse(result["raw_frames_cleaned"])

    def test_parallel_workers_preserve_ordered_dedupe_output(self) -> None:
        signatures = {
            "frame_000001.jpg": b"\x00",
            "frame_000002.jpg": b"\x00",
            "frame_000003.jpg": b"\x40",
            "frame_000004.jpg": b"\x80",
            "frame_000005.jpg": b"\x80",
            "frame_000006.jpg": b"\xff",
        }
        texts = {
            "frame_000001.jpg": "첫 화면",
            "frame_000002.jpg": "중복이라 사용하지 않음",
            "frame_000003.jpg": "두 번째 화면",
            "frame_000004.jpg": "세 번째 화면",
            "frame_000005.jpg": "시각 중복이라 사용하지 않음",
            "frame_000006.jpg": "마지막 화면",
        }

        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            for name in signatures:
                (frames_dir / name).write_bytes(name.encode("utf-8"))

        def fake_signature(
            frame_path: Path,
            *args: object,
        ) -> tuple[bytes, bytes]:
            signature = signatures[frame_path.name]
            return signature, signature

        def fake_ocr(frame_path: Path, *args: object) -> str:
            return texts[frame_path.name]

        outputs = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            for workers in (1, 3):
                with (
                    patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                    patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                    patch(
                        "scripts.ocr.visual_frame_signature_pair",
                        side_effect=fake_signature,
                    ),
                    patch("scripts.ocr.ocr_frame", side_effect=fake_ocr),
                ):
                    result = run_ocr(
                        video,
                        root / f"job-{workers}",
                        make_ocr_settings(ocr_workers=workers),
                        detected_language="ko",
                    )
                outputs.append(
                    [
                        (item["timestamp"], item["source_frame"], item["text"])
                        for item in result["frames"]
                    ]
                )
                self.assertEqual(result["workers"], workers)

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(
            outputs[0],
            [
                ("00:00:00", "frame_000001.jpg", "첫 화면"),
                ("00:00:20", "frame_000003.jpg", "두 번째 화면"),
                ("00:00:30", "frame_000004.jpg", "세 번째 화면"),
                ("00:00:50", "frame_000006.jpg", "마지막 화면"),
            ],
        )

    def test_parallel_ocr_never_exceeds_configured_worker_count(self) -> None:
        active = 0
        peak_active = 0
        lock = threading.Lock()

        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            for index in range(1, 7):
                (frames_dir / f"frame_{index:06}.jpg").write_bytes(b"frame")

        def fake_ocr(frame_path: Path, *args: object) -> str:
            nonlocal active, peak_active
            with lock:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return frame_path.stem

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            with (
                patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                patch("scripts.ocr.ocr_frame", side_effect=fake_ocr),
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(ocr_workers=3, visual_dedupe=False),
                    detected_language="ko",
                )

        self.assertEqual(peak_active, 3)
        tesseract_metrics = result["metrics"]["phases"]["tesseract"]
        self.assertEqual(tesseract_metrics["call_count"], 6)
        self.assertEqual(tesseract_metrics["queue_capacity"], 6)
        self.assertEqual(tesseract_metrics["peak_active_workers"], 3)
        self.assertGreater(tesseract_metrics["average_active_workers"], 1.0)
        self.assertEqual(result["metrics"]["resource"]["ffmpeg_threads"], 1)
        self.assertEqual(
            result["selection_reason_counts"],
            {"selected": 6},
        )

    def test_zero_tesseract_nice_adds_no_second_nice_wrapper(self) -> None:
        completed = subprocess.CompletedProcess(["tesseract"], 0, "text", "")
        with patch("scripts.ocr.run_limited", return_value=completed) as limited:
            result = run_tesseract(
                Path("frame.jpg"),
                "eng",
                1,
                0,
                0,
                0.2,
                2.5,
                True,
            )

        command = limited.call_args.args[0]
        self.assertEqual(result.stdout, "text")
        self.assertEqual(command[0], "tesseract")
        self.assertNotIn("nice", command)

    def test_static_video_forces_snapshot_coverage_every_120_seconds(self) -> None:
        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            for index in range(1, 26):
                (frames_dir / f"frame_{index:06}.jpg").write_bytes(
                    f"frame-{index}".encode("utf-8")
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            with (
                patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                patch(
                    "scripts.ocr.visual_frame_signature_pair",
                    return_value=(b"\x00" * 16, b"\x00" * 16),
                ),
                patch("scripts.ocr.ocr_frame", return_value="고정 슬라이드") as ocr,
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(max_snapshot_gap_seconds=120),
                    detected_language="ko",
                )

            coverage = json.loads(
                (root / "job" / "evidence_coverage.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(ocr.call_count, 3)
        self.assertEqual(
            [frame["timestamp_seconds"] for frame in result["frames"]],
            [0, 120, 240],
        )
        self.assertEqual(coverage["max_selected_snapshot_gap_seconds"], 120)
        self.assertTrue(coverage["coverage_passed"])
        self.assertTrue(coverage["accounting_complete"])
        self.assertEqual(coverage["raw_frame_count"], 25)
        self.assertEqual(coverage["selected_snapshot_count"], 3)
        self.assertEqual(coverage["reason_counts"]["forced_coverage"], 2)
        self.assertEqual(sum(coverage["reason_counts"].values()), 25)

    def test_ocr_empty_material_change_is_preserved_as_visual_only(self) -> None:
        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            for index in range(1, 3):
                (frames_dir / f"frame_{index:06}.jpg").write_bytes(b"frame")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            with (
                patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                patch(
                    "scripts.ocr.visual_frame_signature_pair",
                    side_effect=[
                        (b"\x00" * 16, b"\x00" * 16),
                        (b"\xff" * 16, b"\xff" * 16),
                    ],
                ),
                patch("scripts.ocr.ocr_frame", side_effect=["제목", ""]),
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(),
                    detected_language="ko",
                )

        self.assertEqual(len(result["frames"]), 2)
        self.assertEqual(result["frames"][1]["selection_reason"], "visual_only")
        self.assertEqual(result["frames"][1]["text"], "")

    def test_ui_only_change_is_separate_snapshot_evidence(self) -> None:
        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            for index in range(1, 3):
                (frames_dir / f"frame_{index:06}.jpg").write_bytes(b"frame")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            with (
                patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                patch(
                    "scripts.ocr.visual_frame_signature_pair",
                    side_effect=[
                        (b"\x00" * 16, b"\x00" * 16),
                        (b"\xff" * 16, b"\x00" * 16),
                    ],
                ),
                patch("scripts.ocr.ocr_frame", side_effect=["슬라이드", "참가자 A"]),
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(),
                    detected_language="ko",
                )

        self.assertEqual(result["frames"][1]["selection_reason"], "speaker_ui_change")
        self.assertEqual(result["selection_reason_counts"]["speaker_ui_change"], 1)

    def test_small_localized_numeric_change_becomes_an_ocr_candidate(self) -> None:
        before = b"\x00" * 576
        after = bytearray(before)
        after[10:13] = b"\x60\x60\x60"

        def fake_extract_frames(
            video_path: Path,
            frames_dir: Path,
            *args: object,
        ) -> None:
            frames_dir.mkdir(parents=True)
            for index in range(1, 3):
                (frames_dir / f"frame_{index:06}.jpg").write_bytes(b"frame")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mov"
            video.write_bytes(b"video")
            with (
                patch("scripts.ocr.shutil.which", return_value="/usr/bin/tesseract"),
                patch("scripts.ocr.extract_frames", side_effect=fake_extract_frames),
                patch(
                    "scripts.ocr.visual_frame_signature_pair",
                    side_effect=[(before, before), (bytes(after), bytes(after))],
                ),
                patch("scripts.ocr.ocr_frame", side_effect=["41", "42"]) as ocr,
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(),
                    detected_language="ko",
                )

        self.assertEqual(ocr.call_count, 2)
        self.assertEqual([frame["text"] for frame in result["frames"]], ["41", "42"])

    def test_signature_pair_uses_one_ffmpeg_process_for_full_and_crop(self) -> None:
        raw = (b"F" * 576) + (b"C" * 576)
        completed = subprocess.CompletedProcess(["ffmpeg"], 0, raw, b"")
        with patch("scripts.ocr.run_limited", return_value=completed) as limited:
            full, content = visual_frame_signature_pair(
                Path("frame.jpg"),
                0.18,
                0.20,
                1,
                0,
                0.2,
                2.5,
            )

        command = limited.call_args.args[0]
        self.assertEqual(full, b"F" * 576)
        self.assertEqual(content, b"C" * 576)
        self.assertEqual(command.count("ffmpeg"), 1)
        self.assertIn("-filter_complex", command)
        self.assertIn("vstack=inputs=2", " ".join(command))

    def test_frame_extraction_suppresses_progress_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("scripts.ocr.run_limited") as run_limited:
                extract_frames(
                    Path(temp_dir) / "video.mov",
                    Path(temp_dir) / "frames",
                    5,
                    1,
                    0,
                    0.2,
                    1.5,
                )

        command = run_limited.call_args.args[0]
        self.assertIn("-hide_banner", command)
        self.assertEqual(command[command.index("-loglevel") + 1], "error")
        self.assertIn("-nostats", command)
        self.assertIn("-nostdin", command)


if __name__ == "__main__":
    unittest.main()
