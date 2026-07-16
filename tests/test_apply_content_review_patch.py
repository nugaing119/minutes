from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.apply_content_review_patch import apply_content_review_patch
from scripts.utils import write_json


class ApplyContentReviewPatchTests(unittest.TestCase):
    def _job(self, root: Path) -> Path:
        job = root / "job"
        job.mkdir()
        write_json(
            job / "content_audit.json",
            {"coverage": [{"item_id": "I01", "document_refs": ["old audit ref"]}]},
        )
        write_json(
            job / "content_quality_review.json",
            {
                "bindings": {"minutes_sha256": "a" * 64},
                "document_signals": {"minutes_bytes": 123},
                "review_cycles": [{"cycle": 1, "status": "passed"}],
                "required_item_checks": [
                    {
                        "item_id": "I01",
                        "dimensions": {
                            "core_facts": {
                                "status": "covered",
                                "document_refs": ["old review ref"],
                            },
                            "conditions_exceptions": {
                                "status": "not_applicable",
                                "rationale": "none stated",
                            },
                            "risks_limitations": {
                                "status": "not_applicable",
                                "rationale": "none stated",
                            },
                            "impact": {
                                "status": "not_applicable",
                                "rationale": "none stated",
                            },
                            "actions_decisions": {
                                "status": "not_applicable",
                                "rationale": "none stated",
                            },
                        },
                    }
                ],
            },
        )
        return job

    def test_applies_only_named_refs_and_preserves_validator_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            write_json(
                job / "content_review_patch.json",
                {
                    "schema_version": 1,
                    "audit_coverage_updates": [
                        {"item_id": "I01", "document_refs": ["new audit ref"]}
                    ],
                    "review_dimension_updates": [
                        {
                            "item_id": "I01",
                            "dimension": "core_facts",
                            "status": "covered",
                            "document_refs": ["new review ref"],
                        }
                    ],
                },
            )

            result = apply_content_review_patch(job)
            audit = json.loads((job / "content_audit.json").read_text())
            review = json.loads((job / "content_quality_review.json").read_text())

        self.assertEqual(result["audit_coverage_updates"], 1)
        self.assertEqual(audit["coverage"][0]["document_refs"], ["new audit ref"])
        self.assertEqual(
            review["required_item_checks"][0]["dimensions"]["core_facts"][
                "document_refs"
            ],
            ["new review ref"],
        )
        self.assertEqual(review["bindings"]["minutes_sha256"], "a" * 64)
        self.assertEqual(review["review_cycles"], [{"cycle": 1, "status": "passed"}])
        self.assertFalse((job / "content_review_patch.json").exists())

    def test_rejects_unknown_item_without_mutating_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            original = (job / "content_audit.json").read_bytes()
            write_json(
                job / "content_review_patch.json",
                {
                    "schema_version": 1,
                    "audit_coverage_updates": [
                        {"item_id": "missing", "document_refs": ["new ref"]}
                    ],
                },
            )

            with self.assertRaisesRegex(ValueError, "exactly one missing"):
                apply_content_review_patch(job)

            current = (job / "content_audit.json").read_bytes()

        self.assertEqual(current, original)


if __name__ == "__main__":
    unittest.main()
