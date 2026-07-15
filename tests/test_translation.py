from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.document_language import (
    content_output_language,
    translation_required,
)
from scripts.translation import (
    create_translation_manifest,
    resolve_final_markdown,
    validate_translation_manifest,
)


SOURCE_MARKDOWN = """# LiveStack Demo

Document type: Technical briefing

- Source language: English
- Output language: English

## Operating envelope

- [ ] Verify the **15 minute** startup and USD 750 estimate.

| Item | Recorded value |
|---|---|
| Runtime | `4 ECPUs` |

[Guide](https://example.com/guide)

![Architecture](snapshots/snapshot_0001_00-01-00.jpg)

Evidence: `STT:00:01:00-00:02:00`.
"""

TARGET_MARKDOWN = """# LiveStack 데모

문서 유형: 기술 브리핑

- 원문 언어: 영어
- 출력 언어: 한국어

## 운영 범위

- [ ] **15분** 시작 시간과 USD 750 추정치를 확인한다.

| 항목 | 녹화된 값 |
|---|---|
| 런타임 | `4 ECPUs` |

[가이드](https://example.com/guide)

![아키텍처](snapshots/snapshot_0001_00-01-00.jpg)

근거: `STT:00:01:00-00:02:00`.
"""


class DocumentLanguagePolicyTests(unittest.TestCase):
    def test_translation_is_only_required_for_known_different_languages(self) -> None:
        self.assertTrue(translation_required("ko", "en"))
        self.assertTrue(translation_required("en", "ko-KR"))
        self.assertFalse(translation_required("auto", "en"))
        self.assertFalse(translation_required("ko", "ko"))
        self.assertFalse(translation_required("ko", "unknown"))

    def test_content_phase_keeps_the_source_language_before_translation(self) -> None:
        self.assertEqual(content_output_language("ko", "en"), "auto")
        self.assertEqual(content_output_language("ko", "ko"), "ko")
        self.assertEqual(content_output_language("auto", "en"), "auto")


class TranslationManifestTests(unittest.TestCase):
    def _job(self, root: Path) -> Path:
        job = root / "job"
        job.mkdir()
        (job / "minutes.md").write_text(SOURCE_MARKDOWN, encoding="utf-8")
        (job / "minutes.translated.md").write_text(
            TARGET_MARKDOWN,
            encoding="utf-8",
        )
        (job / "status.json").write_text(
            json.dumps(
                {
                    "codex_handoff": {
                        "output_language": "ko",
                        "detected_language": "en",
                    }
                }
            ),
            encoding="utf-8",
        )
        return job

    def test_manifest_accepts_one_pass_structure_preserving_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            with patch(
                "scripts.translation.validate_content_freeze",
                return_value={"content_sha256": "source-freeze"},
            ):
                manifest = create_translation_manifest(job)
                verified = validate_translation_manifest(job)
                final_path = resolve_final_markdown(job)

        self.assertEqual(manifest["status"], "passed")
        self.assertEqual(verified["target_language"], "ko")
        self.assertEqual(final_path.name, "minutes.translated.md")
        self.assertEqual(manifest["checks"]["model_review_cycles"], 0)

    def test_missing_numeric_literal_is_rejected_without_model_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            target_path = job / "minutes.translated.md"
            target_path.write_text(
                TARGET_MARKDOWN.replace("USD 750", "USD 금액"),
                encoding="utf-8",
            )
            with patch(
                "scripts.translation.validate_content_freeze",
                return_value={"content_sha256": "source-freeze"},
            ):
                with self.assertRaisesRegex(ValueError, "numeric literals"):
                    create_translation_manifest(job)

    def test_translated_number_words_may_add_digits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            source_path = job / "minutes.md"
            target_path = job / "minutes.translated.md"
            source_path.write_text(
                SOURCE_MARKDOWN.replace("15 minute", "five-stage 15 minute"),
                encoding="utf-8",
            )
            target_path.write_text(
                TARGET_MARKDOWN.replace("15분", "5단계 15분"),
                encoding="utf-8",
            )
            with patch(
                "scripts.translation.validate_content_freeze",
                return_value={"content_sha256": "source-freeze"},
            ):
                manifest = create_translation_manifest(job)

        self.assertTrue(manifest["checks"]["protected_literals_preserved"])

    def test_target_language_metadata_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            target_path = job / "minutes.translated.md"
            target_path.write_text(
                TARGET_MARKDOWN.replace("출력 언어: 한국어", "출력 언어: 영어"),
                encoding="utf-8",
            )
            with patch(
                "scripts.translation.validate_content_freeze",
                return_value={"content_sha256": "source-freeze"},
            ):
                with self.assertRaisesRegex(ValueError, "target-language metadata"):
                    create_translation_manifest(job)

    def test_target_change_invalidates_translation_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            with patch(
                "scripts.translation.validate_content_freeze",
                return_value={"content_sha256": "source-freeze"},
            ):
                create_translation_manifest(job)
                with (job / "minutes.translated.md").open(
                    "a",
                    encoding="utf-8",
                ) as handle:
                    handle.write("\n변경됨\n")
                with self.assertRaisesRegex(ValueError, "target hash"):
                    validate_translation_manifest(job)

    def test_same_language_job_uses_frozen_source_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            (job / "status.json").write_text(
                json.dumps(
                    {
                        "codex_handoff": {
                            "output_language": "en",
                            "detected_language": "en",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (job / "minutes.translated.md").unlink()
            self.assertEqual(
                resolve_final_markdown(job),
                (job / "minutes.md").resolve(),
            )


if __name__ == "__main__":
    unittest.main()
