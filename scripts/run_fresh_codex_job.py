#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.run_codex import resolve_configured_path
from scripts.content_quality import (
    BLUEPRINT_ARCHETYPES,
    BLUEPRINT_FORM_FACTORS,
    BLUEPRINT_ROLES,
    BLUEPRINT_WRITING_STYLES,
    LEDGER_CLASSIFICATIONS,
    MODEL_FINAL_CHECKS,
    QUALITY_CONTRACT_VERSION,
    REQUIRED_ITEM_DIMENSIONS,
    REQUIRED_FRONT_MATTER_KEYS,
)
from scripts.document_language import (
    content_output_language,
    language_policy_from_status,
    target_language_label,
    translation_required,
)
from scripts.translation import (
    MANIFEST_NAME as TRANSLATION_MANIFEST_NAME,
    TARGET_NAME as TRANSLATED_MINUTES_NAME,
    create_translation_manifest,
    resolve_final_markdown,
    validate_translation_manifest,
)
from scripts.utils import now_local, read_json, write_json


FRESH_CONTEXT_ENV = "MINUTES_FRESH_CONTEXT"
HANDOFF_SCHEMA_VERSION = 7
MAX_REQUEST_OVERRIDES = 4
MAX_REQUEST_CHARS = 500
MAX_CONSOLE_ERROR_CHARS = 500
DRY_RUN_CONSOLE_BUDGET_BYTES = 8_000
OVERSIZED_TOOL_OUTPUT_BYTES = 24_000
TOOL_OUTPUT_BUDGET_EXIT_CODE = 79
CODEX_STALL_EXIT_CODE = 80
CODEX_STREAM_HEARTBEAT_SECONDS = 10 * 60
CODEX_STREAM_STALL_TIMEOUT_SECONDS = 15 * 60
CODEX_STREAM_POLL_INTERVAL_SECONDS = 1.0
MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS = 4
FORBIDDEN_WORKER_INSTRUCTION_MARKERS = (
    "SKILL.md",
    "quality-loop.md",
    "docx-validation.md",
)
EVIDENCE_CHUNK_COMMAND_PATTERN = re.compile(
    r"evidence_chunks/(part-\d{4}\.md)\b"
)
WORKER_DOCUMENTS_PLUGIN_CONFIG = (
    'plugins."documents@openai-primary-runtime".enabled=false'
)
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_TRANSLATION_REASONING_EFFORT = "low"
NON_TOOL_ITEM_TYPES = {"agent_message", "reasoning"}
USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
WORKER_EVIDENCE_SUMMARY_NAME = "evidence_coverage_summary.json"
WORKER_EVIDENCE_CHUNKS_NAME = "evidence_chunks.json"
WORKER_EVIDENCE_CHUNKS_DIR = "evidence_chunks"
WORKER_RUNTIME_SUMMARY_NAME = "worker_runtime_summary.json"
CONTENT_FREEZE_NAME = "content_freeze.json"
CONTENT_CHECKPOINT_NAME = "content_generation_checkpoint.json"
CONTENT_REPAIR_PATCH_NAME = "content_repair_patch.json"
CONTENT_CHECKPOINT_SCHEMA_VERSION = 1
MAX_CONTENT_REPAIR_ATTEMPTS = 1
FRESH_PHASES = ("content", "translation", "delivery")
SUPPORTED_FRESH_PHASES = ("content", "content_repair", "translation", "delivery")
TOOL_ROUND_TRIP_TARGETS = {
    "content": 50,
    "content_repair": 12,
    "translation": 0,
    "delivery": 18,
}
CONTENT_GENERATED_ARTIFACT_NAMES = (
    "minutes.md",
    "content_inventory.json",
    "evidence_ledger.json",
    "document_blueprint.json",
    "official_sources.json",
    "content_audit.json",
    "content_quality_review.json",
    "content_density_baseline.json",
)
CONTENT_CHECKPOINT_STATES = {
    "awaiting_validation",
    "awaiting_repair",
    "repair_running",
    "repair_failed",
    "frozen",
}
CONTENT_REPAIR_FORBIDDEN_COMMAND_MARKERS = (
    "codex_minutes_input.md",
    "evidence_chunks/",
    "evidence_chunks.json",
    "evidence_coverage.json",
    "transcript.json",
    "transcript.txt",
    "transcript.srt",
    "screen_text.json",
    "screen_text.txt",
    "worker_runtime_summary.json",
    "status.json",
    "process_metrics.json",
    "speaker_attribution_report.json",
    "speech_activity.json",
    "/snapshots",
    "/frames",
    ".mov",
    ".mp4",
    ".mkv",
    ".m4a",
    ".mp3",
    ".wav",
    ".aac",
    ".flac",
    ".ogg",
    "scripts/content_audit.py",
    "scripts/content_quality.py",
    "/tests/",
)
WORKER_EVIDENCE_LINES_PER_CHUNK = 500
WORKER_EVIDENCE_MAX_BYTES_PER_CHUNK = 15_000
WORKER_REVIEW_REASONS = {
    "forced_coverage",
    "speaker_ui_change",
    "visual_only",
}


@dataclass
class CodexStreamSummary:
    event_count: int = 0
    event_bytes: int = 0
    stderr_bytes: int = 0
    malformed_event_lines: int = 0
    thread_id: str | None = None
    usage: dict[str, int] = field(
        default_factory=lambda: {key: 0 for key in USAGE_KEYS}
    )
    item_counts: dict[str, int] = field(default_factory=dict)
    tool_item_ids: set[str] = field(default_factory=set)
    phase_checkpoints: list[dict[str, object]] = field(default_factory=list)
    tool_output_bytes: int = 0
    max_tool_output_bytes: int = 0
    oversized_tool_output_count: int = 0
    tool_output_budget_violations: list[dict[str, object]] = field(
        default_factory=list
    )
    artifact_change_bytes: int = 0
    max_artifact_change_bytes: int = 0
    large_artifact_change_count: int = 0
    forbidden_instruction_read_count: int = 0
    forbidden_instruction_reads: list[dict[str, object]] = field(
        default_factory=list
    )
    forbidden_artifact_write_count: int = 0
    forbidden_artifact_writes: list[dict[str, object]] = field(
        default_factory=list
    )
    evidence_chunk_paths_seen: set[str] = field(default_factory=set)
    duplicate_evidence_chunk_read_count: int = 0
    duplicate_evidence_chunk_reads: list[dict[str, object]] = field(
        default_factory=list
    )
    stall_warning_count: int = 0
    stalled: bool = False
    last_event_elapsed_seconds: float = 0.0
    stall_timeout_seconds: float | None = None

    def context_efficiency_fields(self) -> dict[str, object]:
        input_tokens = self.usage["input_tokens"]
        cached_input_tokens = min(
            input_tokens,
            self.usage["cached_input_tokens"],
        )
        output_tokens = self.usage["output_tokens"]
        return {
            "uncached_input_tokens": input_tokens - cached_input_tokens,
            "cached_input_ratio": (
                round(cached_input_tokens / input_tokens, 6)
                if input_tokens
                else 0.0
            ),
            "input_to_output_ratio": (
                round(input_tokens / output_tokens, 3)
                if output_tokens
                else None
            ),
            "tool_output_bytes": self.tool_output_bytes,
            "max_tool_output_bytes": self.max_tool_output_bytes,
            "oversized_tool_output_count": self.oversized_tool_output_count,
            "oversized_tool_output_threshold_bytes": OVERSIZED_TOOL_OUTPUT_BYTES,
            "tool_output_budget_passed": self.oversized_tool_output_count == 0,
            "tool_output_budget_violations": list(
                self.tool_output_budget_violations
            ),
            "artifact_change_bytes": self.artifact_change_bytes,
            "max_artifact_change_bytes": self.max_artifact_change_bytes,
            "large_artifact_change_count": self.large_artifact_change_count,
            "large_artifact_change_threshold_bytes": OVERSIZED_TOOL_OUTPUT_BYTES,
            "forbidden_instruction_read_count": self.forbidden_instruction_read_count,
            "forbidden_instruction_reads": list(self.forbidden_instruction_reads),
            "forbidden_artifact_write_count": self.forbidden_artifact_write_count,
            "forbidden_artifact_writes": list(self.forbidden_artifact_writes),
            "evidence_chunk_read_count": len(self.evidence_chunk_paths_seen),
            "duplicate_evidence_chunk_read_count": (
                self.duplicate_evidence_chunk_read_count
            ),
            "duplicate_evidence_chunk_reads": list(
                self.duplicate_evidence_chunk_reads
            ),
            "worker_contract_passed": (
                self.oversized_tool_output_count == 0
                and self.forbidden_instruction_read_count == 0
                and self.forbidden_artifact_write_count == 0
                and self.duplicate_evidence_chunk_read_count == 0
            ),
        }

    def manifest_fields(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "event_count": self.event_count,
            "event_bytes": self.event_bytes,
            "stdout_bytes": self.event_bytes,
            "stderr_bytes": self.stderr_bytes,
            "malformed_event_lines": self.malformed_event_lines,
            "token_usage": dict(self.usage),
            "tool_count": len(self.tool_item_ids),
            "item_counts": dict(sorted(self.item_counts.items())),
            "context_efficiency": self.context_efficiency_fields(),
            "phase_checkpoints": list(self.phase_checkpoints),
            "stall_monitor": {
                "warning_count": self.stall_warning_count,
                "stalled": self.stalled,
                "last_event_elapsed_seconds": round(
                    self.last_event_elapsed_seconds,
                    3,
                ),
                "timeout_seconds": self.stall_timeout_seconds,
            },
        }


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def content_artifact_snapshot(job_dir: Path) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for name in CONTENT_GENERATED_ARTIFACT_NAMES:
        path = job_dir / name
        if path.is_file():
            snapshot[name] = {
                "present": True,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        else:
            snapshot[name] = {"present": False}
    return snapshot


def write_content_generation_checkpoint(
    job_dir: Path,
    *,
    content_prompt: str,
    state: str,
    repair_attempts: int,
    validation_error: str | None = None,
) -> dict[str, object]:
    if state not in CONTENT_CHECKPOINT_STATES:
        raise ValueError(f"unsupported content checkpoint state: {state}")
    if (
        isinstance(repair_attempts, bool)
        or not isinstance(repair_attempts, int)
        or not 0 <= repair_attempts <= MAX_CONTENT_REPAIR_ATTEMPTS
    ):
        raise ValueError("repair_attempts is out of range")
    checkpoint: dict[str, object] = {
        "schema_version": CONTENT_CHECKPOINT_SCHEMA_VERSION,
        "state": state,
        "updated_at": now_local().isoformat(),
        "content_prompt_sha256": hashlib.sha256(
            content_prompt.encode("utf-8")
        ).hexdigest(),
        "repair_attempts": repair_attempts,
        "artifacts": content_artifact_snapshot(job_dir),
    }
    if validation_error:
        checkpoint["validation_error"] = _bounded_text(
            validation_error,
            max_chars=12_000,
        )
    write_json(job_dir / CONTENT_CHECKPOINT_NAME, checkpoint)
    return checkpoint


def inspect_content_generation_checkpoint(
    job_dir: Path,
    *,
    content_prompt: str,
) -> dict[str, object]:
    path = job_dir / CONTENT_CHECKPOINT_NAME
    if not path.is_file():
        return {"exists": False, "valid": False}
    try:
        checkpoint = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "valid": False, "error": _bounded_console_text(exc)}
    if not isinstance(checkpoint, Mapping):
        return {
            "exists": True,
            "valid": False,
            "error": "checkpoint must contain a JSON object",
        }
    prompt_hash = hashlib.sha256(content_prompt.encode("utf-8")).hexdigest()
    valid = (
        checkpoint.get("schema_version") == CONTENT_CHECKPOINT_SCHEMA_VERSION
        and checkpoint.get("state") in CONTENT_CHECKPOINT_STATES
        and isinstance(checkpoint.get("repair_attempts"), int)
        and not isinstance(checkpoint.get("repair_attempts"), bool)
        and 0
        <= int(checkpoint.get("repair_attempts", -1))
        <= MAX_CONTENT_REPAIR_ATTEMPTS
        and isinstance(checkpoint.get("artifacts"), dict)
    )
    return {
        "exists": True,
        "valid": valid,
        "prompt_matches": checkpoint.get("content_prompt_sha256") == prompt_hash,
        "artifacts_match": checkpoint.get("artifacts")
        == content_artifact_snapshot(job_dir),
        "state": checkpoint.get("state"),
        "repair_attempts": checkpoint.get("repair_attempts"),
        "validation_error": checkpoint.get("validation_error"),
    }


def select_content_action(
    job_dir: Path,
    *,
    content_prompt: str,
    force_content_rebuild: bool = False,
) -> str:
    if force_content_rebuild:
        return "run_content"
    freeze_path = job_dir / CONTENT_FREEZE_NAME
    if freeze_path.is_file():
        try:
            from scripts.content_freeze import validate_content_freeze

            validate_content_freeze(job_dir)
            return "reuse_freeze"
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    checkpoint = inspect_content_generation_checkpoint(
        job_dir,
        content_prompt=content_prompt,
    )
    if not checkpoint.get("exists"):
        return "run_content"
    if not checkpoint.get("valid"):
        return "checkpoint_invalid"
    if not checkpoint.get("prompt_matches"):
        return "checkpoint_mismatch"
    if int(checkpoint.get("repair_attempts", 0)) >= MAX_CONTENT_REPAIR_ATTEMPTS:
        return "repair_exhausted"
    return "run_repair"


def directory_record(directory: Path, pattern: str) -> dict[str, object]:
    paths = sorted(directory.glob(pattern)) if directory.is_dir() else []
    records = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in paths
    ]
    manifest_bytes = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "path": str(directory),
        "count": len(records),
        "total_bytes": sum(int(item["bytes"]) for item in records),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def write_worker_evidence_summary(job_dir: Path) -> Path:
    coverage_path = job_dir / "evidence_coverage.json"
    output_path = job_dir / WORKER_EVIDENCE_SUMMARY_NAME
    if not coverage_path.is_file():
        write_json(
            output_path,
            {
                "schema_version": 1,
                "status": "not_available",
                "coverage_path": str(coverage_path),
                "review_frames": [],
            },
        )
        return output_path

    coverage = read_json(coverage_path)
    frames = coverage.get("frames", [])
    if not isinstance(frames, list):
        frames = []
    review_frames: list[dict[str, object]] = []
    for frame in frames:
        if not isinstance(frame, Mapping):
            continue
        reason = str(frame.get("reason", ""))
        if reason not in WORKER_REVIEW_REASONS:
            continue
        review_frames.append(
            {
                key: frame.get(key)
                for key in (
                    "evidence_id",
                    "timestamp_seconds",
                    "timestamp",
                    "reason",
                    "snapshot_evidence_id",
                    "snapshot",
                    "ocr_text_present",
                )
                if key in frame
            }
        )

    summary_keys = (
        "schema_version",
        "status",
        "coverage_passed",
        "frame_interval_seconds",
        "max_snapshot_gap_limit_seconds",
        "max_selected_snapshot_gap_seconds",
        "raw_frame_count",
        "raw_frames_bytes",
        "raw_frames_manifest_sha256",
        "selected_snapshot_count",
        "accounted_frame_count",
        "accounting_complete",
        "reason_counts",
        "selected_reason_counts",
        "excluded_reason_counts",
        "raw_frames_retention",
    )
    summary = {key: coverage.get(key) for key in summary_keys if key in coverage}
    summary.update(
        {
            "worker_summary_schema_version": 1,
            "source_coverage_path": str(coverage_path),
            "source_coverage_bytes": coverage_path.stat().st_size,
            "source_coverage_sha256": sha256_file(coverage_path),
            "review_frame_count": len(review_frames),
            "review_frames": review_frames,
        }
    )
    write_json(output_path, summary)
    return output_path


def _pick_fields(
    source: Mapping[str, object],
    keys: Sequence[str],
) -> dict[str, object]:
    return {key: source.get(key) for key in keys if key in source}


def _bounded_json_record(path: Path, fields: Mapping[str, object]) -> dict[str, object]:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "fields": dict(fields),
    }


def write_worker_runtime_summary(job_dir: Path) -> Path:
    output_path = job_dir / WORKER_RUNTIME_SUMMARY_NAME
    summary: dict[str, object] = {
        "schema_version": 1,
        "status": "completed",
        "missing_files": [],
    }

    status_path = job_dir / "status.json"
    if status_path.is_file():
        status = read_json(status_path)
        handoff = status.get("codex_handoff", {})
        if not isinstance(handoff, Mapping):
            handoff = {}
        status_fields = _pick_fields(
            status,
            (
                "status",
                "step",
                "job_id",
                "recording_date",
                "source",
                "output_root",
                "resource_policy",
                "content_audit",
                "speaker_attribution",
            ),
        )
        status_fields["codex_handoff"] = _pick_fields(
            handoff,
            (
                "output_language",
                "detected_language",
                "content_audit_mode",
                "official_source_verification",
                "docx_enabled",
                "selected_snapshot_count",
                "speaker_evidence_policy",
            ),
        )
        summary["job_status"] = _bounded_json_record(status_path, status_fields)
    else:
        summary["missing_files"].append(str(status_path))

    metrics_path = job_dir / "process_metrics.json"
    if metrics_path.is_file():
        metrics = read_json(metrics_path)
        metrics_fields = _pick_fields(
            metrics,
            (
                "state",
                "started_at",
                "completed_at",
                "elapsed_seconds",
                "preprocessing_elapsed_seconds",
                "elapsed_scope",
                "peak_observed_job_bytes",
                "resource_policy",
                "intermediate_cleanup",
                "cpu_metric_note",
            ),
        )
        stages = metrics.get("stages", [])
        metrics_fields["stages"] = [
            _pick_fields(
                stage,
                (
                    "step",
                    "wall_seconds",
                    "cpu_seconds",
                    "cpu_percent_of_one_core",
                    "job_bytes",
                ),
            )
            for stage in stages
            if isinstance(stage, Mapping)
        ]
        summary["process_metrics"] = _bounded_json_record(
            metrics_path,
            metrics_fields,
        )
    else:
        summary["missing_files"].append(str(metrics_path))

    speaker_path = job_dir / "speaker_attribution_report.json"
    if speaker_path.is_file():
        speaker = read_json(speaker_path)
        summary["speaker_attribution"] = _bounded_json_record(
            speaker_path,
            _pick_fields(
                speaker,
                (
                    "status",
                    "requested_mode",
                    "effective_mode",
                    "local_audio_diarization",
                    "speech_activity_validation",
                    "identity_resolution_method",
                    "screen_evidence_available",
                    "snapshot_evidence_available",
                    "evidence_sources",
                    "speaker_resolution_rule",
                ),
            ),
        )
    else:
        summary["missing_files"].append(str(speaker_path))

    activity_path = job_dir / "speech_activity.json"
    if activity_path.is_file():
        activity = read_json(activity_path)
        summary["speech_activity"] = _bounded_json_record(
            activity_path,
            _pick_fields(
                activity,
                (
                    "status",
                    "enabled",
                    "purpose",
                    "model_version",
                    "speaker_identity",
                    "speaker_diarization",
                    "transcript_modified",
                    "audio_duration_seconds",
                    "speech_duration_seconds",
                    "speech_ratio",
                    "speech_region_count",
                    "transcript_segment_count",
                    "suspect_segment_count",
                    "threads",
                ),
            ),
        )
    else:
        summary["missing_files"].append(str(activity_path))

    write_json(output_path, summary)
    return output_path


def write_worker_evidence_chunks(
    job_dir: Path,
    *,
    lines_per_chunk: int = WORKER_EVIDENCE_LINES_PER_CHUNK,
    max_bytes_per_chunk: int = WORKER_EVIDENCE_MAX_BYTES_PER_CHUNK,
) -> Path:
    if lines_per_chunk < 1:
        raise ValueError("lines_per_chunk must be positive")
    if max_bytes_per_chunk < 1:
        raise ValueError("max_bytes_per_chunk must be positive")
    input_path = job_dir / "codex_minutes_input.md"
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    lines = input_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines:
        raise ValueError("codex_minutes_input.md must not be empty")

    chunks_dir = job_dir / WORKER_EVIDENCE_CHUNKS_DIR
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in chunks_dir.glob("part-*.md"):
        stale_path.unlink()

    chunks: list[dict[str, object]] = []
    pending: list[str] = []
    pending_start = 0
    pending_bytes = 0

    def flush_chunk() -> None:
        nonlocal pending, pending_start, pending_bytes
        if not pending:
            return
        chunk_index = len(chunks) + 1
        content = "".join(pending)
        chunk_path = chunks_dir / f"part-{chunk_index:04}.md"
        chunk_path.write_text(content, encoding="utf-8")
        chunks.append(
            {
                "index": chunk_index,
                "path": str(chunk_path),
                "start_line": pending_start + 1,
                "end_line": pending_start + len(pending),
                "source_start_line": pending_start + 1,
                "source_end_line": pending_start + len(pending),
                "chunk_line_count": len(pending),
                "bytes": chunk_path.stat().st_size,
                "sha256": sha256_file(chunk_path),
            }
        )
        pending = []
        pending_bytes = 0

    for line_index, line in enumerate(lines):
        line_bytes = len(line.encode("utf-8"))
        if pending and (
            len(pending) >= lines_per_chunk
            or pending_bytes + line_bytes > max_bytes_per_chunk
        ):
            flush_chunk()
        if not pending:
            pending_start = line_index
        pending.append(line)
        pending_bytes += line_bytes
        if len(pending) >= lines_per_chunk or pending_bytes >= max_bytes_per_chunk:
            flush_chunk()
    flush_chunk()

    chunk_bytes = [int(item["bytes"]) for item in chunks]

    manifest_path = job_dir / WORKER_EVIDENCE_CHUNKS_NAME
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "quality_contract_version": QUALITY_CONTRACT_VERSION,
            "source_path": str(input_path),
            "source_bytes": input_path.stat().st_size,
            "source_sha256": sha256_file(input_path),
            "total_lines": len(lines),
            "lines_per_chunk": lines_per_chunk,
            "max_bytes_per_chunk": max_bytes_per_chunk,
            "chunk_count": len(chunks),
            "max_chunk_bytes": max(chunk_bytes),
            "oversized_chunk_count": sum(
                size > max_bytes_per_chunk for size in chunk_bytes
            ),
            "chunk_read_contract": {
                "scope": "entire_chunk_file",
                "source_coordinate_fields": ["start_line", "end_line"],
                "max_commands_per_chunk": 1,
            },
            "chunks": chunks,
        },
    )
    return manifest_path


def collect_evidence_manifest(job_dir: Path) -> dict[str, object]:
    input_path = job_dir / "codex_minutes_input.md"
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    evidence_names = (
        "codex_minutes_input.md",
        "transcript.evidence.txt",
        "transcript.txt",
        "screen_text.json",
        "screen_text.txt",
        "evidence_coverage.json",
        WORKER_EVIDENCE_SUMMARY_NAME,
        WORKER_EVIDENCE_CHUNKS_NAME,
        WORKER_RUNTIME_SUMMARY_NAME,
        "speaker_attribution_report.json",
        "speech_activity.json",
    )
    evidence_files = [
        file_record(path)
        for name in evidence_names
        if (path := job_dir / name).is_file()
    ]
    snapshots = directory_record(job_dir / "snapshots", "*.jpg")
    raw_frames = directory_record(job_dir / "frames", "frame_*.jpg")
    return {
        "files": evidence_files,
        "snapshots": snapshots,
        "raw_frames": raw_frames,
        "snapshot_count": snapshots["count"],
        "raw_frame_count": raw_frames["count"],
        "total_bytes": (
            sum(int(item["bytes"]) for item in evidence_files)
            + int(snapshots["total_bytes"])
            + int(raw_frames["total_bytes"])
        ),
    }


def build_dry_run_summary(
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Return a bounded operator summary without printing prompt or evidence bodies."""
    evidence = manifest.get("evidence", {})
    if not isinstance(evidence, Mapping):
        evidence = {}
    evidence_files = evidence.get("files", [])
    if not isinstance(evidence_files, list):
        evidence_files = []
    request_overrides = manifest.get("request_overrides", [])
    if not isinstance(request_overrides, list):
        request_overrides = []
    request_digest = hashlib.sha256(
        json.dumps(
            request_overrides,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    summary = {
        "schema_version": manifest.get("schema_version"),
        "state": manifest.get("state"),
        "job_dir": manifest.get("job_dir"),
        "policy": manifest.get("policy"),
        "request_override_count": len(request_overrides),
        "request_overrides_sha256": request_digest,
        "ephemeral_session": manifest.get("ephemeral_session"),
        "planned_ephemeral_session_count": manifest.get(
            "planned_ephemeral_session_count"
        ),
        "planned_phases": manifest.get("planned_phases"),
        "phase_isolation": manifest.get("phase_isolation"),
        "worker_contract": manifest.get("worker_contract"),
        "parent_conversation_inherited": manifest.get(
            "parent_conversation_inherited"
        ),
        "raw_evidence_embedded_in_handoff": manifest.get(
            "raw_evidence_embedded_in_handoff"
        ),
        "handoff_prompt_bytes": manifest.get("handoff_prompt_bytes"),
        "handoff_prompt_sha256": manifest.get("handoff_prompt_sha256"),
        "evidence_summary": {
            "file_count": len(evidence_files),
            "snapshot_count": evidence.get("snapshot_count"),
            "raw_frame_count": evidence.get("raw_frame_count"),
            "total_bytes": evidence.get("total_bytes"),
        },
        "runtime": manifest.get("runtime"),
        "translation_runtime": manifest.get("translation_runtime"),
        "commands": manifest.get("commands"),
        "omitted_from_console": [
            "content_prompt_body",
            "translation_prompt_body",
            "delivery_prompt_body",
            "evidence_file_records",
        ],
    }
    encoded = json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > DRY_RUN_CONSOLE_BUDGET_BYTES:
        raise RuntimeError(
            "dry-run console summary exceeded its bounded-output contract: "
            f"{len(encoded)} > {DRY_RUN_CONSOLE_BUDGET_BYTES} bytes"
        )
    return summary


def validate_job_dir(job_dir: Path, jobs_dir: Path) -> Path:
    resolved_job = job_dir.expanduser().resolve()
    resolved_jobs = jobs_dir.expanduser().resolve()
    if resolved_job.parent != resolved_jobs:
        raise ValueError(f"job directory must be a direct child of {resolved_jobs}")
    if not resolved_job.is_dir():
        raise FileNotFoundError(resolved_job)

    status = read_json(resolved_job / "status.json")
    if status.get("status") != "awaiting_codex":
        raise ValueError(
            "job status must be awaiting_codex before fresh Codex execution; "
            f"found {status.get('status')!r}"
        )
    if not (resolved_job / "codex_minutes_input.md").is_file():
        raise FileNotFoundError(resolved_job / "codex_minutes_input.md")
    return resolved_job


def ensure_not_nested(environ: Mapping[str, str]) -> None:
    if environ.get(FRESH_CONTEXT_ENV) == "1":
        raise RuntimeError(
            "fresh-context worker must complete the prepared job directly; "
            "nested fresh Codex execution is forbidden"
        )


def job_policy(status: Mapping[str, object]) -> dict[str, object]:
    content_audit = status.get("content_audit", {})
    if not isinstance(content_audit, Mapping):
        content_audit = {}
    return {
        **language_policy_from_status(status),
        "content_audit_mode": str(content_audit.get("mode", "off")),
        "official_source_verification": str(
            content_audit.get("official_source_verification", "off")
        ),
    }


def normalize_request_overrides(values: Sequence[str]) -> list[str]:
    if len(values) > MAX_REQUEST_OVERRIDES:
        raise ValueError(
            f"at most {MAX_REQUEST_OVERRIDES} short --request values are allowed"
        )
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        if "\n" in item or "\r" in item:
            raise ValueError("--request must be one short line")
        if len(item) > MAX_REQUEST_CHARS:
            raise ValueError(
                f"--request must be at most {MAX_REQUEST_CHARS} characters"
            )
        normalized.append(item)
    return normalized


def configured_codex_runtime(
    environ: Mapping[str, str],
    *,
    model_override: str | None = None,
    reasoning_effort_override: str | None = None,
) -> dict[str, str | None]:
    codex_home = Path(environ.get("CODEX_HOME", "~/.codex")).expanduser()
    config_path = codex_home / "config.toml"
    result: dict[str, str | None] = {
        "model": None,
        "reasoning_effort": None,
        "config_path": str(config_path),
    }
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        config = {}

    model = config.get("model")
    reasoning = config.get("model_reasoning_effort")
    if isinstance(model, str):
        result["model"] = model
    if isinstance(reasoning, str):
        result["reasoning_effort"] = reasoning
    if model_override:
        result["model"] = model_override
    if reasoning_effort_override:
        result["reasoning_effort"] = reasoning_effort_override
    return result


def _bounded_text(value: object, *, max_chars: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _bounded_console_text(value: object) -> str:
    return _bounded_text(value, max_chars=MAX_CONSOLE_ERROR_CHARS)


def _event_error_message(event: Mapping[str, object]) -> str:
    for key in ("message", "error", "detail"):
        value = event.get(key)
        if value:
            return _bounded_console_text(value)
    return _bounded_console_text(event.get("type", "unknown error"))


def _model_visible_tool_output(item: Mapping[str, object]) -> str:
    for key in ("aggregated_output", "output", "changes", "result"):
        value = item.get(key)
        if isinstance(value, str):
            return value
        if value is not None:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
    return ""


def _tool_item_preview(item: Mapping[str, object]) -> str:
    command = item.get("command")
    if command:
        return _bounded_console_text(command)
    path = item.get("path")
    if path:
        return _bounded_console_text(path)
    return _bounded_console_text(item.get("type", "unknown tool item"))


def _file_change_paths(item: Mapping[str, object]) -> list[str]:
    paths: list[str] = []
    direct_path = item.get("path")
    if isinstance(direct_path, str) and direct_path:
        paths.append(direct_path)
    changes = item.get("changes")
    if isinstance(changes, Mapping):
        changes = [changes]
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            path = change.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    return paths


def record_codex_event(
    event: Mapping[str, object],
    summary: CodexStreamSummary,
    *,
    elapsed_seconds: float,
    progress_stream: TextIO,
    forbidden_command_markers: Sequence[str] = (),
    allowed_file_change_names: Sequence[str] | None = None,
) -> None:
    event_type = str(event.get("type", "unknown"))
    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str):
            summary.thread_id = thread_id
        print("fresh Codex: started", file=progress_stream, flush=True)
    elif event_type == "turn.started":
        summary.phase_checkpoints.append(
            {
                "phase": "turn_started",
                "elapsed_seconds": round(elapsed_seconds, 3),
                "event_index": summary.event_count,
            }
        )
    elif event_type == "turn.completed":
        usage = event.get("usage", {})
        if isinstance(usage, Mapping):
            for key in USAGE_KEYS:
                value = usage.get(key, 0)
                if isinstance(value, int) and value >= 0:
                    summary.usage[key] += value
        summary.phase_checkpoints.append(
            {
                "phase": "turn_completed",
                "elapsed_seconds": round(elapsed_seconds, 3),
                "event_index": summary.event_count,
            }
        )
        print(
            "fresh Codex: turn completed "
            f"(input={summary.usage['input_tokens']}, "
            f"cached={summary.usage['cached_input_tokens']}, "
            f"output={summary.usage['output_tokens']})",
            file=progress_stream,
            flush=True,
        )
    elif event_type in {"turn.failed", "error"}:
        summary.phase_checkpoints.append(
            {
                "phase": event_type.replace(".", "_"),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "event_index": summary.event_count,
            }
        )
        print(
            f"fresh Codex: {event_type}: {_event_error_message(event)}",
            file=progress_stream,
            flush=True,
        )

    if event_type != "item.completed":
        return
    item = event.get("item", {})
    if not isinstance(item, Mapping):
        return
    item_type = str(item.get("type", "unknown"))
    summary.item_counts[item_type] = summary.item_counts.get(item_type, 0) + 1
    output = _model_visible_tool_output(item)
    if output:
        output_bytes = len(output.encode("utf-8"))
        if item_type == "file_change":
            summary.artifact_change_bytes += output_bytes
            summary.max_artifact_change_bytes = max(
                summary.max_artifact_change_bytes,
                output_bytes,
            )
            if output_bytes > OVERSIZED_TOOL_OUTPUT_BYTES:
                summary.large_artifact_change_count += 1
        else:
            summary.tool_output_bytes += output_bytes
            summary.max_tool_output_bytes = max(
                summary.max_tool_output_bytes,
                output_bytes,
            )
            if output_bytes > OVERSIZED_TOOL_OUTPUT_BYTES:
                summary.oversized_tool_output_count += 1
                if (
                    len(summary.tool_output_budget_violations)
                    < MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
                ):
                    preview = _tool_item_preview(item)
                    summary.tool_output_budget_violations.append(
                        {
                            "item_id": str(item.get("id", "")),
                            "item_type": item_type,
                            "output_bytes": output_bytes,
                            "command": preview,
                            "command_sha256": hashlib.sha256(
                                str(item.get("command", "")).encode("utf-8")
                            ).hexdigest(),
                        }
                    )
    if item_type == "command_execution":
        command = str(item.get("command", ""))
        lowered_command = command.lower()
        forbidden_markers = (
            *FORBIDDEN_WORKER_INSTRUCTION_MARKERS,
            *forbidden_command_markers,
        )
        if any(marker.lower() in lowered_command for marker in forbidden_markers):
            summary.forbidden_instruction_read_count += 1
            if (
                len(summary.forbidden_instruction_reads)
                < MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
            ):
                summary.forbidden_instruction_reads.append(
                    {
                        "item_id": str(item.get("id", "")),
                        "command": _bounded_console_text(command),
                        "command_sha256": hashlib.sha256(
                            command.encode("utf-8")
                        ).hexdigest(),
                    }
                )
        chunk_paths = set(EVIDENCE_CHUNK_COMMAND_PATTERN.findall(command))
        duplicates = sorted(chunk_paths & summary.evidence_chunk_paths_seen)
        summary.evidence_chunk_paths_seen.update(chunk_paths)
        if duplicates:
            summary.duplicate_evidence_chunk_read_count += len(duplicates)
            for chunk_path in duplicates:
                if (
                    len(summary.duplicate_evidence_chunk_reads)
                    >= MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
                ):
                    break
                summary.duplicate_evidence_chunk_reads.append(
                    {
                        "item_id": str(item.get("id", "")),
                        "chunk": chunk_path,
                        "command": _bounded_console_text(command),
                        "command_sha256": hashlib.sha256(
                            command.encode("utf-8")
                        ).hexdigest(),
                    }
                )
    if item_type == "file_change" and allowed_file_change_names is not None:
        paths = _file_change_paths(item)
        allowed_names = set(allowed_file_change_names)
        forbidden_paths = [
            path for path in paths if Path(path).name not in allowed_names
        ]
        if not paths or forbidden_paths:
            summary.forbidden_artifact_write_count += 1
            if (
                len(summary.forbidden_artifact_writes)
                < MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
            ):
                summary.forbidden_artifact_writes.append(
                    {
                        "item_id": str(item.get("id", "")),
                        "paths": [_bounded_console_text(path) for path in paths],
                        "forbidden_paths": [
                            _bounded_console_text(path) for path in forbidden_paths
                        ],
                    }
                )
    item_id = item.get("id")
    if item_type not in NON_TOOL_ITEM_TYPES and isinstance(item_id, str):
        summary.tool_item_ids.add(item_id)


def consume_codex_event_line(
    raw_line: str,
    summary: CodexStreamSummary,
    *,
    elapsed_seconds: float,
    progress_stream: TextIO,
    forbidden_command_markers: Sequence[str] = (),
    allowed_file_change_names: Sequence[str] | None = None,
) -> None:
    summary.event_count += 1
    summary.event_bytes += len(raw_line.encode("utf-8"))
    try:
        event = json.loads(raw_line)
    except json.JSONDecodeError:
        summary.malformed_event_lines += 1
        return
    if isinstance(event, Mapping):
        record_codex_event(
            event,
            summary,
            elapsed_seconds=elapsed_seconds,
            progress_stream=progress_stream,
            forbidden_command_markers=forbidden_command_markers,
            allowed_file_change_names=allowed_file_change_names,
        )


def run_codex_json_stream(
    command: Sequence[str],
    *,
    prompt: str,
    env: Mapping[str, str],
    events_path: Path,
    stderr_path: Path,
    progress_stream: TextIO,
    heartbeat_seconds: float = CODEX_STREAM_HEARTBEAT_SECONDS,
    stall_timeout_seconds: float = CODEX_STREAM_STALL_TIMEOUT_SECONDS,
    poll_interval_seconds: float = CODEX_STREAM_POLL_INTERVAL_SECONDS,
    forbidden_command_markers: Sequence[str] = (),
    allowed_file_change_names: Sequence[str] | None = None,
) -> tuple[int, CodexStreamSummary]:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    summary = CodexStreamSummary()
    if heartbeat_seconds <= 0 or stall_timeout_seconds <= heartbeat_seconds:
        raise ValueError("stall timeout must be greater than the positive heartbeat")
    if poll_interval_seconds <= 0:
        raise ValueError("stream poll interval must be positive")
    summary.stall_timeout_seconds = stall_timeout_seconds
    started = time.perf_counter()
    with (
        events_path.open("w", encoding="utf-8") as events_handle,
        stderr_path.open("w", encoding="utf-8") as stderr_handle,
    ):
        os.chmod(events_path, 0o600)
        os.chmod(stderr_path, 0o600)
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env),
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            raise RuntimeError("failed to open fresh Codex process streams")

        def drain_stderr() -> None:
            for line in process.stderr:
                stderr_handle.write(line)
                stderr_handle.flush()
                summary.stderr_bytes += len(line.encode("utf-8"))
                if "error" in line.lower() or "fatal" in line.lower():
                    print(
                        f"fresh Codex stderr: {_bounded_console_text(line)}",
                        file=progress_stream,
                        flush=True,
                    )

        stderr_thread = threading.Thread(
            target=drain_stderr,
            name="fresh-codex-stderr",
            daemon=True,
        )
        stdout_queue: queue.Queue[str | None] = queue.Queue()

        def drain_stdout() -> None:
            for line in process.stdout:
                stdout_queue.put(line)
            stdout_queue.put(None)

        stdout_thread = threading.Thread(
            target=drain_stdout,
            name="fresh-codex-stdout",
            daemon=True,
        )
        stderr_thread.start()
        stdout_thread.start()
        try:
            process.stdin.write(prompt)
            process.stdin.close()
            budget_exceeded = False
            stall_exceeded = False
            returncode: int
            last_event_at = time.perf_counter()
            next_warning_at = last_event_at + heartbeat_seconds
            while True:
                now = time.perf_counter()
                timeout = min(
                    poll_interval_seconds,
                    max(0.001, next_warning_at - now),
                    max(0.001, last_event_at + stall_timeout_seconds - now),
                )
                try:
                    line = stdout_queue.get(timeout=timeout)
                except queue.Empty:
                    now = time.perf_counter()
                    idle_seconds = now - last_event_at
                    if idle_seconds >= stall_timeout_seconds:
                        summary.stalled = True
                        stall_exceeded = True
                        print(
                            "fresh Codex: stalled with no JSON events for "
                            f"{idle_seconds:.1f}s; terminating phase",
                            file=progress_stream,
                            flush=True,
                        )
                        process.terminate()
                        break
                    if now >= next_warning_at:
                        summary.stall_warning_count += 1
                        print(
                            "fresh Codex: no JSON events for "
                            f"{idle_seconds:.1f}s; phase still running",
                            file=progress_stream,
                            flush=True,
                        )
                        next_warning_at = now + heartbeat_seconds
                    continue
                if line is None:
                    break
                now = time.perf_counter()
                last_event_at = now
                next_warning_at = now + heartbeat_seconds
                summary.last_event_elapsed_seconds = now - started
                events_handle.write(line)
                events_handle.flush()
                previous_violation_count = summary.oversized_tool_output_count
                previous_instruction_read_count = (
                    summary.forbidden_instruction_read_count
                )
                previous_artifact_write_count = (
                    summary.forbidden_artifact_write_count
                )
                previous_duplicate_chunk_read_count = (
                    summary.duplicate_evidence_chunk_read_count
                )
                consume_codex_event_line(
                    line,
                    summary,
                    elapsed_seconds=time.perf_counter() - started,
                    progress_stream=progress_stream,
                    forbidden_command_markers=forbidden_command_markers,
                    allowed_file_change_names=allowed_file_change_names,
                )
                output_budget_exceeded = (
                    summary.oversized_tool_output_count > previous_violation_count
                )
                instruction_read_detected = (
                    summary.forbidden_instruction_read_count
                    > previous_instruction_read_count
                )
                artifact_write_detected = (
                    summary.forbidden_artifact_write_count
                    > previous_artifact_write_count
                )
                duplicate_chunk_read_detected = (
                    summary.duplicate_evidence_chunk_read_count
                    > previous_duplicate_chunk_read_count
                )
                if (
                    output_budget_exceeded
                    or instruction_read_detected
                    or artifact_write_detected
                    or duplicate_chunk_read_detected
                ):
                    budget_exceeded = True
                    if output_budget_exceeded:
                        violation = summary.tool_output_budget_violations[-1]
                        detail = (
                            "tool output budget exceeded"
                            f" (bytes={violation['output_bytes']}, "
                            f"command={violation['command']})"
                        )
                    elif instruction_read_detected:
                        violation = summary.forbidden_instruction_reads[-1]
                        detail = (
                            "forbidden worker instruction read"
                            f" (command={violation['command']})"
                        )
                    elif artifact_write_detected:
                        violation = summary.forbidden_artifact_writes[-1]
                        detail = (
                            "forbidden artifact write"
                            f" (paths={violation['paths']})"
                        )
                    else:
                        violation = summary.duplicate_evidence_chunk_reads[-1]
                        detail = (
                            "duplicate evidence chunk read"
                            f" (chunk={violation['chunk']}, "
                            f"command={violation['command']})"
                        )
                    print(
                        f"fresh Codex: {detail}; terminating phase",
                        file=progress_stream,
                        flush=True,
                    )
                    process.terminate()
                    break
            if budget_exceeded or stall_exceeded:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                returncode = (
                    TOOL_OUTPUT_BUDGET_EXIT_CODE
                    if budget_exceeded
                    else CODEX_STALL_EXIT_CODE
                )
            else:
                returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        finally:
            stdout_thread.join()
            stderr_thread.join()
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            process.stderr.close()
    return returncode, summary


def _content_audit_contract_fragment() -> str:
    return (
        '{"schema_version":1,"status":"passed","covered_item_ids":["..."],'
        '"missing_item_ids":[],"documented_conflict_ids":[],"recording_fidelity":'
        '{"preserved_item_ids":["..."],"rewritten_by_external_source_item_ids":[]},'
        '"coverage":[{"item_id":"...","document_refs":["exact minutes substring"]}],'
        '"conflict_coverage":[],"qualifier_changes":[],"silent_conflicts":[],'
        '"intentional_omissions":[]}'
    )


def _quality_review_contract_fragment() -> str:
    checks = ",".join(
        f'"{name}":{{"status":"passed","finding":"concrete finding"}}'
        for name in MODEL_FINAL_CHECKS
    )
    return (
        '{"schema_version":3,"status":"passed","review_cycles":['
        '{"cycle":1,"status":"passed","findings":[],"changes":[]}],'
        f'"final_checks":{{{checks}}},'
        '"required_item_checks":[{"item_id":"...","section_id":"...",'
        '"dimensions":{"core_facts":{"status":"covered","document_refs":'
        '["exact primary-H2 substring"]},"conditions_exceptions":'
        '{"status":"not_applicable","rationale":"concrete reason"},'
        '"risks_limitations":{"status":"not_applicable","rationale":"concrete reason"},'
        '"impact":{"status":"not_applicable","rationale":"concrete reason"},'
        '"actions_decisions":{"status":"not_applicable","rationale":"concrete reason"}}}]}'
    )


def _official_sources_contract_fragment() -> str:
    return (
        'completed claim={"inventory_item_ids":["..."],"status":'
        '"verified","purpose":"transcription_disambiguation","appendix_category":'
        '"transcription_or_ocr_support","appendix_category_heading":'
        '"exact H3","recording_content_preserved":true,"current_official_finding":'
        '"...","document_treatment":"...","recording_document_refs":'
        '["exact body substring"],"appendix_document_refs":["exact appendix substring"],'
        '"sources":[{"title":"...","url":"https://...","publisher":"...",'
        '"source_type":"official","published_or_updated":"..."}]}; '
        'top-level={"schema_version":1,"status":"completed",'
        '"policy":"official_only","checked_at":"timezone-aware ISO-8601",'
        '"appendix_heading":"canonical final H2","claims":[...],'
        '"privacy":{"raw_transcript_or_ocr_sent":false}}; claim status is one of verified, '
        'contradicted, partially_verified, not_found; auto purpose/category mapping is '
        'transcription_disambiguation=>transcription_or_ocr_support or '
        'source_conflict_resolution=>video_conflict; not_applicable uses status=not_applicable, '
        'reason, claims=[] and the same policy/checked_at/heading/privacy fields; required mode '
        'additionally allows current_fact_check=>current_official_status'
    )


def build_fresh_prompt(
    repo_root: Path,
    job_dir: Path,
    *,
    policy: Mapping[str, object],
    request_overrides: Sequence[str] = (),
) -> str:
    input_path = job_dir / "codex_minutes_input.md"
    evidence_summary_path = job_dir / WORKER_EVIDENCE_SUMMARY_NAME
    evidence_chunks_path = job_dir / WORKER_EVIDENCE_CHUNKS_NAME
    runtime_summary_path = job_dir / WORKER_RUNTIME_SUMMARY_NAME
    snapshots_dir = job_dir / "snapshots"
    overrides = normalize_request_overrides(request_overrides)
    override_text = "\n".join(f"- {item}" for item in overrides) or "- 없음 / none"
    final_output_language = str(policy.get("output_language", "auto"))
    detected_language = str(policy.get("detected_language", "unknown"))
    needs_translation = translation_required(
        final_output_language,
        detected_language,
    )
    content_language = content_output_language(
        final_output_language,
        detected_language,
    )
    python_executable = repo_root / ".venv/bin/python"
    freeze_script = repo_root / "scripts/content_freeze.py"
    freeze_command = " ".join(
        shlex.quote(str(value))
        for value in (python_executable, freeze_script, job_dir)
    )
    ledger_classifications = "|".join(sorted(LEDGER_CLASSIFICATIONS))
    blueprint_archetypes = "|".join(sorted(BLUEPRINT_ARCHETYPES))
    blueprint_roles = "|".join(sorted(BLUEPRINT_ROLES))
    blueprint_forms = "|".join(sorted(BLUEPRINT_FORM_FACTORS))
    blueprint_writing_styles = "|".join(sorted(BLUEPRINT_WRITING_STYLES))
    front_matter_keys = "|".join(sorted(REQUIRED_FRONT_MATTER_KEYS))
    required_item_dimensions = "|".join(REQUIRED_ITEM_DIMENSIONS)
    language_contract = (
        "Write validated minutes.md in the detected source language. Do not translate; the "
        "next phase translates it once without evidence."
        if needs_translation
        else "Write validated minutes.md in CONTENT_OUTPUT_LANGUAGE."
    )
    return (
        "Fresh worker; preloaded compact content contract. Do not invoke a skill. "
        "Do not open SKILL.md. "
        "Do not open quality-loop.md or other references.\n\n"
        "Do not relaunch run_fresh_codex_job.py.\n\n"
        f"Repository: {repo_root}\n"
        f"Job directory: {job_dir}\n"
        f"Evidence input (do not read): {input_path}\n"
        f"Evidence chunk manifest: {evidence_chunks_path}\n"
        f"Bounded evidence coverage summary: {evidence_summary_path}\n"
        f"Runtime summary: {runtime_summary_path}\n"
        f"Snapshots: {snapshots_dir}\n"
        f"FINAL_OUTPUT_LANGUAGE={final_output_language}\n"
        f"CONTENT_OUTPUT_LANGUAGE={content_language}\n"
        f"TRANSLATION_REQUIRED={'true' if needs_translation else 'false'}\n"
        f"Detected source language: {detected_language}\n"
        f"CONTENT_AUDIT_MODE={policy.get('content_audit_mode', 'off')}\n"
        "OFFICIAL_SOURCE_VERIFICATION="
        f"{policy.get('official_source_verification', 'off')}\n\n"
        "Overrides:\n"
        f"{override_text}\n\n"
        f"Language contract: {language_contract}\n\n"
        "This is a production media job. Do not reread this contract. Do not "
        "inspect validator implementations or tests. Do not run repository-wide py_compile, unittest "
        "discover, pytest, lint, or git review commands; the freeze is the acceptance gate.\n\n"
        "Read worker_runtime_summary.json once; do not print raw status/metrics/speaker/speech JSON. "
        "Read evidence_chunks.json once and every listed chunk exactly once in manifest order. "
        "Read each chunks[].path whole in one command; manifest line fields are unsplit-source "
        "coordinates. Duplicate reads terminate. Each part is <=15KB; never concatenate files. "
        "Do not read codex_minutes_input.md directly. Read evidence_coverage_summary.json once; "
        "verify accounting/max-gap and inspect only material "
        "visual_only, speaker_ui_change, and forced_coverage snapshots. Use resolvable "
        "STT:HH:MM:SS-HH:MM:SS, OCR:HH:MM:SS, or Snapshot:snapshot-NNNN@HH:MM:SS refs. "
        "Do not read or print evidence_coverage.json; validators own the raw ledger. Do not read "
        "or print fresh_codex_handoff.json. Do not recursively list the job directory or frames. "
        "Keep command output <=16KB; 24KB terminates. Build one cumulative inventory without lossy "
        f"summaries; target <={TOOL_ROUND_TRIP_TARGETS['content']} tool calls without skipping gates.\n\n"
        f"Exact strict sidecar quality contract v{QUALITY_CONTRACT_VERSION}; do not inspect validator source/tests or invent enums:\n"
        f"- evidence_ledger.json: schema_version=1, status=completed, chunk_count; every chunk uses "
        f"index, source_sha256, classification({ledger_classifications}), rationale, material_topics[], "
        "inventory_item_ids[]. material/mixed needs topics+IDs; other classes need empty IDs.\n"
        "- content_inventory.json: schema_version=1, items[], conflicts[]. Each item has id, "
        "time_range, category, statement, importance(required|optional), qualifier, source_refs[], "
        "official_verification(required|not_applicable). Conflicts use id,description,source_refs for both sides.\n"
        f"- reader-facing document blueprint document_blueprint.json: schema_version=1, status=completed, "
        f"document_archetype({blueprint_archetypes}), document_type, reader_goal, "
        f"writing_style({blueprint_writing_styles}), front_matter[], "
        f"sections[]. Required front-matter keys are {front_matter_keys}; each entry has key,label,value "
        "and its `- label: value` line must be verbatim in minutes.md. Each section has id, heading, "
        f"role({blueprint_roles}), form_factor({blueprint_forms}), applicability(required|not_applicable), "
        "primary_inventory_item_ids[] and rationale when not applicable. H2 order must equal section "
        "order; assign every required item to exactly one primary section; use exactly one "
        "executive_synthesis and open_questions, operational_actions when actionable evidence exists, "
        "and <=6 topic_analysis H2s. grouped_bullets needs >=3 bullets; table and timeline "
        "requires a Markdown table; checklist needs >=3 entries; definition_list requires >=2 "
        "labeled `label: value` lines; source_list needs a real Markdown link; mixed needs two of "
        "prose/bullets/table/image. The final two H2s: `Items Requiring Further Verification`, "
        "`External Evidence Check` (English) or `추가 검증이 필요한 항목`, `외부 근거 확인` "
        "(Korean); keep both if not_applicable.\n"
        "  visual_evidence_plan{status,rationale,items[{snapshot_path,section_id,purpose,reader_value}]}; "
        "embedded=3-5 core images; limited only if <3; Markdown order, <=2/H2, no adjacent "
        "full-width images, with content after the last.\n"
        "  Style: meeting_minutes uses writing_style=meeting_minutes_objective; capture agenda, "
        "discussion, decisions, owners/deadlines, follow-ups/risks, not dialogue. Korean endings: "
        "~함, ~하기로 함, ~예정임, ~필요함. Other types use "
        "writing_style=content_adaptive.\n"
        "  Front matter is reader metadata, not a production log: require date/duration; optional "
        "purpose/participants; forbid language/evidence-policy, model/skill/token, "
        "preprocess/render/QA, hashes, and internal paths.\n"
        f"- content_audit.json exact model-owned shape: {_content_audit_contract_fragment()} "
        "documented_conflict_ids covers every conflict; covered_item_ids plus intentional_omissions "
        "disposes every inventory item; each intentional omission is {item_id,reason}; "
        "recording_fidelity.preserved_item_ids covers covered items.\n"
        "Do not expose raw STT/OCR/Snapshot refs anywhere. Omit internal "
        "artifacts/paths/hashes, model/tool/token use, preprocessing, render attempts, or QA mechanics; "
        "use natural image captions.\n"
        f"- content_quality_review.json schema_version=3 exact model-owned shape: "
        f"{_quality_review_contract_fragment()} "
        "final_checks is an object, never a list. "
        "required_item_checks uses item_id,section_id,dimensions; never `checks`, inventory_item_id, "
        f"or primary_section_id. dimensions exactly {required_item_dimensions}; core_facts=covered; "
        "others covered+verbatim ref or N/A+rationale. Each covered ref is an exact substring of that "
        "primary H2. For repair, the revised cycle owns nonempty findings, changes, and "
        "target_section_ids; cycle 2 is a clean pass. LOW_INFORMATION_DENSITY keeps "
        "content_density_baseline.json and its validator-selected substantive targets/minimum gains. "
        "No character maximum; use additive edits preserving refs. Do not patch review_cycles; "
        "content_freeze writes deterministic revised/pass cycles. Structural validity alone is not a quality pass.\n"
        "- Auto checks unresolved public support/version/release/EOL/policy/security/API claims; a "
        "presenter estimate is no exemption. official_sources exact model-owned shape: "
        f"{_official_sources_contract_fragment()}. In auto mode current_fact_check is forbidden. "
        "Category must match purpose; completed claims require an exact H3, body/appendix refs, "
        "and an official URL shown in the appendix. Final section gives checked date, recording-first "
        "and non-transmission disclosures without pipeline details.\n\n"
        "Complete only content artifacts. Do not delete, move, recreate, or hash raw frames/Snapshots; "
        "validators own frame accounting. Preserve useful Snapshots and verify all Markdown Snapshot "
        "refs resolve. Do not treat raw Snapshot count as the evidence-coverage gate. Follow "
        "ledger/inventory → blueprint → sources → minutes → audit/quality review → content "
        "freeze. Create large artifacts once in one multi-file apply_patch; no reread or custom "
        "precheck. Run exactly "
        f"`{freeze_command}`; it fills deterministic bindings, reruns all content gates, and writes "
        "content_freeze.json. Do not create, edit, render, or inspect a DOCX; do not archive or mark "
        "completed. If freeze fails, stop and return only its bounded error. Do not inspect or patch "
        "artifacts, rerun freeze, or reread evidence after that failure; the launcher has already "
        "checkpointed the completed content turn and will start one isolated sidecar-only repair. "
        "Do not open validator code or print/reread full artifacts or diffs.\n"
    )


def build_content_repair_prompt(
    repo_root: Path,
    job_dir: Path,
    *,
    validation_error: str,
) -> str:
    python_executable = repo_root / ".venv/bin/python"
    patch_script = repo_root / "scripts/apply_content_repair_patch.py"
    freeze_script = repo_root / "scripts/content_freeze.py"
    patch_command = " ".join(
        shlex.quote(str(value))
        for value in (python_executable, patch_script, job_dir)
    )
    freeze_command = " ".join(
        shlex.quote(str(value))
        for value in (python_executable, freeze_script, job_dir)
    )
    error = _bounded_text(validation_error, max_chars=5_000)
    return (
        "Fresh content-repair worker; one bounded schema/section repair after a completed content "
        "turn. Do not invoke a skill, use web search, or launch another worker.\n\n"
        f"Repository: {repo_root}\nJob directory: {job_dir}\n"
        f"Deterministic validation error (bounded): {error}\n\n"
        "Do not read raw evidence, codex_minutes_input.md, evidence chunks/coverage, transcript, "
        "OCR, screen text, snapshots, frames, media, worker metrics, validator source, tests, git "
        "diffs, or instruction files. The recording analysis is complete. Inspect only the named "
        "entries in content_inventory.json, document_blueprint.json, official_sources.json, "
        "content_audit.json, content_quality_review.json and only an exact implicated H2 excerpt "
        "from minutes.md. Keep every command result <=16KB; never concatenate or print a whole large "
        "sidecar. Do not direct-edit minutes.md or any JSON sidecar.\n\n"
        "Write only content_repair_patch.json with apply_patch. Exact patch shape: "
        '{"schema_version":1,"json_updates":[{"file":"content_audit.json|'
        'content_quality_review.json|document_blueprint.json|official_sources.json",'
        '"path":["model_owned_field",0,"nested_key"],"value":"replacement JSON value"}],'
        '"markdown_replacements":[{"old":"unique exact minutes text","new":'
        '"evidence-preserving replacement","expected_count":1}]}. '
        "Use an empty list for the unused update type. Keep the patch minimal; do not touch "
        "validator-owned bindings, document_signals, hashes, reviewed chunks, density baselines, "
        "inventory, or ledger. Existing intermediate JSON path components must exist; a missing "
        "top-level model-owned field may be added.\n\n"
        f"Canonical audit shape: {_content_audit_contract_fragment()}\n"
        f"Canonical quality-review shape: {_quality_review_contract_fragment()}\n"
        f"Canonical official-source shape: {_official_sources_contract_fragment()}\n"
        "Blueprint form gate: grouped_bullets >=3 bullets; table/timeline uses a Markdown table; "
        "checklist >=3 entries; definition_list >=2 labeled `label: value` lines; source_list has "
        "a Markdown link; mixed has two distinct forms.\n\n"
        f"Run the patch helper exactly once: `{patch_command}`. It validates all operations before "
        "writing and emits only counts, never a diff. If it fails, stop. Run content_freeze.py "
        f"exactly once after the helper: `{freeze_command}`. If freeze fails, report the bounded "
        "error and stop; do not write another patch or rerun any model phase. Do not create a DOCX.\n"
    )


def build_delivery_prompt(
    repo_root: Path,
    job_dir: Path,
    *,
    policy: Mapping[str, object],
) -> str:
    freeze_path = job_dir / CONTENT_FREEZE_NAME
    final_output_language = str(policy.get("output_language", "auto"))
    detected_language = str(policy.get("detected_language", "unknown"))
    needs_translation = translation_required(
        final_output_language,
        detected_language,
    )
    minutes_path = (
        job_dir / TRANSLATED_MINUTES_NAME
        if needs_translation
        else job_dir / "minutes.md"
    )
    translation_manifest_path = job_dir / TRANSLATION_MANIFEST_NAME
    blueprint_path = job_dir / "document_blueprint.json"
    python_executable = repo_root / ".venv/bin/python"

    def worker_command(script_name: str, *arguments: object) -> str:
        values = (
            python_executable,
            repo_root / "scripts" / script_name,
            *arguments,
        )
        return " ".join(shlex.quote(str(value)) for value in values)

    freeze_verify_command = worker_command("content_freeze.py", job_dir, "--verify")
    translation_verify_command = worker_command(
        "translation.py", job_dir, "--verify"
    )
    prepare_command = worker_command("finalize_docx.py", "prepare", job_dir)
    reuse_command = worker_command(
        "finalize_docx.py", "prepare", job_dir, "--reuse-final"
    )
    approve_command = worker_command("finalize_docx.py", "approve", job_dir)
    archive_command = worker_command("archive_job.py", job_dir)
    translation_contract = (
        f"Run `{translation_verify_command}` once, then use only "
        f"the validated final Markdown path at {minutes_path} without printing the file. Do not "
        "translate, polish, summarize, or "
        "compare it with raw evidence in this phase."
        if needs_translation
        else "No translation is required; use the frozen source Markdown as the final Markdown."
    )
    return (
        "Fresh-context visual delivery worker for one content-frozen job. This prompt is the "
        "preloaded compact delivery contract. Do not invoke a skill. Do not open any SKILL.md or "
        "instruction/reference file. `finalize_docx.py` already fills the bundled retained Word "
        "template and invokes the Documents renderer deterministically; do not reconstruct the layout.\n\n"
        "This is a new ephemeral execution with no parent or content-worker conversation. "
        "Do not launch run_fresh_codex_job.py again.\n\n"
        f"Repository: {repo_root}\n"
        f"Job directory: {job_dir}\n"
        f"Content freeze: {freeze_path}\n"
        f"Final Markdown: {minutes_path}\n"
        f"Translation manifest: {translation_manifest_path if needs_translation else 'not required'}\n"
        f"Document blueprint: {blueprint_path}\n"
        f"FINAL_OUTPUT_LANGUAGE={final_output_language}\n"
        f"TRANSLATION_REQUIRED={'true' if needs_translation else 'false'}\n\n"
        "This phase performs Word finalization, all-page visual QA, archive, and final verification "
        "only. Never read codex_minutes_input.md, evidence_chunks.json, transcript, OCR, evidence "
        "ledger, content inventory, content audit, or raw evidence. Deterministic validators consume "
        "those files without returning them to you. Do not print the full final Markdown or DOCX "
        "XML. Read only bounded content_freeze.json fields and document_blueprint.json once. Run "
        f"`{freeze_verify_command}` before Word work. "
        "Never concatenate files in one command. Keep each command/read output at or below 16KB; a "
        "24KB command/read result terminates this phase. This console limit never limits final Markdown, "
        "DOCX, or file-change size. "
        "Do not recursively list the job directory, frames, snapshots, or render directory; use only "
        "the exact paths and commands named by this contract. "
        f"Target <={TOOL_ROUND_TRIP_TARGETS['delivery']} tool "
        "calls without skipping all-page QA.\n\n"
        f"{translation_contract}\n\n"
        "The frozen Markdown is immutable, and the validated final Markdown is immutable. It has no "
        "document or section character maximum. Do not "
        "rewrite, shorten, translate, polish, or reorganize either file for pagination. "
        "If you find a genuine semantic blocker, write semantic_blocker.json with the exact section "
        "and reason, then fail without archiving. Otherwise use only layout-preserving DOCX edits.\n\n"
        f"Run `{prepare_command}` once. It copies the retained template, fills its cover/TOC/body slots, "
        "creates the draft and final DOCX, cleans the render directory, renders every page once, and runs "
        "structural QA in one bounded command. Inspect every latest page PNG at 100% zoom. Deterministic and visual "
        "blocking defects are: "
        "clipped or overlapping text, missing glyph/content, blank interior page, broken TOC or "
        "bookmark, unreadable table, incorrect list numbering, orphan heading or split row, "
        "excessive interior layout gap, adjacent large images, or image placement drift. A naturally short "
        "final page is NATURAL_FINAL_PAGE_WHITESPACE, a nonblocking warning, alongside single-page TOC "
        "whitespace, intentional section whitespace, and mild readable wrapping. Never reflow or add filler "
        "merely to fill the final page; do not revise for warnings alone.\n\n"
        "If a blocking defect exists, edit only minutes.final.docx and run "
        f"`{reuse_command}` once. A third render "
        "is forbidden unless a blocking defect remains; then pass its exact supported code through "
        "--blocking-defect-code. Do not increase paragraph spacing, insert blank lines, or move content "
        "downward to manipulate page occupancy. Preserve all complete, evidence-backed content. "
        "Write visual_review.json with schema_version=1, status=passed, the "
        "complete ordered inspected_pages list, empty blocking_defects, and warnings using only the "
        f"documented nonblocking codes. Run `{approve_command}`; "
        f"the command adds hashes and creates passed docx_qa.json. Then run `{archive_command}` and verify "
        "status.json is completed and final artifacts pass. Use defect codes, hashes, counts, "
        "and targeted excerpts only.\n"
    )


def build_translation_prompt(
    source_markdown: str,
    *,
    target_language: str,
) -> str:
    target_label = target_language_label(target_language)
    metadata_value = "한국어" if target_label == "Korean" else "English"
    meeting_style_contract = (
        "If the document is meeting minutes, use concise objective Korean report style with "
        "consistent endings such as ~함, ~하기로 함, ~예정임, and ~필요함; summarize rather "
        "than recreate dialogue. "
        if target_label == "Korean"
        else "If the document is meeting minutes, keep a concise objective minutes voice and "
        "summarize rather than recreate dialogue. "
    )
    return (
        f"Translate the complete Markdown document below into natural professional {target_label}.\n"
        "Return only the translated Markdown as the final response: no preamble, no code fence, "
        "no commentary, and no tool calls. This is one translation pass, not a new analysis.\n\n"
        "Preserve the exact Markdown structure and order: heading levels, tables and cell counts, "
        "list types, checklist states, image paths, link URLs, inline-code literals, evidence "
        "references, timestamps, numeric values, units, product names, acronyms, and code. Translate "
        "the title, headings, prose, table labels/cells, captions, and reader metadata labels naturally. "
        f"{meeting_style_contract}Do not add source/output-language, model, skill, token, preprocessing, "
        "rendering, QA, hash, internal-path, or other production metadata. If a legacy source already "
        f"contains an Output language/출력 언어 line, translate its value to {metadata_value}. When present, render the final "
        f"trust headings exactly as `## {'추가 검증이 필요한 항목' if target_label == 'Korean' else 'Items Requiring Further Verification'}` "
        f"then `## {'외부 근거 확인' if target_label == 'Korean' else 'External Evidence Check'}`. "
        "Do not summarize, omit, add, "
        "fact-check, reinterpret, or polish beyond natural target-language phrasing.\n\n"
        "--- BEGIN SOURCE MARKDOWN ---\n"
        f"{source_markdown.rstrip()}\n"
        "--- END SOURCE MARKDOWN ---\n"
    )


def build_fresh_codex_command(
    codex_binary: str,
    repo_root: Path,
    minutes_home: Path,
    last_message_path: Path,
    *,
    model: str | None = None,
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
) -> list[str]:
    command = [
        codex_binary,
        "exec",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(repo_root),
        "--add-dir",
        str(minutes_home),
        "--output-last-message",
        str(last_message_path),
        "--json",
        "--color",
        "never",
    ]
    if model:
        command.extend(["--model", model])
    command.extend(["--config", WORKER_DOCUMENTS_PLUGIN_CONFIG])
    if reasoning_effort:
        command.extend(
            ["--config", f'model_reasoning_effort="{reasoning_effort}"']
        )
    command.append("-")
    return command


def phase_artifact_paths(job_dir: Path, phase: str) -> dict[str, Path]:
    if phase not in SUPPORTED_FRESH_PHASES:
        raise ValueError(f"unsupported fresh Codex phase: {phase}")
    prefix = f"fresh_codex_{phase}"
    return {
        "last_message": (
            job_dir / TRANSLATED_MINUTES_NAME
            if phase == "translation"
            else job_dir / f"{prefix}_last_message.txt"
        ),
        "events": job_dir / f"{prefix}_events.jsonl",
        "stderr": job_dir / f"{prefix}_stderr.log",
    }


def aggregate_phase_records(
    phase_records: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    usage = {key: 0 for key in USAGE_KEYS}
    item_counts: dict[str, int] = {}
    tool_count = 0
    tool_output_bytes = 0
    max_tool_output_bytes = 0
    oversized_tool_output_count = 0
    tool_output_budget_violations: list[dict[str, object]] = []
    artifact_change_bytes = 0
    max_artifact_change_bytes = 0
    large_artifact_change_count = 0
    forbidden_instruction_read_count = 0
    forbidden_instruction_reads: list[dict[str, object]] = []
    forbidden_artifact_write_count = 0
    forbidden_artifact_writes: list[dict[str, object]] = []
    evidence_chunk_read_count = 0
    duplicate_evidence_chunk_read_count = 0
    duplicate_evidence_chunk_reads: list[dict[str, object]] = []
    elapsed_seconds = 0.0
    for phase in SUPPORTED_FRESH_PHASES:
        record = phase_records.get(phase, {})
        if not isinstance(record, Mapping) or record.get("state") in {"reused", "skipped"}:
            continue
        elapsed = record.get("elapsed_seconds", 0.0)
        if isinstance(elapsed, (int, float)):
            elapsed_seconds += float(elapsed)
        phase_usage = record.get("token_usage", {})
        if isinstance(phase_usage, Mapping):
            for key in USAGE_KEYS:
                value = phase_usage.get(key, 0)
                if isinstance(value, int):
                    usage[key] += value
        value = record.get("tool_count", 0)
        if isinstance(value, int):
            tool_count += value
        phase_items = record.get("item_counts", {})
        if isinstance(phase_items, Mapping):
            for name, count in phase_items.items():
                if isinstance(name, str) and isinstance(count, int):
                    item_counts[name] = item_counts.get(name, 0) + count
        efficiency = record.get("context_efficiency", {})
        if isinstance(efficiency, Mapping):
            value = efficiency.get("tool_output_bytes", 0)
            if isinstance(value, int):
                tool_output_bytes += value
            value = efficiency.get("max_tool_output_bytes", 0)
            if isinstance(value, int):
                max_tool_output_bytes = max(max_tool_output_bytes, value)
            value = efficiency.get("oversized_tool_output_count", 0)
            if isinstance(value, int):
                oversized_tool_output_count += value
            violations = efficiency.get("tool_output_budget_violations", [])
            if isinstance(violations, list):
                for violation in violations:
                    if not isinstance(violation, Mapping):
                        continue
                    tool_output_budget_violations.append(
                        {"phase": phase, **dict(violation)}
                    )
            value = efficiency.get("artifact_change_bytes", 0)
            if isinstance(value, int):
                artifact_change_bytes += value
            value = efficiency.get("max_artifact_change_bytes", 0)
            if isinstance(value, int):
                max_artifact_change_bytes = max(max_artifact_change_bytes, value)
            value = efficiency.get("large_artifact_change_count", 0)
            if isinstance(value, int):
                large_artifact_change_count += value
            value = efficiency.get("forbidden_instruction_read_count", 0)
            if isinstance(value, int):
                forbidden_instruction_read_count += value
            reads = efficiency.get("forbidden_instruction_reads", [])
            if isinstance(reads, list):
                for read in reads:
                    if not isinstance(read, Mapping):
                        continue
                    forbidden_instruction_reads.append(
                        {"phase": phase, **dict(read)}
                    )
            value = efficiency.get("forbidden_artifact_write_count", 0)
            if isinstance(value, int):
                forbidden_artifact_write_count += value
            writes = efficiency.get("forbidden_artifact_writes", [])
            if isinstance(writes, list):
                for write in writes:
                    if not isinstance(write, Mapping):
                        continue
                    forbidden_artifact_writes.append(
                        {"phase": phase, **dict(write)}
                    )
            value = efficiency.get("evidence_chunk_read_count", 0)
            if isinstance(value, int):
                evidence_chunk_read_count += value
            value = efficiency.get("duplicate_evidence_chunk_read_count", 0)
            if isinstance(value, int):
                duplicate_evidence_chunk_read_count += value
            reads = efficiency.get("duplicate_evidence_chunk_reads", [])
            if isinstance(reads, list):
                for read in reads:
                    if not isinstance(read, Mapping):
                        continue
                    duplicate_evidence_chunk_reads.append(
                        {"phase": phase, **dict(read)}
                    )
    cached = min(usage["input_tokens"], usage["cached_input_tokens"])
    return {
        "active_codex_elapsed_seconds": round(elapsed_seconds, 3),
        "token_usage": usage,
        "tool_count": tool_count,
        "item_counts": dict(sorted(item_counts.items())),
        "context_efficiency": {
            "uncached_input_tokens": usage["input_tokens"] - cached,
            "cached_input_ratio": (
                round(cached / usage["input_tokens"], 6)
                if usage["input_tokens"]
                else 0.0
            ),
            "input_to_output_ratio": (
                round(usage["input_tokens"] / usage["output_tokens"], 3)
                if usage["output_tokens"]
                else None
            ),
            "tool_output_bytes": tool_output_bytes,
            "max_tool_output_bytes": max_tool_output_bytes,
            "oversized_tool_output_count": oversized_tool_output_count,
            "oversized_tool_output_threshold_bytes": OVERSIZED_TOOL_OUTPUT_BYTES,
            "tool_output_budget_passed": oversized_tool_output_count == 0,
            "tool_output_budget_violations": tool_output_budget_violations[
                :MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
            ],
            "artifact_change_bytes": artifact_change_bytes,
            "max_artifact_change_bytes": max_artifact_change_bytes,
            "large_artifact_change_count": large_artifact_change_count,
            "large_artifact_change_threshold_bytes": OVERSIZED_TOOL_OUTPUT_BYTES,
            "forbidden_instruction_read_count": forbidden_instruction_read_count,
            "forbidden_instruction_reads": forbidden_instruction_reads[
                :MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
            ],
            "forbidden_artifact_write_count": forbidden_artifact_write_count,
            "forbidden_artifact_writes": forbidden_artifact_writes[
                :MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
            ],
            "evidence_chunk_read_count": evidence_chunk_read_count,
            "duplicate_evidence_chunk_read_count": (
                duplicate_evidence_chunk_read_count
            ),
            "duplicate_evidence_chunk_reads": duplicate_evidence_chunk_reads[
                :MAX_RECORDED_TOOL_OUTPUT_VIOLATIONS
            ],
            "worker_contract_passed": (
                oversized_tool_output_count == 0
                and forbidden_instruction_read_count == 0
                and forbidden_artifact_write_count == 0
                and duplicate_evidence_chunk_read_count == 0
            ),
        },
    }


def run_fresh_phase(
    phase: str,
    *,
    command: Sequence[str],
    prompt: str,
    env: Mapping[str, str],
    job_dir: Path,
    progress_stream: TextIO,
) -> tuple[int, CodexStreamSummary, dict[str, object]]:
    paths = phase_artifact_paths(job_dir, phase)
    started = time.perf_counter()
    print(f"fresh Codex {phase}: starting", file=progress_stream, flush=True)
    returncode, summary = run_codex_json_stream(
        command,
        prompt=prompt,
        env=env,
        events_path=paths["events"],
        stderr_path=paths["stderr"],
        progress_stream=progress_stream,
        forbidden_command_markers=(
            CONTENT_REPAIR_FORBIDDEN_COMMAND_MARKERS
            if phase == "content_repair"
            else ()
        ),
        allowed_file_change_names=(
            (CONTENT_REPAIR_PATCH_NAME,)
            if phase == "content_repair"
            else None
        ),
    )
    elapsed_seconds = round(time.perf_counter() - started, 3)
    record = {
        "state": "completed" if returncode == 0 else "failed",
        "elapsed_seconds": elapsed_seconds,
        "prompt_bytes": len(prompt.encode("utf-8")),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "returncode": returncode,
        "events": file_record(paths["events"]),
        "stderr_log": file_record(paths["stderr"]),
        "last_message_path": str(paths["last_message"]),
        **summary.manifest_fields(),
    }
    target = TOOL_ROUND_TRIP_TARGETS[phase]
    record["tool_round_trip_target"] = target
    efficiency = record.get("context_efficiency")
    if isinstance(efficiency, dict):
        efficiency["tool_round_trip_count"] = len(summary.tool_item_ids)
        efficiency["tool_round_trip_target"] = target
        efficiency["tool_round_trip_target_met"] = (
            len(summary.tool_item_ids) <= target
        )
    print(
        f"fresh Codex {phase}: completed in {elapsed_seconds:.3f}s",
        file=progress_stream,
        flush=True,
    )
    return returncode, summary, record


def verify_completed_job(job_dir: Path) -> dict[str, object]:
    status = read_json(job_dir / "status.json")
    if status.get("status") != "completed":
        raise RuntimeError(
            "fresh Codex exited without completing the job; "
            f"status is {status.get('status')!r}"
        )
    output_dir = Path(str(status.get("output_dir", ""))).expanduser()
    if not output_dir.is_dir():
        raise RuntimeError(f"completed job output directory is missing: {output_dir}")
    freeze_path = job_dir / CONTENT_FREEZE_NAME
    if freeze_path.is_file():
        from scripts.content_freeze import validate_content_freeze

        validate_content_freeze(job_dir, revalidate_content=False)
    files = status.get("files", {})
    if not isinstance(files, Mapping):
        raise RuntimeError("completed job status has no files mapping")
    for required in ("source", "minutes"):
        path = Path(str(files.get(required, ""))).expanduser()
        if not path.is_file():
            raise RuntimeError(f"completed job is missing {required}: {path}")
    handoff = status.get("codex_handoff", {})
    if not isinstance(handoff, Mapping):
        handoff = {}
    if translation_required(
        str(handoff.get("output_language", "auto")),
        str(handoff.get("detected_language", "unknown")),
    ):
        validate_translation_manifest(job_dir)
    if handoff.get("docx_enabled", False):
        docx_path = Path(str(files.get("docx", ""))).expanduser()
        if not docx_path.is_file():
            raise RuntimeError(f"completed job is missing docx: {docx_path}")
        docx_qa_path = Path(str(files.get("docx_qa", ""))).expanduser()
        if not docx_qa_path.is_file():
            raise RuntimeError(f"completed job is missing docx QA: {docx_qa_path}")
        from scripts.docx_qa import validate_docx_qa

        minutes_path = Path(str(files.get("minutes", ""))).expanduser()
        validate_docx_qa(
            minutes_path,
            docx_path,
            docx_qa_path,
            require_visual=True,
            require_visual_review=freeze_path.is_file(),
        )
    if int(handoff.get("selected_snapshot_count", 0)) > 0:
        snapshots_path = Path(str(files.get("snapshots", ""))).expanduser()
        if not snapshots_path.is_dir() or not any(snapshots_path.glob("*.jpg")):
            raise RuntimeError(
                f"completed job is missing selected snapshots: {snapshots_path}"
            )
    return status


def reset_content_generation(job_dir: Path) -> None:
    derived_names = {
        *CONTENT_GENERATED_ARTIFACT_NAMES,
        CONTENT_FREEZE_NAME,
        CONTENT_CHECKPOINT_NAME,
        CONTENT_REPAIR_PATCH_NAME,
        "content_review_patch.json",
        TRANSLATED_MINUTES_NAME,
        TRANSLATION_MANIFEST_NAME,
    }
    for name in derived_names:
        (job_dir / name).unlink(missing_ok=True)


def ensure_content_freeze(
    job_dir: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    from scripts.content_freeze import create_content_freeze, validate_content_freeze

    freeze_path = job_dir / CONTENT_FREEZE_NAME
    if freeze_path.is_file():
        try:
            return validate_content_freeze(job_dir), None
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    try:
        return create_content_freeze(job_dir), None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, _bounded_text(exc, max_chars=12_000)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Complete one prepared minutes job in a clean ephemeral Codex context."
    )
    parser.add_argument("job_dir", help="Prepared ~/minutes/jobs/<job_id> directory")
    parser.add_argument(
        "--request",
        action="append",
        default=[],
        help="Short per-job instruction not already encoded in the job (repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate and print a bounded handoff summary without prompt or evidence "
            "bodies, then exit"
        ),
    )
    parser.add_argument(
        "--model",
        help="Optional Codex model override for a controlled quality comparison",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=DEFAULT_REASONING_EFFORT,
        help="Optional reasoning-effort override for a controlled quality comparison",
    )
    parser.add_argument(
        "--translation-reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=DEFAULT_TRANSLATION_REASONING_EFFORT,
        help="Reasoning effort for the single translation-only turn",
    )
    parser.add_argument(
        "--force-content-rebuild",
        action="store_true",
        help=(
            "Explicitly discard generated content/checkpoint artifacts and reread raw "
            "evidence; required after the single bounded repair is exhausted"
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = Path(__file__).resolve().parents[1]
    parent_env = dict(os.environ)
    ensure_not_nested(parent_env)
    minutes_home = resolve_configured_path(
        repo_root,
        "MINUTES_HOME",
        environ=parent_env,
    )
    jobs_dir = minutes_home / "jobs"
    job_dir = validate_job_dir(Path(args.job_dir), jobs_dir)
    status = read_json(job_dir / "status.json")
    policy = job_policy(status)
    needs_translation = bool(policy.get("translation_required", False))
    planned_phases = (
        FRESH_PHASES
        if needs_translation
        else ("content", "delivery")
    )
    write_worker_evidence_summary(job_dir)
    write_worker_evidence_chunks(job_dir)
    write_worker_runtime_summary(job_dir)
    evidence = collect_evidence_manifest(job_dir)
    request_overrides = normalize_request_overrides(args.request)
    content_prompt = build_fresh_prompt(
        repo_root,
        job_dir,
        policy=policy,
        request_overrides=request_overrides,
    )
    delivery_prompt = build_delivery_prompt(repo_root, job_dir, policy=policy)
    codex_binary = shutil.which("codex", path=parent_env.get("PATH"))
    if codex_binary is None:
        raise SystemExit("error: codex executable was not found on PATH")
    commands: dict[str, list[str]] = {}
    command_phases = tuple(dict.fromkeys((*planned_phases, "content_repair")))
    for phase in command_phases:
        paths = phase_artifact_paths(job_dir, phase)
        commands[phase] = build_fresh_codex_command(
            codex_binary,
            repo_root,
            minutes_home,
            paths["last_message"],
            model=args.model,
            reasoning_effort=(
                args.translation_reasoning_effort
                if phase == "translation"
                else args.reasoning_effort
            ),
        )
    manifest_path = job_dir / "fresh_codex_handoff.json"
    manifest: dict[str, object] = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "state": "dry_run" if args.dry_run else "running",
        "created_at": now_local().isoformat(),
        "job_dir": str(job_dir),
        "policy": policy,
        "request_overrides": request_overrides,
        "ephemeral_session": True,
        "planned_ephemeral_session_count": len(planned_phases),
        "maximum_ephemeral_session_count": len(planned_phases) + 1,
        "planned_phases": list(planned_phases),
        "optional_phases": ["content_repair"],
        "phase_isolation": {
            "content_reads_raw_evidence": True,
            "content_repair_reads_raw_evidence": False,
            "content_repair_uses_bounded_patch_helper": True,
            "translation_reads_raw_evidence": False,
            "translation_reads_only_frozen_markdown": needs_translation,
            "delivery_reads_raw_evidence": False,
            "delivery_requires_content_freeze": True,
            "delivery_requires_translation_manifest": needs_translation,
        },
        "worker_contract": {
            "mode": "preloaded_compact",
            "worker_skill_file_reads_required": False,
            "documents_renderer": "bundled_documents_skill_render_docx.py",
            "documents_skill_plugin_enabled_in_worker": False,
            "tool_output_target_bytes": 16_000,
            "tool_output_hard_limit_bytes": OVERSIZED_TOOL_OUTPUT_BYTES,
            "hard_limit_action": "terminate_phase",
            "stream_heartbeat_seconds": CODEX_STREAM_HEARTBEAT_SECONDS,
            "stream_stall_timeout_seconds": CODEX_STREAM_STALL_TIMEOUT_SECONDS,
            "stream_stall_exit_code": CODEX_STALL_EXIT_CODE,
            "tool_round_trip_targets": {
                phase: TOOL_ROUND_TRIP_TARGETS[phase]
                for phase in command_phases
            },
        },
        "parent_conversation_inherited": False,
        "raw_evidence_embedded_in_handoff": False,
        "handoff_prompt_bytes": {
            "content": len(content_prompt.encode("utf-8")),
            "content_repair": None,
            "translation": None,
            "delivery": len(delivery_prompt.encode("utf-8")),
        },
        "handoff_prompt_sha256": {
            "content": hashlib.sha256(content_prompt.encode("utf-8")).hexdigest(),
            "content_repair": None,
            "translation": None,
            "delivery": hashlib.sha256(delivery_prompt.encode("utf-8")).hexdigest(),
        },
        "evidence": evidence,
        "runtime": configured_codex_runtime(
            parent_env,
            model_override=args.model,
            reasoning_effort_override=args.reasoning_effort,
        ),
        "translation_runtime": (
            configured_codex_runtime(
                parent_env,
                model_override=args.model,
                reasoning_effort_override=args.translation_reasoning_effort,
            )
            if needs_translation
            else None
        ),
        "commands": {
            phase: command[:-1] + ["<prompt-via-stdin>"]
            for phase, command in commands.items()
        },
        "phases": {},
    }

    if args.dry_run:
        print(
            json.dumps(
                build_dry_run_summary(manifest),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    write_json(manifest_path, manifest)
    child_env = dict(parent_env)
    child_env[FRESH_CONTEXT_ENV] = "1"
    child_env["MINUTES_JOB_DIR"] = str(job_dir)
    started = time.perf_counter()
    phase_records: dict[str, Mapping[str, object]] = {}
    phase_checkpoints: list[dict[str, object]] = []
    try:
        freeze_path = job_dir / CONTENT_FREEZE_NAME
        if args.force_content_rebuild:
            reset_content_generation(job_dir)
        content_action = select_content_action(
            job_dir,
            content_prompt=content_prompt,
            force_content_rebuild=args.force_content_rebuild,
        )
        if content_action == "checkpoint_invalid":
            raise RuntimeError(
                "content generation checkpoint is invalid; use "
                "--force-content-rebuild for an explicit evidence reread"
            )
        if content_action == "checkpoint_mismatch":
            raise RuntimeError(
                "content prompt changed after the completed content turn; use "
                "--force-content-rebuild for an explicit evidence reread"
            )
        if content_action == "repair_exhausted":
            raise RuntimeError(
                "the single bounded content repair is exhausted; refusing to repeat the "
                "full content model turn automatically. Inspect the checkpoint or use "
                "--force-content-rebuild"
            )

        if content_action == "reuse_freeze":
            phase_records["content"] = {
                "state": "reused",
                "content_freeze": file_record(freeze_path),
                "elapsed_seconds": 0.0,
            }
            phase_checkpoints.append(
                {
                    "phase": "content_reused",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
        elif content_action == "run_content":
            phase_checkpoints.append(
                {
                    "phase": "content_started",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
            returncode, _content_summary, content_record = run_fresh_phase(
                "content",
                command=commands["content"],
                prompt=content_prompt,
                env=child_env,
                job_dir=job_dir,
                progress_stream=sys.stderr,
            )
            phase_records["content"] = content_record
            manifest["phases"] = dict(phase_records)
            write_json(manifest_path, manifest)
            if returncode != 0:
                if returncode == TOOL_OUTPUT_BUDGET_EXIT_CODE:
                    raise RuntimeError(
                        "fresh Codex content phase violated the worker output/contract guard"
                    )
                if returncode == CODEX_STALL_EXIT_CODE:
                    raise RuntimeError(
                        "fresh Codex content phase stalled for 15 minutes without JSON events"
                    )
                raise RuntimeError(
                    f"fresh Codex content phase exited with status {returncode}"
                )
            phase_checkpoints.append(
                {
                    "phase": "content_completed",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
            write_content_generation_checkpoint(
                job_dir,
                content_prompt=content_prompt,
                state="awaiting_validation",
                repair_attempts=0,
            )
        else:
            checkpoint_path = job_dir / CONTENT_CHECKPOINT_NAME
            phase_records["content"] = {
                "state": "reused",
                "reason": "completed content turn checkpoint",
                "content_checkpoint": file_record(checkpoint_path),
                "elapsed_seconds": 0.0,
            }
            phase_checkpoints.append(
                {
                    "phase": "content_checkpoint_reused",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )

        freeze, validation_error = ensure_content_freeze(job_dir)
        if freeze is None:
            assert validation_error is not None
            checkpoint_status = inspect_content_generation_checkpoint(
                job_dir,
                content_prompt=content_prompt,
            )
            repair_attempts = int(checkpoint_status.get("repair_attempts", 0))
            if repair_attempts >= MAX_CONTENT_REPAIR_ATTEMPTS:
                raise RuntimeError(
                    "the single bounded content repair is exhausted; refusing to repeat "
                    "the full content model turn automatically"
                )
            write_content_generation_checkpoint(
                job_dir,
                content_prompt=content_prompt,
                state="awaiting_repair",
                repair_attempts=repair_attempts,
                validation_error=validation_error,
            )
            repair_prompt = build_content_repair_prompt(
                repo_root,
                job_dir,
                validation_error=validation_error,
            )
            repair_prompt_bytes = repair_prompt.encode("utf-8")
            manifest["handoff_prompt_bytes"]["content_repair"] = len(
                repair_prompt_bytes
            )
            manifest["handoff_prompt_sha256"]["content_repair"] = hashlib.sha256(
                repair_prompt_bytes
            ).hexdigest()
            write_content_generation_checkpoint(
                job_dir,
                content_prompt=content_prompt,
                state="repair_running",
                repair_attempts=repair_attempts + 1,
                validation_error=validation_error,
            )
            phase_checkpoints.append(
                {
                    "phase": "content_repair_started",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
            returncode, _repair_summary, repair_record = run_fresh_phase(
                "content_repair",
                command=commands["content_repair"],
                prompt=repair_prompt,
                env=child_env,
                job_dir=job_dir,
                progress_stream=sys.stderr,
            )
            phase_records["content_repair"] = repair_record
            manifest["phases"] = dict(phase_records)
            write_json(manifest_path, manifest)
            if returncode != 0:
                write_content_generation_checkpoint(
                    job_dir,
                    content_prompt=content_prompt,
                    state="repair_failed",
                    repair_attempts=repair_attempts + 1,
                    validation_error=(
                        "content repair phase violated the output/command guard"
                        if returncode == TOOL_OUTPUT_BUDGET_EXIT_CODE
                        else "content repair phase stalled"
                        if returncode == CODEX_STALL_EXIT_CODE
                        else f"content repair phase exited with status {returncode}"
                    ),
                )
                if returncode == TOOL_OUTPUT_BUDGET_EXIT_CODE:
                    raise RuntimeError(
                        "fresh Codex content repair violated the output/command guard"
                    )
                if returncode == CODEX_STALL_EXIT_CODE:
                    raise RuntimeError(
                        "fresh Codex content repair stalled for 15 minutes without JSON events"
                    )
                raise RuntimeError(
                    f"fresh Codex content repair exited with status {returncode}"
                )
            freeze, validation_error = ensure_content_freeze(job_dir)
            if freeze is None:
                assert validation_error is not None
                write_content_generation_checkpoint(
                    job_dir,
                    content_prompt=content_prompt,
                    state="repair_failed",
                    repair_attempts=repair_attempts + 1,
                    validation_error=validation_error,
                )
                raise RuntimeError(
                    "content remains invalid after the single bounded repair: "
                    + _bounded_console_text(validation_error)
                )
            write_content_generation_checkpoint(
                job_dir,
                content_prompt=content_prompt,
                state="frozen",
                repair_attempts=repair_attempts + 1,
            )
            phase_checkpoints.append(
                {
                    "phase": "content_repair_completed",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
        elif content_action != "reuse_freeze":
            checkpoint_status = inspect_content_generation_checkpoint(
                job_dir,
                content_prompt=content_prompt,
            )
            write_content_generation_checkpoint(
                job_dir,
                content_prompt=content_prompt,
                state="frozen",
                repair_attempts=int(checkpoint_status.get("repair_attempts", 0)),
            )

        phase_checkpoints.append(
            {
                "phase": "content_verified",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "content_sha256": freeze["content_sha256"],
            }
        )

        if needs_translation:
            translation_manifest_path = job_dir / TRANSLATION_MANIFEST_NAME
            translated_path = job_dir / TRANSLATED_MINUTES_NAME
            reuse_translation = False
            if translation_manifest_path.is_file() and translated_path.is_file():
                try:
                    validate_translation_manifest(job_dir)
                    reuse_translation = True
                except (OSError, ValueError, json.JSONDecodeError):
                    reuse_translation = False
            if reuse_translation:
                phase_records["translation"] = {
                    "state": "reused",
                    "translation_manifest": file_record(translation_manifest_path),
                    "elapsed_seconds": 0.0,
                }
                phase_checkpoints.append(
                    {
                        "phase": "translation_reused",
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                )
            else:
                translated_path.unlink(missing_ok=True)
                translation_manifest_path.unlink(missing_ok=True)
                source_markdown = (job_dir / "minutes.md").read_text(encoding="utf-8")
                translation_prompt = build_translation_prompt(
                    source_markdown,
                    target_language=str(policy.get("output_language", "auto")),
                )
                prompt_bytes = translation_prompt.encode("utf-8")
                manifest["handoff_prompt_bytes"]["translation"] = len(prompt_bytes)
                manifest["handoff_prompt_sha256"]["translation"] = hashlib.sha256(
                    prompt_bytes
                ).hexdigest()
                manifest["frozen_content_embedded_in_translation_prompt"] = True
                phase_checkpoints.append(
                    {
                        "phase": "translation_started",
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                )
                returncode, _translation_summary, translation_record = run_fresh_phase(
                    "translation",
                    command=commands["translation"],
                    prompt=translation_prompt,
                    env=child_env,
                    job_dir=job_dir,
                    progress_stream=sys.stderr,
                )
                phase_records["translation"] = translation_record
                manifest["phases"] = dict(phase_records)
                write_json(manifest_path, manifest)
                if returncode != 0:
                    if returncode == TOOL_OUTPUT_BUDGET_EXIT_CODE:
                        raise RuntimeError(
                            "fresh Codex translation phase violated the worker "
                            "output/contract guard"
                        )
                    if returncode == CODEX_STALL_EXIT_CODE:
                        raise RuntimeError(
                            "fresh Codex translation phase stalled for 15 minutes "
                            "without JSON events"
                        )
                    raise RuntimeError(
                        "fresh Codex translation phase exited with status "
                        f"{returncode}"
                    )
                translation_manifest = create_translation_manifest(job_dir)
                translation_record["translation_manifest"] = file_record(
                    translation_manifest_path
                )
                translation_record["target_sha256"] = translation_manifest["target"][
                    "sha256"
                ]
                phase_checkpoints.append(
                    {
                        "phase": "translation_completed",
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                        "target_sha256": translation_manifest["target"]["sha256"],
                    }
                )
            validate_translation_manifest(job_dir)
            phase_checkpoints.append(
                {
                    "phase": "translation_verified",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
        else:
            phase_records["translation"] = {
                "state": "skipped",
                "reason": "source and final languages do not require translation",
                "elapsed_seconds": 0.0,
            }

        phase_checkpoints.append(
            {
                "phase": "delivery_started",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        returncode, _delivery_summary, delivery_record = run_fresh_phase(
            "delivery",
            command=commands["delivery"],
            prompt=delivery_prompt,
            env=child_env,
            job_dir=job_dir,
            progress_stream=sys.stderr,
        )
        phase_records["delivery"] = delivery_record
        manifest["phases"] = dict(phase_records)
        write_json(manifest_path, manifest)
        if returncode != 0:
            if returncode == TOOL_OUTPUT_BUDGET_EXIT_CODE:
                raise RuntimeError(
                    "fresh Codex delivery phase violated the worker output/contract guard"
                )
            if returncode == CODEX_STALL_EXIT_CODE:
                raise RuntimeError(
                    "fresh Codex delivery phase stalled for 15 minutes without JSON events"
                )
            raise RuntimeError(
                f"fresh Codex delivery phase exited with status {returncode}"
            )
        phase_checkpoints.append(
            {
                "phase": "delivery_completed",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        final_status = verify_completed_job(job_dir)
        phase_checkpoints.append(
            {
                "phase": "job_verified",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        aggregate = aggregate_phase_records(phase_records)
        final_markdown = resolve_final_markdown(job_dir)
        manifest.update(
            {
                "state": "completed",
                "finished_at": now_local().isoformat(),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "output_dir": final_status["output_dir"],
                "content_sha256": freeze["content_sha256"],
                "final_markdown_sha256": sha256_file(final_markdown),
                "ephemeral_session_count": sum(
                    record.get("state") == "completed"
                    for record in phase_records.values()
                ),
                "phases": dict(phase_records),
                "phase_checkpoints": phase_checkpoints,
                **aggregate,
            }
        )
        write_json(manifest_path, manifest)
        print(f"fresh Codex completed: {final_status['output_dir']}")
    except BaseException as exc:
        if manifest.get("state") != "completed":
            aggregate = aggregate_phase_records(phase_records)
            manifest.update(
                {
                    "state": "failed",
                    "finished_at": now_local().isoformat(),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "phases": dict(phase_records),
                    "phase_checkpoints": phase_checkpoints,
                    "ephemeral_session_count": sum(
                        record.get("state") in {"completed", "failed"}
                        for record in phase_records.values()
                    ),
                    "error": str(exc),
                    **aggregate,
                }
            )
            write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    main()
