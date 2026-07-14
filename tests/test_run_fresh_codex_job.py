from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_fresh_codex_job import (
    FRESH_CONTEXT_ENV,
    build_fresh_codex_command,
    build_fresh_prompt,
    collect_evidence_manifest,
    ensure_not_nested,
    job_policy,
    normalize_request_overrides,
    validate_job_dir,
    verify_completed_job,
)


class FreshCodexJobTests(unittest.TestCase):
    def test_prompt_passes_paths_and_policy_without_copying_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "minutes" / "jobs" / "job-1"
            job.mkdir(parents=True)
            sentinel = "PRIVATE_TRANSCRIPT_SENTINEL"
            (job / "codex_minutes_input.md").write_text(
                f"full evidence: {sentinel}\n",
                encoding="utf-8",
            )

            prompt = build_fresh_prompt(
                root / "repo",
                job,
                policy={
                    "output_language": "ko",
                    "detected_language": "en",
                    "content_audit_mode": "strict",
                    "official_source_verification": "auto",
                },
                request_overrides=("CPU와 총 소요시간을 보고",),
            )

        self.assertIn("$minutes fresh-context worker", prompt)
        self.assertIn(str(job / "codex_minutes_input.md"), prompt)
        self.assertIn("OUTPUT_LANGUAGE=ko", prompt)
        self.assertIn("CONTENT_AUDIT_MODE=strict", prompt)
        self.assertIn("CPU와 총 소요시간을 보고", prompt)
        self.assertNotIn(sentinel, prompt)
        self.assertLess(len(prompt.encode("utf-8")), 5_000)

    def test_evidence_manifest_hashes_full_input_and_selected_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            input_bytes = b"complete input"
            snapshot_bytes = b"jpeg bytes"
            (job / "codex_minutes_input.md").write_bytes(input_bytes)
            snapshots = job / "snapshots"
            snapshots.mkdir()
            (snapshots / "snapshot_0001.jpg").write_bytes(snapshot_bytes)

            manifest = collect_evidence_manifest(job)

        self.assertEqual(manifest["snapshot_count"], 1)
        self.assertEqual(manifest["total_bytes"], len(input_bytes) + len(snapshot_bytes))
        self.assertEqual(
            manifest["files"][0]["sha256"],
            hashlib.sha256(input_bytes).hexdigest(),
        )
        self.assertEqual(
            manifest["snapshots"][0]["sha256"],
            hashlib.sha256(snapshot_bytes).hexdigest(),
        )

    def test_command_uses_a_new_ephemeral_session_and_stdin_prompt(self) -> None:
        command = build_fresh_codex_command(
            "/usr/local/bin/codex",
            Path("/tmp/repo"),
            Path("/tmp/minutes"),
            Path("/tmp/minutes/jobs/job/final.txt"),
        )

        self.assertEqual(command[:2], ["/usr/local/bin/codex", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("workspace-write", command)
        self.assertIn("--add-dir", command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn("resume", command)

    def test_job_validation_rejects_non_job_paths_and_non_awaiting_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs = root / "jobs"
            job = jobs / "job-1"
            job.mkdir(parents=True)
            (job / "codex_minutes_input.md").write_text("input", encoding="utf-8")
            (job / "status.json").write_text(
                json.dumps({"status": "awaiting_codex"}),
                encoding="utf-8",
            )

            self.assertEqual(validate_job_dir(job, jobs), job.resolve())
            with self.assertRaisesRegex(ValueError, "direct child"):
                validate_job_dir(root, jobs)

            (job / "status.json").write_text(
                json.dumps({"status": "completed"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "awaiting_codex"):
                validate_job_dir(job, jobs)

    def test_nested_fresh_worker_is_rejected(self) -> None:
        ensure_not_nested({})
        with self.assertRaisesRegex(RuntimeError, "nested"):
            ensure_not_nested({FRESH_CONTEXT_ENV: "1"})

    def test_request_overrides_are_short_single_line_instructions(self) -> None:
        self.assertEqual(
            normalize_request_overrides(("  report timing  ", "")),
            ["report timing"],
        )
        with self.assertRaisesRegex(ValueError, "one short line"):
            normalize_request_overrides(("first line\nraw transcript",))
        with self.assertRaisesRegex(ValueError, "at most 500"):
            normalize_request_overrides(("x" * 501,))
        with self.assertRaisesRegex(ValueError, "at most 4"):
            normalize_request_overrides(("a", "b", "c", "d", "e"))

    def test_policy_comes_from_prepared_job_status(self) -> None:
        policy = job_policy(
            {
                "codex_handoff": {
                    "output_language": "en",
                    "detected_language": "ko",
                },
                "content_audit": {
                    "mode": "strict",
                    "official_source_verification": "auto",
                },
            }
        )

        self.assertEqual(
            policy,
            {
                "output_language": "en",
                "detected_language": "ko",
                "content_audit_mode": "strict",
                "official_source_verification": "auto",
            },
        )

    def test_completion_requires_real_archived_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "job"
            output = root / "output"
            job.mkdir()
            output.mkdir()
            source = output / "video.mov"
            minutes = output / "video.md"
            docx = output / "video.docx"
            snapshots = output / "snapshots"
            snapshots.mkdir()
            source.write_bytes(b"video")
            minutes.write_text("# title\n", encoding="utf-8")
            docx.write_bytes(b"docx")
            (snapshots / "snapshot_0001.jpg").write_bytes(b"jpg")
            (job / "status.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "output_dir": str(output),
                        "codex_handoff": {
                            "docx_enabled": True,
                            "selected_snapshot_count": 1,
                        },
                        "files": {
                            "source": str(source),
                            "minutes": str(minutes),
                            "docx": str(docx),
                            "snapshots": str(snapshots),
                        },
                    }
                ),
                encoding="utf-8",
            )

            status = verify_completed_job(job)
            self.assertEqual(status["status"], "completed")
            minutes.unlink()
            with self.assertRaisesRegex(RuntimeError, "missing minutes"):
                verify_completed_job(job)


if __name__ == "__main__":
    unittest.main()
