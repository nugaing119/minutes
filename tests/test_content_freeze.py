from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.content_freeze import (
    MAX_CLI_ERROR_CHARS,
    _bounded_error,
    _normalize_official_checked_at,
    create_content_freeze,
    validate_content_freeze,
)


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

    def test_freeze_normalizes_a_same_day_future_official_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            official_path = job / "official_sources.json"
            official_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "checked_at": "2026-07-16T14:30:00+09:00",
                    }
                ),
                encoding="utf-8",
            )
            actual_now = datetime(
                2026,
                7,
                16,
                14,
                15,
                tzinfo=timezone(timedelta(hours=9)),
            )
            with patch("scripts.content_freeze.now_local", return_value=actual_now):
                normalized = _normalize_official_checked_at(job)

            official = json.loads(official_path.read_text(encoding="utf-8"))

        self.assertTrue(normalized)
        self.assertEqual(official["checked_at"], actual_now.isoformat())

    def test_cli_error_budget_preserves_a_material_repair_report(self) -> None:
        message = "; ".join(f"issue-{index}: details" for index in range(2_000))

        rendered = _bounded_error(message)

        self.assertGreater(MAX_CLI_ERROR_CHARS, 10_000)
        self.assertEqual(len(rendered), MAX_CLI_ERROR_CHARS)
        self.assertTrue(rendered.endswith("…"))


if __name__ == "__main__":
    unittest.main()
