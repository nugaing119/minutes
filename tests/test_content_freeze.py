from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.content_freeze import create_content_freeze, validate_content_freeze


PASSED_VALIDATION = {
    "mode": "strict",
    "status": "passed",
    "official_source_status": "not_required",
    "inventory_items": 3,
    "required_items": 2,
    "conflicts": 0,
    "issues": [],
}


class ContentFreezeTests(unittest.TestCase):
    def _job(self, root: Path) -> Path:
        job = root / "job"
        job.mkdir()
        (job / "minutes.md").write_text("# Frozen report\n\nBody\n", encoding="utf-8")
        (job / "content_audit.json").write_text("{}", encoding="utf-8")
        (job / "status.json").write_text(
            json.dumps(
                {
                    "status": "awaiting_codex",
                    "content_audit": {
                        "mode": "strict",
                        "official_source_verification": "off",
                    },
                }
            ),
            encoding="utf-8",
        )
        return job

    def test_freeze_binds_current_content_and_revalidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            with patch(
                "scripts.content_freeze.validate_content_artifacts",
                return_value=PASSED_VALIDATION,
            ) as validator:
                frozen = create_content_freeze(job)
                verified = validate_content_freeze(job)

        self.assertEqual(frozen["status"], "frozen")
        self.assertEqual(frozen["content_sha256"], verified["content_sha256"])
        self.assertEqual(validator.call_count, 2)

    def test_minutes_change_invalidates_freeze_before_docx_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            with patch(
                "scripts.content_freeze.validate_content_artifacts",
                return_value=PASSED_VALIDATION,
            ):
                create_content_freeze(job)
                (job / "minutes.md").write_text(
                    "# Frozen report\n\nChanged after review\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, "minutes.md"):
                    validate_content_freeze(job)


if __name__ == "__main__":
    unittest.main()
