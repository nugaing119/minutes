from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.docx_qa import create_docx_qa
from scripts.docx_report import generate_docx_report

from scripts.run_fresh_codex_job import (
    CODEX_STALL_EXIT_CODE,
    CodexStreamSummary,
    DEFAULT_REASONING_EFFORT,
    DRY_RUN_CONSOLE_BUDGET_BYTES,
    FRESH_CONTEXT_ENV,
    TOOL_OUTPUT_BUDGET_EXIT_CODE,
    WORKER_DOCUMENTS_PLUGIN_CONFIG,
    aggregate_phase_records,
    build_content_repair_prompt,
    build_delivery_prompt,
    build_dry_run_summary,
    build_fresh_codex_command,
    build_fresh_prompt,
    build_translation_prompt,
    collect_evidence_manifest,
    configured_codex_runtime,
    consume_codex_event_line,
    ensure_not_nested,
    job_policy,
    normalize_request_overrides,
    inspect_content_generation_checkpoint,
    reset_content_generation,
    run_codex_json_stream,
    select_content_action,
    validate_job_dir,
    verify_completed_job,
    write_worker_evidence_chunks,
    write_worker_evidence_summary,
    write_worker_runtime_summary,
    write_content_generation_checkpoint,
)


class FreshCodexJobTests(unittest.TestCase):
    def test_dry_run_summary_omits_prompt_and_evidence_bodies(self) -> None:
        manifest = {
            "schema_version": 7,
            "state": "dry_run",
            "job_dir": "/tmp/minutes/jobs/job-1",
            "policy": {"output_language": "ko"},
            "request_overrides": ["PRIVATE REQUEST"],
            "ephemeral_session": True,
            "planned_ephemeral_session_count": 2,
            "planned_phases": ["content", "delivery"],
            "phase_isolation": {"content_reads_raw_evidence": True},
            "worker_contract": {"mode": "preloaded_compact"},
            "parent_conversation_inherited": False,
            "raw_evidence_embedded_in_handoff": False,
            "handoff_prompt_bytes": {"content": 7_000, "delivery": 6_000},
            "handoff_prompt_sha256": {"content": "a" * 64, "delivery": "b" * 64},
            "evidence": {
                "files": [
                    {
                        "path": "/private/raw/transcript.txt",
                        "bytes": 123,
                        "sha256": "c" * 64,
                    }
                ],
                "snapshot_count": 10,
                "raw_frame_count": 50,
                "total_bytes": 999,
            },
            "runtime": {"model": "gpt-test", "reasoning_effort": "high"},
            "translation_runtime": None,
            "commands": {"content": ["codex", "exec", "<prompt-via-stdin>"]},
        }

        summary = build_dry_run_summary(manifest)
        rendered = json.dumps(summary, ensure_ascii=False, indent=2)

        self.assertNotIn("PRIVATE REQUEST", rendered)
        self.assertNotIn("/private/raw/transcript.txt", rendered)
        self.assertNotIn("evidence", summary.get("omitted_from_console", [])[:3])
        self.assertEqual(summary["request_override_count"], 1)
        self.assertEqual(summary["evidence_summary"]["file_count"], 1)
        self.assertLessEqual(
            len(rendered.encode("utf-8")),
            DRY_RUN_CONSOLE_BUDGET_BYTES,
        )

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

        self.assertIn("preloaded compact content contract", prompt)
        self.assertIn(str(job / "codex_minutes_input.md"), prompt)
        self.assertIn("FINAL_OUTPUT_LANGUAGE=ko", prompt)
        self.assertIn("CONTENT_OUTPUT_LANGUAGE=auto", prompt)
        self.assertIn("TRANSLATION_REQUIRED=true", prompt)
        self.assertIn("Do not translate", prompt)
        self.assertIn("CONTENT_AUDIT_MODE=strict", prompt)
        self.assertIn("CPU와 총 소요시간을 보고", prompt)
        self.assertIn(str(job / "evidence_coverage_summary.json"), prompt)
        self.assertIn(str(job / "evidence_chunks.json"), prompt)
        self.assertIn(str(job / "worker_runtime_summary.json"), prompt)
        self.assertIn("Do not read codex_minutes_input.md directly", prompt)
        self.assertIn("exactly once in manifest order", prompt)
        self.assertIn("Do not read or print evidence_coverage.json", prompt)
        self.assertIn("Do not read or print fresh_codex_handoff.json", prompt)
        self.assertIn("Do not recursively list the job directory", prompt)
        self.assertIn("Do not open SKILL.md", prompt)
        self.assertIn("Do not open quality-loop.md", prompt)
        self.assertIn("reader-facing document blueprint", prompt)
        self.assertIn("Items Requiring Further Verification", prompt)
        self.assertIn("External Evidence Check", prompt)
        self.assertIn("final two H2s", prompt)
        self.assertIn("presenter estimate", prompt)
        self.assertIn("ledger/inventory → blueprint", prompt)
        self.assertIn("Structural validity alone is not a quality pass", prompt)
        self.assertIn("required_item_checks", prompt)
        self.assertIn("never `checks`", prompt)
        self.assertIn("revised cycle owns nonempty findings, changes, and target_section_ids", prompt)
        self.assertIn("Do not expose raw STT/OCR/Snapshot refs anywhere", prompt)
        self.assertIn("writing_style=meeting_minutes_objective", prompt)
        self.assertIn("~하기로 함", prompt)
        self.assertIn("writing_style=content_adaptive", prompt)
        self.assertIn("Front matter is reader metadata, not a production log", prompt)
        self.assertIn("render attempts, or QA mechanics", prompt)
        self.assertIn("source_list needs a real Markdown link", prompt)
        self.assertIn("exact substring of that primary H2", prompt)
        self.assertIn("If freeze fails, stop and return only its bounded error", prompt)
        self.assertIn("target_section_ids", prompt)
        self.assertIn("LOW_INFORMATION_DENSITY", prompt)
        self.assertIn("content_density_baseline.json", prompt)
        self.assertIn("validator-selected substantive", prompt)
        self.assertIn("Do not patch review_cycles", prompt)
        self.assertIn("content_freeze writes deterministic revised/pass cycles", prompt)
        self.assertIn("one isolated sidecar-only repair", prompt)
        self.assertIn("3-5", prompt)
        self.assertIn("adjacent full-width", prompt)
        self.assertIn("content_freeze.json", prompt)
        self.assertIn("schema_version=3", prompt)
        self.assertIn('"documented_conflict_ids":[]', prompt)
        self.assertIn('"recording_fidelity":{"preserved_item_ids":', prompt)
        self.assertIn('"intentional_omissions":[]', prompt)
        self.assertIn('"final_checks":{"overcompression":', prompt)
        self.assertIn('"purpose":"transcription_disambiguation"', prompt)
        self.assertIn("source_conflict_resolution=>video_conflict", prompt)
        self.assertIn("timeline requires a Markdown table", prompt)
        self.assertIn("definition_list requires >=2 labeled `label: value` lines", prompt)
        self.assertIn("Do not create, edit, render, or inspect a DOCX", prompt)
        self.assertIn("production media job", prompt)
        self.assertIn("Do not run repository-wide", prompt)
        self.assertIn("unittest discover", prompt)
        self.assertIn("Do not inspect validator implementations or tests", prompt)
        self.assertIn("Do not reread", prompt)
        self.assertNotIn("$minutes", prompt)
        self.assertNotIn(sentinel, prompt)
        self.assertLess(len(prompt.encode("utf-8")), 12_000)

    def test_content_repair_prompt_is_sidecar_only_and_patch_driven(self) -> None:
        prompt = build_content_repair_prompt(
            Path("/tmp/repo"),
            Path("/tmp/minutes/jobs/job"),
            validation_error="content audit failed: final_checks must be an object",
        )

        self.assertIn("content audit failed: final_checks must be an object", prompt)
        self.assertIn("content_repair_patch.json", prompt)
        self.assertIn("apply_content_repair_patch.py", prompt)
        self.assertIn("Do not read raw evidence", prompt)
        self.assertIn("Do not direct-edit minutes.md or any JSON sidecar", prompt)
        self.assertIn("Run the patch helper exactly once", prompt)
        self.assertIn("Run content_freeze.py exactly once after the helper", prompt)
        self.assertLess(len(prompt.encode("utf-8")), 9_000)
        worst_case_prompt = build_content_repair_prompt(
            Path("/tmp/repo"),
            Path("/tmp/minutes/jobs/job"),
            validation_error="x" * 20_000,
        )
        self.assertLess(len(worst_case_prompt.encode("utf-8")), 11_000)

    def test_content_checkpoint_reuses_completed_turn_even_after_artifact_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            (job / "minutes.md").write_text("before\n", encoding="utf-8")
            prompt = "content prompt v1"
            write_content_generation_checkpoint(
                job,
                content_prompt=prompt,
                state="awaiting_repair",
                repair_attempts=0,
                validation_error="schema mismatch",
            )

            status = inspect_content_generation_checkpoint(
                job,
                content_prompt=prompt,
            )
            self.assertTrue(status["prompt_matches"])
            self.assertTrue(status["artifacts_match"])
            self.assertEqual(
                select_content_action(job, content_prompt=prompt),
                "run_repair",
            )

            (job / "minutes.md").write_text("changed\n", encoding="utf-8")
            status = inspect_content_generation_checkpoint(
                job,
                content_prompt=prompt,
            )
            self.assertFalse(status["artifacts_match"])
            self.assertEqual(
                select_content_action(job, content_prompt=prompt),
                "run_repair",
            )

    def test_content_checkpoint_never_auto_repeats_after_repair_is_spent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            prompt = "content prompt v1"
            write_content_generation_checkpoint(
                job,
                content_prompt=prompt,
                state="repair_failed",
                repair_attempts=1,
                validation_error="still invalid",
            )

            self.assertEqual(
                select_content_action(job, content_prompt=prompt),
                "repair_exhausted",
            )
            self.assertEqual(
                select_content_action(
                    job,
                    content_prompt=prompt,
                    force_content_rebuild=True,
                ),
                "run_content",
            )
            self.assertEqual(
                select_content_action(job, content_prompt="content prompt v2"),
                "checkpoint_mismatch",
            )

    def test_force_rebuild_reset_removes_only_generated_content_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            preserved = {
                "evidence_chunks.json": "evidence",
                "transcript.json": "raw transcript",
                "screen_text.json": "raw OCR",
                "source.mov": "media",
            }
            removed = {
                "minutes.md": "generated",
                "content_audit.json": "{}",
                "content_generation_checkpoint.json": "{}",
                "content_repair_patch.json": "{}",
                "minutes.translated.md": "translated",
                "translation_manifest.json": "{}",
            }
            for name, value in {**preserved, **removed}.items():
                (job / name).write_text(value, encoding="utf-8")

            reset_content_generation(job)

            for name in preserved:
                self.assertTrue((job / name).is_file(), name)
            for name in removed:
                self.assertFalse((job / name).exists(), name)

    def test_delivery_prompt_uses_frozen_content_and_retained_template(self) -> None:
        prompt = build_delivery_prompt(
            Path("/tmp/repo"),
            Path("/tmp/minutes/jobs/job"),
            policy={"output_language": "ko", "detected_language": "en"},
        )

        self.assertIn("preloaded compact delivery contract", prompt)
        self.assertIn("bundled retained Word", prompt)
        self.assertIn("Do not open any SKILL.md", prompt)
        self.assertIn("Do not recursively list the job directory", prompt)
        self.assertIn("frames, snapshots, or render directory", prompt)
        self.assertIn("content_freeze.json", prompt)
        self.assertIn("minutes.translated.md", prompt)
        self.assertIn("translation.py", prompt)
        self.assertIn("Never read codex_minutes_input.md", prompt)
        self.assertIn("frozen Markdown is immutable", prompt)
        self.assertIn("finalize_docx.py prepare", prompt)
        self.assertIn("Inspect every latest page PNG at 100% zoom", prompt)
        self.assertIn("NATURAL_FINAL_PAGE_WHITESPACE", prompt)
        self.assertIn("Never reflow or add filler", prompt)
        self.assertIn("no document or section character maximum", prompt)
        self.assertIn("console limit never limits", prompt)
        self.assertIn("fills its cover/TOC/body slots", prompt)
        self.assertIn("blocking defects", prompt)
        self.assertNotIn("SHORT_FINAL_PAGE", prompt)
        self.assertIn("--blocking-defect-code", prompt)
        self.assertIn("archive_job.py", prompt)
        self.assertNotIn("$documents", prompt)
        self.assertNotIn("$minutes", prompt)
        self.assertLess(len(prompt.encode("utf-8")), 8_000)

    def test_translation_prompt_is_one_pass_and_preserves_protected_literals(self) -> None:
        source = "# Demo\n\n- Output language: English\n\n`STT:00:00:01-00:00:02`\n"

        prompt = build_translation_prompt(source, target_language="ko")

        self.assertIn("natural professional Korean", prompt)
        self.assertIn("one translation pass", prompt)
        self.assertIn("no tool calls", prompt)
        self.assertIn("numeric values", prompt)
        self.assertIn(source.strip(), prompt)
        self.assertIn("legacy source already contains an Output language", prompt)
        self.assertIn("objective Korean report style", prompt)
        self.assertIn("Do not add source/output-language", prompt)
        self.assertIn("## 추가 검증이 필요한 항목", prompt)
        self.assertIn("## 외부 근거 확인", prompt)

    def test_phase_aggregation_keeps_content_and_delivery_costs_separable(self) -> None:
        aggregate = aggregate_phase_records(
            {
                "content": {
                    "state": "completed",
                    "elapsed_seconds": 10.0,
                    "token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 60,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 2,
                    },
                    "tool_count": 3,
                    "item_counts": {"command_execution": 3},
                    "context_efficiency": {
                        "tool_output_bytes": 1_000,
                        "max_tool_output_bytes": 800,
                        "oversized_tool_output_count": 0,
                    },
                },
                "delivery": {
                    "state": "completed",
                    "elapsed_seconds": 5.0,
                    "token_usage": {
                        "input_tokens": 20,
                        "cached_input_tokens": 10,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 1,
                    },
                    "tool_count": 2,
                    "item_counts": {"command_execution": 1, "file_change": 1},
                    "context_efficiency": {
                        "tool_output_bytes": 500,
                        "max_tool_output_bytes": 300,
                        "oversized_tool_output_count": 0,
                        "artifact_change_bytes": 2_000,
                        "max_artifact_change_bytes": 2_000,
                        "large_artifact_change_count": 0,
                    },
                },
            }
        )

        self.assertEqual(aggregate["active_codex_elapsed_seconds"], 15.0)
        self.assertEqual(aggregate["token_usage"]["input_tokens"], 120)
        self.assertEqual(aggregate["tool_count"], 5)
        self.assertEqual(
            aggregate["item_counts"],
            {"command_execution": 4, "file_change": 1},
        )
        self.assertEqual(
            aggregate["context_efficiency"]["artifact_change_bytes"],
            2_000,
        )

    def test_evidence_manifest_hashes_full_input_and_bounds_directory_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            input_bytes = b"complete input"
            snapshot_bytes = b"jpeg bytes"
            raw_frame_bytes = b"raw jpeg bytes"
            (job / "codex_minutes_input.md").write_bytes(input_bytes)
            snapshots = job / "snapshots"
            snapshots.mkdir()
            for index in range(1, 101):
                (snapshots / f"snapshot_{index:04}.jpg").write_bytes(
                    snapshot_bytes + str(index).encode("ascii")
                )
            frames = job / "frames"
            frames.mkdir()
            for index in range(1, 1_001):
                (frames / f"frame_{index:06}.jpg").write_bytes(
                    raw_frame_bytes + str(index).encode("ascii")
                )

            manifest = collect_evidence_manifest(job)

        self.assertEqual(manifest["snapshot_count"], 100)
        self.assertEqual(manifest["raw_frame_count"], 1_000)
        self.assertEqual(
            manifest["files"][0]["sha256"],
            hashlib.sha256(input_bytes).hexdigest(),
        )
        self.assertEqual(manifest["snapshots"]["count"], 100)
        self.assertEqual(manifest["raw_frames"]["count"], 1_000)
        self.assertIn("manifest_sha256", manifest["snapshots"])
        self.assertIn("manifest_sha256", manifest["raw_frames"])
        self.assertNotIn("files", manifest["snapshots"])
        self.assertNotIn("files", manifest["raw_frames"])
        self.assertLess(
            len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")),
            5_000,
        )

    def test_worker_evidence_summary_keeps_only_material_review_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            frames = [
                {
                    "evidence_id": f"frame-{index:06}",
                    "timestamp_seconds": index * 5,
                    "timestamp": f"00:{index // 12:02}:{(index % 12) * 5:02}",
                    "reason": "visual_duplicate",
                    "raw_frame": f"frames/frame_{index:06}.jpg",
                    "raw_frame_sha256": "a" * 64,
                }
                for index in range(1, 1_001)
            ]
            frames[119].update(
                {
                    "reason": "forced_coverage",
                    "snapshot": "snapshots/snapshot_0001.jpg",
                    "snapshot_evidence_id": "snapshot-0001",
                    "ocr_text_present": True,
                }
            )
            frames[479].update(
                {
                    "reason": "speaker_ui_change",
                    "snapshot": "snapshots/snapshot_0002.jpg",
                    "snapshot_evidence_id": "snapshot-0002",
                    "ocr_text_present": True,
                }
            )
            coverage = {
                "schema_version": 1,
                "status": "completed",
                "coverage_passed": True,
                "accounting_complete": True,
                "raw_frame_count": 1_000,
                "accounted_frame_count": 1_000,
                "selected_snapshot_count": 2,
                "max_snapshot_gap_limit_seconds": 120,
                "max_selected_snapshot_gap_seconds": 120,
                "reason_counts": {
                    "visual_duplicate": 998,
                    "forced_coverage": 1,
                    "speaker_ui_change": 1,
                },
                "frames": frames,
            }
            (job / "evidence_coverage.json").write_text(
                json.dumps(coverage),
                encoding="utf-8",
            )

            summary_path = write_worker_evidence_summary(job)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["review_frame_count"], 2)
        self.assertEqual(
            [item["reason"] for item in summary["review_frames"]],
            ["forced_coverage", "speaker_ui_change"],
        )
        self.assertNotIn("frames", summary)
        self.assertEqual(len(summary["source_coverage_sha256"]), 64)
        self.assertLess(len(json.dumps(summary).encode("utf-8")), 10_000)

    def test_worker_runtime_summary_omits_large_arrays_and_keeps_gate_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            (job / "status.json").write_text(
                json.dumps(
                    {
                        "status": "awaiting_codex",
                        "step": "awaiting_codex",
                        "recording_date": "2026-07-15",
                        "resource_policy": {"ocr_workers": 5},
                        "codex_handoff": {"content_audit_mode": "strict"},
                    }
                ),
                encoding="utf-8",
            )
            (job / "process_metrics.json").write_text(
                json.dumps(
                    {
                        "state": "completed",
                        "preprocessing_elapsed_seconds": 123.4,
                        "stages": [
                            {
                                "step": "ocr",
                                "wall_seconds": 10.0,
                                "cpu_seconds": 20.0,
                                "cpu_percent_of_one_core": 200.0,
                                "large_detail": "omit-me",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (job / "speaker_attribution_report.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "local_audio_diarization": "disabled_by_policy",
                        "speech_activity_validation": "completed",
                    }
                ),
                encoding="utf-8",
            )
            sentinel = "LARGE_ARRAY_SENTINEL"
            (job / "speech_activity.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "purpose": "speech_presence_validation_only",
                        "speaker_identity": False,
                        "speaker_diarization": False,
                        "transcript_modified": False,
                        "regions": [sentinel] * 10_000,
                        "suspect_segments": [sentinel] * 10_000,
                    }
                ),
                encoding="utf-8",
            )

            summary_path = write_worker_runtime_summary(job)
            summary_text = summary_path.read_text(encoding="utf-8")
            summary = json.loads(summary_text)

        self.assertNotIn(sentinel, summary_text)
        self.assertLess(len(summary_text.encode("utf-8")), 10_000)
        self.assertEqual(
            summary["speech_activity"]["fields"]["speaker_identity"],
            False,
        )
        self.assertEqual(
            summary["process_metrics"]["fields"]["stages"][0],
            {
                "step": "ocr",
                "wall_seconds": 10.0,
                "cpu_seconds": 20.0,
                "cpu_percent_of_one_core": 200.0,
            },
        )

    def test_worker_evidence_chunks_are_non_overlapping_and_reconstruct_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            source = "".join(f"line {index}\n" for index in range(1, 506))
            (job / "codex_minutes_input.md").write_text(source, encoding="utf-8")

            manifest_path = write_worker_evidence_chunks(job, lines_per_chunk=200)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            reconstructed = "".join(
                Path(item["path"]).read_text(encoding="utf-8")
                for item in manifest["chunks"]
            )

        self.assertEqual(manifest["chunk_count"], 3)
        self.assertEqual(manifest["quality_contract_version"], 3)
        self.assertEqual(
            [
                (item["start_line"], item["end_line"])
                for item in manifest["chunks"]
            ],
            [(1, 200), (201, 400), (401, 505)],
        )
        self.assertEqual(
            [item["chunk_line_count"] for item in manifest["chunks"]],
            [200, 200, 105],
        )
        self.assertEqual(
            manifest["chunk_read_contract"],
            {
                "scope": "entire_chunk_file",
                "source_coordinate_fields": ["start_line", "end_line"],
                "max_commands_per_chunk": 1,
            },
        )
        self.assertEqual(reconstructed, source)
        self.assertEqual(
            manifest["source_sha256"],
            hashlib.sha256(source.encode("utf-8")).hexdigest(),
        )

    def test_default_worker_chunks_reduce_round_trips_without_losing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            source = "".join(f"line {index}\n" for index in range(1, 1_202))
            (job / "codex_minutes_input.md").write_text(source, encoding="utf-8")

            manifest_path = write_worker_evidence_chunks(job)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            reconstructed = "".join(
                Path(item["path"]).read_text(encoding="utf-8")
                for item in manifest["chunks"]
            )

        self.assertEqual(manifest["lines_per_chunk"], 500)
        self.assertEqual(manifest["max_bytes_per_chunk"], 15_000)
        self.assertEqual(manifest["chunk_count"], 3)
        self.assertEqual(reconstructed, source)

    def test_worker_chunks_apply_byte_cap_to_long_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir)
            source = "".join(f"{index:04}:" + "x" * 90 + "\n" for index in range(120))
            (job / "codex_minutes_input.md").write_text(source, encoding="utf-8")

            manifest_path = write_worker_evidence_chunks(
                job,
                lines_per_chunk=500,
                max_bytes_per_chunk=1_000,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            reconstructed = "".join(
                Path(item["path"]).read_text(encoding="utf-8")
                for item in manifest["chunks"]
            )

        self.assertEqual(reconstructed, source)
        self.assertGreater(manifest["chunk_count"], 1)
        self.assertLessEqual(manifest["max_chunk_bytes"], 1_000)
        self.assertEqual(manifest["oversized_chunk_count"], 0)

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
        self.assertIn("--json", command)
        self.assertIn(WORKER_DOCUMENTS_PLUGIN_CONFIG, command)
        self.assertEqual(DEFAULT_REASONING_EFFORT, "high")
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn("resume", command)

    def test_prompt_forbids_snapshot_mutation_without_using_count_as_coverage(self) -> None:
        prompt = build_fresh_prompt(
            Path("/tmp/repo"),
            Path("/tmp/minutes/jobs/job"),
            policy={},
        )

        self.assertIn("Do not delete, move, recreate, or hash raw frames/Snapshots", prompt)
        self.assertIn("validators own frame accounting", prompt)
        self.assertIn("verify all Markdown Snapshot refs resolve", prompt)
        self.assertIn("Do not treat raw Snapshot count as the evidence-coverage gate", prompt)

    def test_command_applies_explicit_runtime_overrides(self) -> None:
        command = build_fresh_codex_command(
            "/usr/local/bin/codex",
            Path("/tmp/repo"),
            Path("/tmp/minutes"),
            Path("/tmp/minutes/jobs/job/final.txt"),
            model="gpt-test",
            reasoning_effort="high",
        )

        self.assertIn("--model", command)
        self.assertIn("gpt-test", command)
        self.assertIn("model_reasoning_effort=\"high\"", command)
        self.assertEqual(command[-1], "-")

    def test_codex_runtime_metadata_reads_only_model_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            (codex_home / "config.toml").write_text(
                'model = "gpt-test"\n'
                'model_reasoning_effort = "high"\n'
                'api_key = "must-not-leak"\n',
                encoding="utf-8",
            )

            runtime = configured_codex_runtime({"CODEX_HOME": str(codex_home)})

        self.assertEqual(runtime["model"], "gpt-test")
        self.assertEqual(runtime["reasoning_effort"], "high")
        self.assertNotIn("api_key", runtime)

        overridden = configured_codex_runtime(
            {"CODEX_HOME": str(codex_home)},
            model_override="gpt-override",
            reasoning_effort_override="medium",
        )
        self.assertEqual(overridden["model"], "gpt-override")
        self.assertEqual(overridden["reasoning_effort"], "medium")

    def test_event_parser_keeps_large_items_out_of_console_and_tracks_usage(self) -> None:
        progress = io.StringIO()
        summary = CodexStreamSummary()
        large_diff = "+" + ("changed line\n" * 2_000)
        events = [
            {"type": "thread.started", "thread_id": "thread-123"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "tool-1",
                    "type": "file_change",
                    "changes": large_diff,
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "message-1",
                    "type": "agent_message",
                    "text": "done",
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 120,
                    "cached_input_tokens": 20,
                    "output_tokens": 30,
                    "reasoning_output_tokens": 10,
                },
            },
        ]
        for index, event in enumerate(events, start=1):
            consume_codex_event_line(
                json.dumps(event) + "\n",
                summary,
                elapsed_seconds=float(index),
                progress_stream=progress,
            )
        consume_codex_event_line(
            "not-json\n",
            summary,
            elapsed_seconds=6.0,
            progress_stream=progress,
        )

        fields = summary.manifest_fields()
        console = progress.getvalue()
        self.assertEqual(fields["thread_id"], "thread-123")
        self.assertEqual(fields["event_count"], 6)
        self.assertEqual(fields["malformed_event_lines"], 1)
        self.assertEqual(fields["tool_count"], 1)
        self.assertEqual(fields["item_counts"], {"agent_message": 1, "file_change": 1})
        self.assertEqual(fields["token_usage"]["input_tokens"], 120)
        self.assertEqual(
            fields["context_efficiency"]["oversized_tool_output_count"],
            0,
        )
        self.assertEqual(fields["context_efficiency"]["tool_output_bytes"], 0)
        self.assertEqual(
            fields["context_efficiency"]["artifact_change_bytes"],
            len(large_diff.encode("utf-8")),
        )
        self.assertEqual(
            fields["context_efficiency"]["large_artifact_change_count"],
            1,
        )
        self.assertIn("fresh Codex: started", console)
        self.assertIn("input=120", console)
        self.assertNotIn("changed line", console)

    def test_event_summary_records_model_visible_tool_output_pressure(self) -> None:
        progress = io.StringIO()
        summary = CodexStreamSummary()
        events = [
            {
                "type": "item.completed",
                "item": {
                    "id": "tool-1",
                    "type": "command_execution",
                    "aggregated_output": "x" * 25_000,
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1_000,
                    "cached_input_tokens": 900,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 10,
                },
            },
        ]
        for index, event in enumerate(events, start=1):
            consume_codex_event_line(
                json.dumps(event) + "\n",
                summary,
                elapsed_seconds=float(index),
                progress_stream=progress,
            )

        efficiency = summary.manifest_fields()["context_efficiency"]

        self.assertEqual(efficiency["uncached_input_tokens"], 100)
        self.assertEqual(efficiency["cached_input_ratio"], 0.9)
        self.assertEqual(efficiency["input_to_output_ratio"], 20.0)
        self.assertEqual(efficiency["tool_output_bytes"], 25_000)
        self.assertEqual(efficiency["max_tool_output_bytes"], 25_000)
        self.assertEqual(efficiency["oversized_tool_output_count"], 1)
        self.assertEqual(len(efficiency["tool_output_budget_violations"]), 1)
        self.assertEqual(
            efficiency["tool_output_budget_violations"][0]["output_bytes"],
            25_000,
        )

    def test_json_stream_stops_after_first_tool_output_budget_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "events.jsonl"
            stderr_path = root / "stderr.log"
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys, time",
                    "sys.stdin.read()",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'tool-1', 'type': 'command_execution', "
                    "'command': 'cat large-file', 'aggregated_output': 'x' * 24001}}), flush=True)",
                    "time.sleep(0.2)",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'message-1', 'type': 'agent_message', 'text': 'too late'}}), flush=True)",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded prompt",
                env={},
                events_path=events_path,
                stderr_path=stderr_path,
                progress_stream=progress,
            )

            event_log = events_path.read_text(encoding="utf-8")

        self.assertEqual(returncode, TOOL_OUTPUT_BUDGET_EXIT_CODE)
        self.assertEqual(summary.oversized_tool_output_count, 1)
        self.assertIn("cat large-file", progress.getvalue())
        self.assertNotIn("too late", event_log)

    def test_json_stream_stops_on_forbidden_worker_skill_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "events.jsonl"
            stderr_path = root / "stderr.log"
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys, time",
                    "sys.stdin.read()",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'tool-1', 'type': 'command_execution', "
                    "'command': 'cat /tmp/SKILL.md', 'aggregated_output': 'small'}}), flush=True)",
                    "time.sleep(0.2)",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'message-1', 'type': 'agent_message', 'text': 'too late'}}), flush=True)",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded prompt",
                env={},
                events_path=events_path,
                stderr_path=stderr_path,
                progress_stream=progress,
            )

            event_log = events_path.read_text(encoding="utf-8")

        self.assertEqual(returncode, TOOL_OUTPUT_BUDGET_EXIT_CODE)
        self.assertEqual(summary.forbidden_instruction_read_count, 1)
        self.assertIn("forbidden worker instruction read", progress.getvalue())
        self.assertNotIn("too late", event_log)

    def test_json_stream_stops_on_repair_phase_raw_evidence_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "events.jsonl"
            stderr_path = root / "stderr.log"
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys, time",
                    "sys.stdin.read()",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'tool-1', 'type': 'command_execution', "
                    "'command': 'sed -n 1,40p evidence_chunks/part-0001.md', "
                    "'aggregated_output': 'small'}}), flush=True)",
                    "time.sleep(0.2)",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'message-1', 'type': 'agent_message', 'text': 'too late'}}), flush=True)",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded repair prompt",
                env={},
                events_path=events_path,
                stderr_path=stderr_path,
                progress_stream=progress,
                forbidden_command_markers=("evidence_chunks/",),
            )

            event_log = events_path.read_text(encoding="utf-8")

        self.assertEqual(returncode, TOOL_OUTPUT_BUDGET_EXIT_CODE)
        self.assertEqual(summary.forbidden_instruction_read_count, 1)
        self.assertIn("forbidden worker instruction read", progress.getvalue())
        self.assertNotIn("too late", event_log)

    def test_json_stream_stops_on_repair_direct_sidecar_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "events.jsonl"
            stderr_path = root / "stderr.log"
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys, time",
                    "sys.stdin.read()",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'tool-1', 'type': 'file_change', 'changes': "
                    "[{'path': '/tmp/job/content_audit.json', 'kind': 'update'}]}}), flush=True)",
                    "time.sleep(0.2)",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'message-1', 'type': 'agent_message', 'text': 'too late'}}), flush=True)",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded repair prompt",
                env={},
                events_path=events_path,
                stderr_path=stderr_path,
                progress_stream=progress,
                allowed_file_change_names=("content_repair_patch.json",),
            )

            event_log = events_path.read_text(encoding="utf-8")

        self.assertEqual(returncode, TOOL_OUTPUT_BUDGET_EXIT_CODE)
        self.assertEqual(summary.forbidden_artifact_write_count, 1)
        self.assertIn("forbidden artifact write", progress.getvalue())
        self.assertNotIn("too late", event_log)

    def test_json_stream_allows_repair_patch_file_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys",
                    "sys.stdin.read()",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'tool-1', 'type': 'file_change', 'changes': "
                    "[{'path': '/tmp/job/content_repair_patch.json', 'kind': 'add'}]}}))",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded repair prompt",
                env={},
                events_path=root / "events.jsonl",
                stderr_path=root / "stderr.log",
                progress_stream=progress,
                allowed_file_change_names=("content_repair_patch.json",),
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(summary.forbidden_artifact_write_count, 0)

    def test_json_stream_stops_on_duplicate_evidence_chunk_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "events.jsonl"
            stderr_path = root / "stderr.log"
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys, time",
                    "sys.stdin.read()",
                    "for item_id in ('tool-1', 'tool-2'):",
                    "    print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': item_id, 'type': 'command_execution', "
                    "'command': 'sed -n 1,999p evidence_chunks/part-0002.md', "
                    "'aggregated_output': 'small'}}), flush=True)",
                    "    time.sleep(0.1)",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'message-1', 'type': 'agent_message', 'text': 'too late'}}), flush=True)",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded prompt",
                env={},
                events_path=events_path,
                stderr_path=stderr_path,
                progress_stream=progress,
            )

            event_log = events_path.read_text(encoding="utf-8")

        self.assertEqual(returncode, TOOL_OUTPUT_BUDGET_EXIT_CODE)
        self.assertEqual(summary.duplicate_evidence_chunk_read_count, 1)
        self.assertEqual(summary.context_efficiency_fields()["evidence_chunk_read_count"], 1)
        self.assertIn("duplicate evidence chunk read", progress.getvalue())
        self.assertNotIn("too late", event_log)

    def test_json_stream_terminates_a_silent_stalled_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "events.jsonl"
            stderr_path = root / "stderr.log"
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys, time",
                    "sys.stdin.read()",
                    "print(json.dumps({'type': 'thread.started', 'thread_id': 't-1'}), flush=True)",
                    "time.sleep(2)",
                    "print(json.dumps({'type': 'turn.completed', 'usage': {}}), flush=True)",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded prompt",
                env={},
                events_path=events_path,
                stderr_path=stderr_path,
                progress_stream=progress,
                heartbeat_seconds=0.05,
                stall_timeout_seconds=0.15,
                poll_interval_seconds=0.01,
            )

            event_log = events_path.read_text(encoding="utf-8")

        self.assertEqual(returncode, CODEX_STALL_EXIT_CODE)
        self.assertTrue(summary.stalled)
        self.assertGreaterEqual(summary.stall_warning_count, 1)
        self.assertIn("no JSON events", progress.getvalue())
        self.assertIn("stalled", progress.getvalue())
        self.assertNotIn("turn.completed", event_log)

    def test_json_stream_archives_full_events_but_emits_bounded_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "events.jsonl"
            stderr_path = root / "stderr.log"
            progress = io.StringIO()
            child_code = "\n".join(
                (
                    "import json, sys",
                    "sys.stdin.read()",
                    "print(json.dumps({'type': 'thread.started', 'thread_id': 't-1'}))",
                    "print(json.dumps({'type': 'item.completed', 'item': "
                    "{'id': 'tool-1', 'type': 'file_change', 'changes': 'DIFF-' + 'x' * 25000}}))",
                    "print(json.dumps({'type': 'turn.completed', 'usage': "
                    "{'input_tokens': 9, 'cached_input_tokens': 2, "
                    "'output_tokens': 3, 'reasoning_output_tokens': 1}}))",
                    "print('ordinary diagnostic', file=sys.stderr)",
                    "print('fatal: synthetic failure detail', file=sys.stderr)",
                )
            )

            returncode, summary = run_codex_json_stream(
                [sys.executable, "-c", child_code],
                prompt="bounded prompt",
                env={},
                events_path=events_path,
                stderr_path=stderr_path,
                progress_stream=progress,
            )

            event_log = events_path.read_text(encoding="utf-8")
            stderr_log = stderr_path.read_text(encoding="utf-8")

        console = progress.getvalue()
        self.assertEqual(returncode, 0)
        self.assertEqual(summary.usage["input_tokens"], 9)
        self.assertEqual(len(summary.tool_item_ids), 1)
        self.assertEqual(summary.oversized_tool_output_count, 0)
        self.assertEqual(summary.large_artifact_change_count, 1)
        self.assertIn("DIFF-", event_log)
        self.assertIn("ordinary diagnostic", stderr_log)
        self.assertIn("fatal: synthetic failure detail", stderr_log)
        self.assertIn("fresh Codex: started", console)
        self.assertIn("fresh Codex: turn completed", console)
        self.assertIn("fatal: synthetic failure detail", console)
        self.assertNotIn("ordinary diagnostic", console)
        self.assertNotIn("DIFF-", console)

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
                "content_output_language": "auto",
                "translation_required": True,
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
            docx_qa = job / "docx_qa.json"
            draft = job / "video.draft.docx"
            render_dir = job / "render"
            render_dir.mkdir()
            snapshots = output / "snapshots"
            snapshots.mkdir()
            source.write_bytes(b"video")
            minutes.write_text(
                "# title\n\nDocument type: brief\n\n## section\n\nbody\n",
                encoding="utf-8",
            )
            generate_docx_report(minutes, draft)
            shutil.copy2(draft, docx)
            (render_dir / "page-1.png").write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + struct.pack(">I", 13)
                + b"IHDR"
                + struct.pack(">II", 1275, 1650)
            )
            create_docx_qa(
                minutes,
                draft,
                docx,
                render_dir=render_dir,
                visual_status="passed",
                output_path=docx_qa,
            )
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
                            "docx_qa": str(docx_qa),
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
