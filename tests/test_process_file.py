from __future__ import annotations

import json
import tempfile
import types
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from scripts.process_file import process_file
from scripts.utils import file_fingerprint, now_local, write_json


def make_settings(root: Path, speaker_mode: str = "evidence") -> SimpleNamespace:
    return SimpleNamespace(
        recordings_inbox=root / "inbox",
        jobs_dir=root / "jobs",
        output_dir=root / "output",
        llm_provider="codex",
        ocr_enabled=True,
        speaker_attribution_mode=speaker_mode,
        speaker_attribution_required=False,
        speech_activity_validation_enabled=False,
        vad_model_dir=root / "models" / "silero-vad-6.2.1",
        process_qos="utility",
        process_nice=10,
        ocr_ffmpeg_threads=2,
        ocr_workers=3,
        ocr_tesseract_thread_limit=1,
        ocr_prestart_cooldown_seconds=0.0,
        audio_sample_rate=16000,
        cleanup_job_media_after_archive=True,
        cleanup_job_ocr_images_after_archive=True,
        completed_job_retention_hours=24,
        docx_enabled=False,
        output_language="ko",
        content_audit_mode="off",
        official_source_verification="off",
    )


def fake_transcribe_module(language: str | None = None) -> types.ModuleType:
    module = types.ModuleType("scripts.transcribe")

    def extract_audio(source: Path, audio_path: Path, settings: object) -> None:
        audio_path.write_bytes(b"audio")

    def load_pcm_wav(
        audio_path: Path,
        *,
        expected_sample_rate: int | None = None,
    ) -> tuple[np.ndarray, int]:
        return np.zeros(16, dtype=np.float32), expected_sample_rate or 16000

    def transcribe_audio(
        audio_path: Path,
        transcript_path: Path,
        settings: object,
        *,
        waveform: np.ndarray | None = None,
    ) -> dict:
        assert waveform is not None
        result = {
            "text": "회의 전사",
            "segments": [{"start": 0.0, "end": 1.0, "text": "회의 전사"}],
        }
        if language is not None:
            result["language"] = language
        transcript_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        transcript_path.with_suffix(".txt").write_text("회의 전사\n", encoding="utf-8")
        transcript_path.with_suffix(".srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n회의 전사\n",
            encoding="utf-8",
        )
        return result

    module.extract_audio = extract_audio
    module.load_pcm_wav = load_pcm_wav
    module.transcribe_audio = transcribe_audio
    return module


def forbidden_diarization_module() -> tuple[types.ModuleType, Mock]:
    module = types.ModuleType("scripts.diarize")
    spy = Mock(
        side_effect=AssertionError(
            "local audio diarization must never run in the automatic workflow"
        )
    )
    module.run_diarization = spy
    return module, spy


class ProcessFileRegressionTests(unittest.TestCase):
    def test_strict_codex_mode_writes_audit_and_official_source_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "briefing.wav"
            source.write_bytes(b"source")
            settings = make_settings(root)
            settings.content_audit_mode = "strict"
            settings.official_source_verification = "auto"

            with (
                patch("scripts.process_file.load_settings", return_value=settings),
                patch.dict("sys.modules", {"scripts.transcribe": fake_transcribe_module()}),
            ):
                job_dir = process_file(source)

            prompt = (job_dir / "codex_minutes_input.md").read_text(encoding="utf-8")
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertIn("content_inventory.json", prompt)
            self.assertIn("official_sources.json", prompt)
            self.assertIn("하드 상한이 없습니다", prompt)
            self.assertEqual(status["content_audit"]["mode"], "strict")
            self.assertEqual(
                status["content_audit"]["official_source_verification"],
                "auto",
            )
            self.assertTrue(status["codex_handoff"]["fresh_context_required"])
            self.assertEqual(status["codex_handoff"]["output_language"], "ko")
            self.assertFalse(status["codex_handoff"]["docx_enabled"])
            self.assertEqual(status["codex_handoff"]["selected_snapshot_count"], 0)
            self.assertEqual(
                Path(status["codex_handoff"]["input_path"]),
                job_dir / "codex_minutes_input.md",
            )

    def test_new_processing_purges_only_expired_completed_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "meeting.wav"
            source.write_bytes(b"source")
            old_job = root / "jobs" / "expired"
            old_output = root / "output" / "expired"
            old_job.mkdir(parents=True)
            old_output.mkdir(parents=True)
            old_media = old_output / "video.mov"
            old_minutes = old_output / "minutes.md"
            old_media.write_bytes(b"video")
            old_minutes.write_text("# 이전 문서\n", encoding="utf-8")
            write_json(
                old_job / "status.json",
                {
                    "status": "completed",
                    "completed_at": (now_local() - timedelta(days=2)).isoformat(),
                    "output_dir": str(old_output),
                    "files": {
                        "video": str(old_media),
                        "minutes": str(old_minutes),
                    },
                },
            )

            with (
                patch("scripts.process_file.load_settings", return_value=make_settings(root)),
                patch.dict("sys.modules", {"scripts.transcribe": fake_transcribe_module()}),
            ):
                new_job = process_file(source)

            self.assertFalse(old_job.exists())
            self.assertTrue(old_media.exists())
            self.assertTrue(old_minutes.exists())
            self.assertTrue(new_job.exists())

    def test_audio_codex_mode_completes_without_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "meeting.wav"
            source.write_bytes(b"source")

            with (
                patch("scripts.process_file.load_settings", return_value=make_settings(root)),
                patch.dict("sys.modules", {"scripts.transcribe": fake_transcribe_module()}),
            ):
                job_dir = process_file(source)

            self.assertIn("회의 전사", (job_dir / "codex_minutes_input.md").read_text(encoding="utf-8"))
            screen = json.loads((job_dir / "screen_text.json").read_text(encoding="utf-8"))
            self.assertEqual(screen["status"], "skipped")
            self.assertTrue((job_dir / "source.wav").exists())
            self.assertFalse((job_dir / "audio.wav").exists())
            metrics = json.loads(
                (job_dir / "process_metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metrics["state"], "awaiting_codex")
            self.assertGreater(
                metrics["intermediate_cleanup"]["audio_wav"]["reclaimed_bytes"],
                0,
            )
            completed_steps = [stage["step"] for stage in metrics["stages"]]
            for required_audio_step in ("extract_audio", "load_audio", "transcribe"):
                self.assertIn(required_audio_step, completed_steps)
            self.assertNotIn("diarize", completed_steps)
            self.assertNotIn("attribute_speakers", completed_steps)

    def test_video_ocr_failure_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "meeting.mp4"
            source.write_bytes(b"source")
            settings = make_settings(root)
            settings.ocr_prestart_cooldown_seconds = 2.5

            with (
                patch("scripts.process_file.load_settings", return_value=settings),
                patch.dict("sys.modules", {"scripts.transcribe": fake_transcribe_module()}),
                patch("scripts.ocr.run_ocr", side_effect=RuntimeError("ocr failed")),
                patch("scripts.process_file.time.sleep") as sleep,
            ):
                job_dir = process_file(source)

            screen = json.loads((job_dir / "screen_text.json").read_text(encoding="utf-8"))
            metrics = json.loads(
                (job_dir / "process_metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(screen["status"], "failed")
            self.assertTrue((job_dir / "codex_minutes_input.md").exists())
            sleep.assert_called_once_with(2.5)
            self.assertIn(
                "pre_ocr_cooldown",
                {stage["step"] for stage in metrics["stages"]},
            )

    def test_evidence_mode_uses_timed_stt_ocr_and_snapshots_without_diarization(
        self,
    ) -> None:
        def fake_ocr(
            source: Path,
            job_dir: Path,
            settings: object,
            **kwargs: object,
        ) -> dict:
            result = {
                "enabled": True,
                "status": "completed",
                "frames": [
                    {
                        "timestamp": "00:00:00",
                        "text": "Alex Kim",
                    }
                ],
            }
            write_json(job_dir / "screen_text.json", result)
            (job_dir / "screen_text.txt").write_text(
                "[00:00:00]\nAlex Kim\n",
                encoding="utf-8",
            )
            snapshots = job_dir / "snapshots"
            snapshots.mkdir()
            (snapshots / "00-00-00.jpg").write_bytes(b"snapshot")
            return result

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "video.mp4"
            source.write_bytes(b"source")
            diarize_module, diarize_spy = forbidden_diarization_module()

            with (
                patch(
                    "scripts.process_file.load_settings",
                    return_value=make_settings(root, "evidence"),
                ),
                patch.dict(
                    "sys.modules",
                    {
                        "scripts.transcribe": fake_transcribe_module(),
                        "scripts.diarize": diarize_module,
                    },
                ),
                patch("scripts.ocr.run_ocr", side_effect=fake_ocr),
            ):
                job_dir = process_file(source)

            diarize_spy.assert_not_called()
            codex_input = (job_dir / "codex_minutes_input.md").read_text(
                encoding="utf-8"
            )
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            report = json.loads(
                (job_dir / "speaker_attribution_report.json").read_text(
                    encoding="utf-8"
                )
            )
            metrics = json.loads(
                (job_dir / "process_metrics.json").read_text(encoding="utf-8")
            )
            self.assertIn("화자 식별 모드: evidence", codex_input)
            self.assertIn("[00:00:00.000 - 00:00:01.000] 회의 전사", codex_input)
            self.assertIn("[00:00:00]\nAlex Kim", codex_input)
            self.assertIn("화면 근거가 없거나 약해도 로컬 화자분리", codex_input)
            self.assertEqual(status["speaker_attribution"]["effective_mode"], "evidence")
            self.assertEqual(
                status["speaker_attribution"]["local_audio_diarization"],
                "disabled_by_policy",
            )
            self.assertEqual(
                report["identity_resolution_method"],
                "llm_timestamped_stt_ocr_selected_snapshots",
            )
            self.assertFalse(report["audio_separation_available"])
            self.assertEqual(
                metrics["resource_policy"]["local_audio_diarization"],
                "disabled_by_policy",
            )
            self.assertEqual(metrics["resource_policy"]["ocr_workers"], 3)
            self.assertEqual(metrics["resource_policy"]["ocr_ffmpeg_threads"], 2)
            self.assertEqual(
                metrics["resource_policy"]["ocr_prestart_cooldown_seconds"],
                0.0,
            )
            self.assertEqual(
                metrics["resource_policy"]["ocr_tesseract_thread_limit"],
                1,
            )
            self.assertNotIn("diarize", {stage["step"] for stage in metrics["stages"]})
            self.assertNotIn(
                "attribute_speakers",
                {stage["step"] for stage in metrics["stages"]},
            )
            self.assertFalse((job_dir / "diarization.json").exists())
            self.assertFalse((job_dir / "attributed_transcript.txt").exists())
            self.assertNotIn("화자 1: 회의 전사", codex_input)
            self.assertIn("화자 미상", codex_input)

    def test_evidence_policy_without_screen_is_language_independent(self) -> None:
        for language in ("en", "ko"):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source = root / "recording.wav"
                source.write_bytes(b"source")
                diarize_module, diarize_spy = forbidden_diarization_module()
                with (
                    patch(
                        "scripts.process_file.load_settings",
                        return_value=make_settings(root, "evidence"),
                    ),
                    patch.dict(
                        "sys.modules",
                        {
                            "scripts.transcribe": fake_transcribe_module(language),
                            "scripts.diarize": diarize_module,
                        },
                    ),
                ):
                    job_dir = process_file(source)

                diarize_spy.assert_not_called()
                prompt = (job_dir / "codex_minutes_input.md").read_text(
                    encoding="utf-8"
                )
                report = json.loads(
                    (job_dir / "speaker_attribution_report.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertIn("화면 OCR 근거가 없습니다", prompt)
                self.assertIn("화면 근거가 없거나 약해도 로컬 화자분리", prompt)
                self.assertNotIn("화자 1: 회의 전사", prompt)
                self.assertEqual(
                    report["identity_resolution_method"],
                    "llm_explicit_stt_only",
                )
                self.assertFalse(report["screen_evidence_available"])

    def test_successful_non_codex_archive_cleans_job_media_after_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "meeting.wav"
            source.write_bytes(b"source")
            settings = make_settings(root)
            settings.llm_provider = "openai"

            with (
                patch("scripts.process_file.load_settings", return_value=settings),
                patch.dict("sys.modules", {"scripts.transcribe": fake_transcribe_module()}),
                patch(
                    "scripts.process_file.generate_minutes",
                    return_value={
                        "document_title": "테스트 분석",
                        "document_type": "오디오 분석",
                    },
                ),
                patch(
                    "scripts.process_file.render_markdown",
                    return_value="# 테스트 분석\n\n문서 유형: 오디오 분석\n",
                ),
            ):
                output_dir = process_file(source)

            job_dirs = [path for path in (root / "jobs").iterdir() if path.is_dir()]
            self.assertEqual(len(job_dirs), 1)
            job_dir = job_dirs[0]
            self.assertTrue(next(output_dir.glob("*.wav")).exists())
            self.assertEqual(
                sorted(path.suffix for path in output_dir.iterdir()),
                [".md", ".wav"],
            )
            self.assertFalse((job_dir / "source.wav").exists())
            self.assertFalse((job_dir / "audio.wav").exists())
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")
            self.assertTrue(status["cleaned_job_media"])

    def test_legacy_local_audio_modes_are_rejected_before_processing(self) -> None:
        for mode in ("audio", "hybrid"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source = root / "meeting.wav"
                source.write_bytes(b"source")
                settings = make_settings(root, mode)
                with patch("scripts.process_file.load_settings", return_value=settings):
                    with self.assertRaisesRegex(
                        ValueError,
                        "Automatic local audio diarization is disabled by policy",
                    ):
                        process_file(source)
                self.assertTrue(source.exists())

    def test_required_speaker_identity_is_rejected_in_evidence_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "meeting.wav"
            source.write_bytes(b"source")
            settings = make_settings(root, "evidence")
            settings.speaker_attribution_required = True

            with patch("scripts.process_file.load_settings", return_value=settings):
                with self.assertRaisesRegex(
                    ValueError,
                    "uncertain speakers must remain unknown",
                ):
                    process_file(source)

            self.assertTrue(source.exists())

    def test_evidence_profile_reprocesses_prior_off_result_without_diarization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "meeting.wav"
            source.write_bytes(b"source")
            diarize_module, diarize_spy = forbidden_diarization_module()
            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            (jobs_dir / "index.json").write_text(
                json.dumps(
                    {
                        "processed_files": [
                            {
                                "fingerprint": file_fingerprint(source),
                                "output_dir": str(root / "old-output"),
                                "speaker_attribution_mode": "off",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch(
                    "scripts.process_file.load_settings",
                    return_value=make_settings(root, "evidence"),
                ),
                patch.dict(
                    "sys.modules",
                    {
                        "scripts.transcribe": fake_transcribe_module(),
                        "scripts.diarize": diarize_module,
                    },
                ),
            ):
                job_dir = process_file(source)

            diarize_spy.assert_not_called()
            self.assertNotEqual(job_dir, root / "old-output")
            self.assertTrue((job_dir / "transcript.evidence.txt").exists())
            self.assertFalse((job_dir / "attributed_transcript.txt").exists())

    def test_inbox_video_is_moved_and_preserves_filename_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            source = inbox / "2026-02-12 13-37-50.mov"
            source.write_bytes(b"source")
            settings = make_settings(root)
            settings.llm_provider = "openai"

            with (
                patch("scripts.process_file.load_settings", return_value=settings),
                patch.dict("sys.modules", {"scripts.transcribe": fake_transcribe_module()}),
                patch("scripts.ocr.run_ocr", side_effect=RuntimeError("ocr skipped")),
                patch(
                    "scripts.process_file.generate_minutes",
                    return_value={
                        "document_title": "MySQL HeatWave 운영 정책",
                        "document_type": "기술 세션 분석",
                    },
                ),
                patch(
                    "scripts.process_file.render_markdown",
                    return_value=(
                        "# MySQL HeatWave 운영 정책\n\n"
                        "문서 유형: 기술 세션 분석\n"
                    ),
                ),
            ):
                output_dir = process_file(source)

            self.assertFalse(source.exists())
            self.assertEqual(output_dir.parent, settings.output_dir)
            self.assertEqual(
                output_dir.name,
                "2026-02-12_MySQL-HeatWave-운영-정책",
            )
            self.assertTrue(
                (output_dir / "2026-02-12_MySQL-HeatWave-운영-정책.mov").exists()
            )
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                [
                    "2026-02-12_MySQL-HeatWave-운영-정책.md",
                    "2026-02-12_MySQL-HeatWave-운영-정책.mov",
                ],
            )


if __name__ == "__main__":
    unittest.main()
