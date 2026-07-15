from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.cleanup_completed_jobs import (
    cleanup_completed_job,
    cleanup_completed_jobs,
)
from scripts.utils import write_json


class CleanupCompletedJobsTests(unittest.TestCase):
    def _completed_job(
        self,
        root: Path,
        name: str,
        *,
        completed_at: datetime,
    ) -> tuple[Path, Path]:
        job_dir = root / "jobs" / name
        output_dir = root / "output" / name
        job_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        media = output_dir / "video.mov"
        minutes = output_dir / "minutes.md"
        media.write_bytes(b"source")
        minutes.write_text("# 분석\n", encoding="utf-8")
        (job_dir / "transcript.txt").write_text("전사\n", encoding="utf-8")
        write_json(
            job_dir / "status.json",
            {
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "output_dir": str(output_dir),
                "files": {
                    "video": str(media),
                    "minutes": str(minutes),
                },
            },
        )
        return job_dir, output_dir

    def test_expired_job_is_dry_run_by_default_then_entire_job_is_purged(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir, output_dir = self._completed_job(
                root,
                "expired",
                completed_at=now - timedelta(hours=25),
            )

            dry_run = cleanup_completed_job(
                job_dir,
                apply=False,
                retention_hours=24,
                now=now,
            )
            self.assertTrue(dry_run["eligible"])
            self.assertFalse(dry_run["purged"])
            self.assertTrue(job_dir.exists())

            applied = cleanup_completed_job(
                job_dir,
                apply=True,
                retention_hours=24,
                now=now,
            )

            self.assertTrue(applied["purged"])
            self.assertFalse(job_dir.exists())
            self.assertTrue((output_dir / "video.mov").exists())
            self.assertTrue((output_dir / "minutes.md").exists())

    def test_recent_completed_job_is_kept_for_rework(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir, _ = self._completed_job(
                root,
                "recent",
                completed_at=now - timedelta(hours=23),
            )

            result = cleanup_completed_job(
                job_dir,
                apply=True,
                retention_hours=24,
                now=now,
            )

            self.assertFalse(result["eligible"])
            self.assertEqual(result["reason"], "retention period is still active")
            self.assertTrue(job_dir.exists())

    def test_job_local_docx_qa_is_verified_without_shipping_it_to_output(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir, output_dir = self._completed_job(
                root,
                "docx-job",
                completed_at=now - timedelta(hours=25),
            )
            docx = output_dir / "minutes.docx"
            docx.write_bytes(b"docx")
            qa = job_dir / "docx_qa.json"
            qa.write_text("{}", encoding="utf-8")
            status_path = job_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["files"].update({"docx": str(docx), "docx_qa": str(qa)})
            write_json(status_path, status)

            result = cleanup_completed_job(
                job_dir,
                apply=False,
                retention_hours=24,
                now=now,
            )

            self.assertTrue(result["eligible"])
            self.assertFalse((output_dir / "docx_qa.json").exists())

    def test_failed_job_and_completed_job_with_missing_output_are_kept(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            failed = jobs_dir / "failed"
            failed.mkdir(parents=True)
            write_json(failed / "status.json", {"status": "failed"})
            broken, output_dir = self._completed_job(
                root,
                "broken",
                completed_at=now - timedelta(days=2),
            )
            (output_dir / "video.mov").unlink()
            (jobs_dir / "index.json").write_text("{}", encoding="utf-8")
            (jobs_dir / ".process.lock").touch()

            result = cleanup_completed_jobs(
                jobs_dir,
                apply=True,
                retention_hours=24,
                now=now,
            )

            self.assertEqual(result["purged_jobs"], 0)
            self.assertTrue(failed.exists())
            self.assertTrue(broken.exists())
            self.assertTrue((jobs_dir / "index.json").exists())
            self.assertTrue((jobs_dir / ".process.lock").exists())

    def test_selected_directory_outside_jobs_root_is_rejected(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "jobs").mkdir()
            outside, _ = self._completed_job(
                root / "outside-root",
                "completed",
                completed_at=now - timedelta(days=2),
            )

            result = cleanup_completed_jobs(
                root / "jobs",
                apply=True,
                retention_hours=24,
                selected_jobs=[outside],
                now=now,
            )

            self.assertEqual(result["purged_jobs"], 0)
            self.assertTrue(outside.exists())
            self.assertIn("outside", result["jobs"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
