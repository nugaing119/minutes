from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.apply_content_repair_patch import (
    MAX_PATCH_BYTES,
    PATCH_NAME,
    apply_content_repair_patch,
)


class ContentRepairPatchTests(unittest.TestCase):
    def _write_json(self, path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_applies_bounded_json_updates_and_exact_markdown_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            audit_path = job / "content_audit.json"
            minutes_path = job / "minutes.md"
            self._write_json(
                audit_path,
                {
                    "schema_version": 1,
                    "status": "passed",
                    "covered_item_ids": ["I-1"],
                },
            )
            minutes_path.write_text("## 일정\n\n기존 문장\n", encoding="utf-8")
            self._write_json(
                job / PATCH_NAME,
                {
                    "schema_version": 1,
                    "json_updates": [
                        {
                            "file": "content_audit.json",
                            "path": ["documented_conflict_ids"],
                            "value": [],
                        },
                        {
                            "file": "content_audit.json",
                            "path": ["recording_fidelity"],
                            "value": {
                                "preserved_item_ids": ["I-1"],
                                "rewritten_by_external_source_item_ids": [],
                            },
                        },
                    ],
                    "markdown_replacements": [
                        {
                            "old": "기존 문장",
                            "new": "기존 문장\n\n- 담당: 김 대리",
                            "expected_count": 1,
                        }
                    ],
                },
            )

            result = apply_content_repair_patch(job)

            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["documented_conflict_ids"], [])
            self.assertEqual(
                audit["recording_fidelity"]["preserved_item_ids"],
                ["I-1"],
            )
            self.assertIn("- 담당: 김 대리", minutes_path.read_text(encoding="utf-8"))
            self.assertFalse((job / PATCH_NAME).exists())
            self.assertEqual(result["json_updates"], 2)
            self.assertEqual(result["markdown_replacements"], 1)

    def test_rejects_unallowlisted_artifact_without_modifying_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            evidence_path = job / "evidence_chunks.json"
            self._write_json(evidence_path, {"secret": "unchanged"})
            before = evidence_path.read_bytes()
            self._write_json(
                job / PATCH_NAME,
                {
                    "schema_version": 1,
                    "json_updates": [
                        {
                            "file": "evidence_chunks.json",
                            "path": ["secret"],
                            "value": "changed",
                        }
                    ],
                    "markdown_replacements": [],
                },
            )

            with self.assertRaisesRegex(ValueError, "not repairable"):
                apply_content_repair_patch(job)

            self.assertEqual(evidence_path.read_bytes(), before)

    def test_ambiguous_markdown_replacement_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            audit_path = job / "content_audit.json"
            minutes_path = job / "minutes.md"
            self._write_json(audit_path, {"schema_version": 1, "status": "passed"})
            minutes_path.write_text("repeat\nrepeat\n", encoding="utf-8")
            before_audit = audit_path.read_bytes()
            before_minutes = minutes_path.read_bytes()
            self._write_json(
                job / PATCH_NAME,
                {
                    "schema_version": 1,
                    "json_updates": [
                        {
                            "file": "content_audit.json",
                            "path": ["documented_conflict_ids"],
                            "value": [],
                        }
                    ],
                    "markdown_replacements": [
                        {"old": "repeat", "new": "once", "expected_count": 1}
                    ],
                },
            )

            with self.assertRaisesRegex(ValueError, "expected 1 occurrence"):
                apply_content_repair_patch(job)

            self.assertEqual(audit_path.read_bytes(), before_audit)
            self.assertEqual(minutes_path.read_bytes(), before_minutes)

    def test_rejects_validator_owned_quality_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            review_path = job / "content_quality_review.json"
            self._write_json(review_path, {"schema_version": 3, "bindings": {}})
            self._write_json(
                job / PATCH_NAME,
                {
                    "schema_version": 1,
                    "json_updates": [
                        {
                            "file": "content_quality_review.json",
                            "path": ["bindings"],
                            "value": {"forged": True},
                        }
                    ],
                    "markdown_replacements": [],
                },
            )

            with self.assertRaisesRegex(ValueError, "model-owned"):
                apply_content_repair_patch(job)

    def test_rejects_oversized_patch_before_reading_target_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            (job / PATCH_NAME).write_text(
                "{" + ("x" * MAX_PATCH_BYTES) + "}",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "exceeds"):
                apply_content_repair_patch(job)


if __name__ == "__main__":
    unittest.main()
