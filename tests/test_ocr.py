from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ocr import (
    frame_index_to_seconds,
    is_near_duplicate,
    render_screen_text,
    resolve_ocr_languages,
    run_ocr,
)


def make_ocr_settings(
    *,
    cleanup_raw_frames: bool = True,
    ocr_workers: int = 1,
    visual_dedupe: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        ocr_enabled=True,
        ocr_languages="auto",
        ocr_frame_interval_seconds=10,
        ocr_ffmpeg_threads=1,
        ocr_workers=ocr_workers,
        ocr_frame_extract_cpu_limit_percent=80,
        ocr_frame_extract_cpu_limit_period_seconds=0.2,
        ocr_frame_extract_cpu_limit_fallback_burst_cores=1.5,
        ocr_visual_dedupe_enabled=visual_dedupe,
        ocr_visual_dedupe_ignore_bottom_ratio=0.18,
        ocr_visual_dedupe_ignore_right_ratio=0.20,
        ocr_visual_dedupe_max_mean_delta=6.0,
        ocr_signature_cpu_limit_percent=0,
        ocr_signature_cpu_limit_period_seconds=0.2,
        ocr_signature_cpu_limit_fallback_burst_cores=2.5,
        ocr_tesseract_thread_limit=1,
        ocr_tesseract_nice=10,
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

    def test_successful_ocr_removes_raw_frames_but_keeps_snapshots(self) -> None:
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
                    "scripts.ocr.visual_frame_signature",
                    side_effect=[b"\x00", b"\xff"],
                ),
                patch("scripts.ocr.ocr_frame", side_effect=["첫 화면", "두 번째 화면"]),
            ):
                result = run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(),
                    detected_language="ko",
                )

            self.assertFalse((root / "job" / "frames").exists())
            self.assertEqual(len(list((root / "job" / "snapshots").glob("*.jpg"))), 2)
            self.assertTrue(result["raw_frames_cleaned"])
            self.assertEqual(result["raw_frame_count"], 2)
            self.assertGreater(result["raw_frames_reclaimed_bytes"], 0)
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
                patch("scripts.ocr.visual_frame_signature", return_value=b"\x00"),
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

        def fake_signature(frame_path: Path, *args: object) -> bytes:
            return signatures[frame_path.name]

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
                    patch("scripts.ocr.visual_frame_signature", side_effect=fake_signature),
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
                run_ocr(
                    video,
                    root / "job",
                    make_ocr_settings(ocr_workers=3, visual_dedupe=False),
                    detected_language="ko",
                )

        self.assertEqual(peak_active, 3)


if __name__ == "__main__":
    unittest.main()
