from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.prepare_quality_rework import prepare_quality_rework
from scripts.utils import read_json, write_json


class PrepareQualityReworkTests(unittest.TestCase):
    def test_completed_job_is_cloned_without_old_document_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            source_job = jobs_dir / "completed-job"
            output_dir = Path(temp_dir) / "output"
            source_job.mkdir(parents=True)
            output_dir.mkdir()
            archived_source = output_dir / "recording.mov"
            archived_source.write_bytes(b"video")
            (source_job / "codex_minutes_input.md").write_text("evidence")
            (source_job / "transcript.txt").write_text("transcript")
            (source_job / "minutes.md").write_text("old draft")
            (source_job / "frames").mkdir()
            (source_job / "frames/frame.jpg").write_bytes(b"frame")
            (source_job / "snapshots").mkdir()
            (source_job / "snapshots/snapshot.jpg").write_bytes(b"snapshot")
            write_json(
                source_job / "status.json",
                {
                    "status": "completed",
                    "recording_date": "2026-04-07",
                    "files": {"source": str(archived_source)},
                    "codex_handoff": {
                        "output_language": "ko",
                        "detected_language": "ko",
                        "selected_snapshot_count": 1,
                    },
                    "content_audit": {
                        "mode": "strict",
                        "official_source_verification": "auto",
                    },
                },
            )

            destination = prepare_quality_rework(source_job, jobs_dir=jobs_dir)
            status = read_json(destination / "status.json")
            self.assertEqual(status["status"], "awaiting_codex")
            self.assertEqual(status["step"], "quality_rework")
            self.assertEqual(status["content_audit"]["status"], "pending")
            self.assertNotIn("inventory_items", status["content_audit"])
            self.assertTrue((destination / "source.mov").is_file())
            self.assertTrue((destination / "frames/frame.jpg").is_file())
            self.assertTrue((destination / "snapshots/snapshot.jpg").is_file())
            self.assertFalse((destination / "minutes.md").exists())
            self.assertTrue((destination / "rework_provenance.json").is_file())

    def test_non_completed_job_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            source_job = jobs_dir / "pending-job"
            source_job.mkdir(parents=True)
            write_json(source_job / "status.json", {"status": "awaiting_codex"})

            with self.assertRaisesRegex(ValueError, "must be completed"):
                prepare_quality_rework(source_job, jobs_dir=jobs_dir)

    def test_prepared_job_can_be_cloned_for_a_clean_retry_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            source_job = jobs_dir / "failed-content-job"
            source_job.mkdir(parents=True)
            source_media = source_job / "source.mov"
            source_media.write_bytes(b"video")
            (source_job / "codex_minutes_input.md").write_text("evidence")
            (source_job / "transcript.txt").write_text("transcript")
            (source_job / "minutes.md").write_text("failed draft")
            (source_job / "frames").mkdir()
            (source_job / "frames/frame.jpg").write_bytes(b"frame")
            (source_job / "snapshots").mkdir()
            (source_job / "snapshots/snapshot.jpg").write_bytes(b"snapshot")
            write_json(
                source_job / "status.json",
                {
                    "status": "awaiting_codex",
                    "step": "awaiting_codex",
                    "managed_source": str(source_media),
                    "codex_handoff": {
                        "output_language": "ko",
                        "detected_language": "ko",
                        "selected_snapshot_count": 1,
                    },
                    "content_audit": {
                        "mode": "strict",
                        "official_source_verification": "auto",
                    },
                },
            )

            destination = prepare_quality_rework(
                source_job,
                jobs_dir=jobs_dir,
                allow_prepared_retry=True,
            )
            status = read_json(destination / "status.json")
            provenance = read_json(destination / "rework_provenance.json")
            self.assertEqual(status["status"], "awaiting_codex")
            self.assertTrue((destination / "source.mov").is_file())
            self.assertFalse((destination / "minutes.md").exists())
            self.assertEqual(
                provenance["purpose"],
                "content_quality_retry_without_preprocessing",
            )

if __name__ == "__main__":
    unittest.main()
