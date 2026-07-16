from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_speaker_defaults_use_evidence_only_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "HOME": temp_dir,
                "MINUTES_HOME": str(Path(temp_dir) / "minutes"),
                "RECORDINGS_INBOX": str(Path(temp_dir) / "inbox"),
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("scripts.config.get_secret", return_value=None) as get_secret,
            ):
                settings = load_settings()
                requested_secrets = [call.args[0] for call in get_secret.call_args_list]

        self.assertEqual(settings.speaker_attribution_mode, "evidence")
        self.assertFalse(settings.speaker_attribution_required)
        self.assertTrue(settings.speech_activity_validation_enabled)
        self.assertEqual(
            settings.vad_model_dir,
            Path(temp_dir)
            / "minutes"
            / "models"
            / "silero-vad-6.2.1",
        )
        self.assertEqual(
            settings.community1_approval_path,
            Path(temp_dir)
            / "minutes"
            / "governance"
            / "pyannote-community1-approval.json",
        )
        self.assertEqual(
            settings.community1_model_dir,
            Path(temp_dir) / "minutes" / "models" / "pyannote-community1",
        )
        self.assertEqual(settings.process_qos, "utility")
        self.assertEqual(settings.process_nice, 10)
        self.assertTrue(settings.cleanup_job_media_after_archive)
        self.assertEqual(settings.completed_job_retention_hours, 0)
        self.assertEqual(settings.output_language, "auto")
        self.assertEqual(settings.content_audit_mode, "off")
        self.assertEqual(settings.official_source_verification, "off")
        self.assertEqual(settings.ocr_languages, "auto")
        self.assertEqual(settings.ocr_frame_interval_seconds, 5)
        self.assertEqual(settings.ocr_ffmpeg_threads, 2)
        self.assertEqual(settings.ocr_workers, 3)
        self.assertEqual(settings.ocr_prestart_cooldown_seconds, 20.0)
        self.assertEqual(settings.ocr_max_snapshot_gap_seconds, 120)
        self.assertEqual(settings.ocr_visual_only_min_mean_delta, 12.0)
        self.assertEqual(settings.ocr_frame_extract_cpu_limit_percent, 0)
        self.assertEqual(settings.ocr_tesseract_nice, 0)
        self.assertFalse(settings.cleanup_job_ocr_images_after_archive)
        self.assertFalse(hasattr(settings, "huggingface_token"))
        self.assertNotIn("HF_TOKEN", requested_secrets)

    def test_invalid_speaker_mode_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"SPEAKER_ATTRIBUTION_MODE": "invalid"}, clear=True),
            patch("scripts.config.get_secret", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "SPEAKER_ATTRIBUTION_MODE"):
                load_settings()

    def test_legacy_local_audio_speaker_modes_are_rejected(self) -> None:
        for mode in ("audio", "hybrid"):
            with (
                self.subTest(mode=mode),
                patch.dict(
                    os.environ,
                    {"SPEAKER_ATTRIBUTION_MODE": mode},
                    clear=True,
                ),
                patch("scripts.config.get_secret", return_value=None),
            ):
                with self.assertRaisesRegex(ValueError, "SPEAKER_ATTRIBUTION_MODE"):
                    load_settings()

    def test_required_speaker_attribution_rejects_forced_identity(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SPEAKER_ATTRIBUTION_MODE": "evidence",
                    "SPEAKER_ATTRIBUTION_REQUIRED": "true",
                },
                clear=True,
            ),
            patch("scripts.config.get_secret", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "uncertain speakers must remain unknown"):
                load_settings()

    def test_invalid_output_language_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"OUTPUT_LANGUAGE": "fr"}, clear=True),
            patch("scripts.config.get_secret", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "OUTPUT_LANGUAGE"):
                load_settings()

    def test_strict_content_audit_requires_codex(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "openai",
                    "CONTENT_AUDIT_MODE": "strict",
                },
                clear=True,
            ),
            patch("scripts.config.get_secret", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "LLM_PROVIDER=codex"):
                load_settings()

    def test_official_verification_requires_content_audit(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "codex",
                    "OFFICIAL_SOURCE_VERIFICATION": "required",
                },
                clear=True,
            ),
            patch("scripts.config.get_secret", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "CONTENT_AUDIT_MODE"):
                load_settings()

    def test_strict_codex_official_verification_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.dict(
                    os.environ,
                    {
                        "HOME": temp_dir,
                        "LLM_PROVIDER": "codex",
                        "CONTENT_AUDIT_MODE": "strict",
                        "OFFICIAL_SOURCE_VERIFICATION": "required",
                    },
                    clear=True,
                ),
                patch("scripts.config.get_secret", return_value=None),
            ):
                settings = load_settings()

        self.assertEqual(settings.content_audit_mode, "strict")
        self.assertEqual(settings.official_source_verification, "required")

    def test_invalid_resource_limits_are_rejected(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "PROCESS_NICE": "21",
                },
                clear=True,
            ),
            patch("scripts.config.get_secret", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "PROCESS_NICE"):
                load_settings()

    def test_negative_completed_job_retention_is_rejected(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"COMPLETED_JOB_RETENTION_HOURS": "-1"},
                clear=True,
            ),
            patch("scripts.config.get_secret", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "COMPLETED_JOB_RETENTION_HOURS"):
                load_settings()

    def test_ocr_workers_are_configurable_and_bounded(self) -> None:
        with (
            patch.dict(os.environ, {"OCR_WORKERS": "5"}, clear=True),
            patch("scripts.config.get_secret", return_value=None),
        ):
            self.assertEqual(load_settings().ocr_workers, 5)

        for value in ("0", "17"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"OCR_WORKERS": value}, clear=True),
                patch("scripts.config.get_secret", return_value=None),
            ):
                with self.assertRaisesRegex(ValueError, "OCR_WORKERS"):
                    load_settings()

    def test_ocr_ffmpeg_threads_and_cooldown_are_bounded(self) -> None:
        for env, expected_error in (
            ({"OCR_FFMPEG_THREADS": "0"}, "OCR_FFMPEG_THREADS"),
            ({"OCR_FFMPEG_THREADS": "17"}, "OCR_FFMPEG_THREADS"),
            ({"OCR_PRESTART_COOLDOWN_SECONDS": "-0.1"}, "OCR_PRESTART_COOLDOWN_SECONDS"),
        ):
            with (
                self.subTest(env=env),
                patch.dict(os.environ, env, clear=True),
                patch("scripts.config.get_secret", return_value=None),
            ):
                with self.assertRaisesRegex(ValueError, expected_error):
                    load_settings()


if __name__ == "__main__":
    unittest.main()
