#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence, TextIO

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.run_codex import resolve_configured_path
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
HANDOFF_SCHEMA_VERSION = 5
MAX_REQUEST_OVERRIDES = 4
MAX_REQUEST_CHARS = 500
MAX_CONSOLE_ERROR_CHARS = 500
OVERSIZED_TOOL_OUTPUT_BYTES = 20_000
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
FRESH_PHASES = ("content", "translation", "delivery")
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

    def context_efficiency_fields(self) -> dict[str, int | float | None]:
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


def _bounded_console_text(value: object) -> str:
    text = " ".join(str(value).split())
    if len(text) <= MAX_CONSOLE_ERROR_CHARS:
        return text
    return text[: MAX_CONSOLE_ERROR_CHARS - 1] + "…"


def _event_error_message(event: Mapping[str, object]) -> str:
    for key in ("message", "error", "detail"):
        value = event.get(key)
        if value:
            return _bounded_console_text(value)
    return _bounded_console_text(event.get("type", "unknown error"))


def record_codex_event(
    event: Mapping[str, object],
    summary: CodexStreamSummary,
    *,
    elapsed_seconds: float,
    progress_stream: TextIO,
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
    if item_type == "command_execution":
        aggregated_output = item.get("aggregated_output")
        if isinstance(aggregated_output, str):
            output_bytes = len(aggregated_output.encode("utf-8"))
            summary.tool_output_bytes += output_bytes
            summary.max_tool_output_bytes = max(
                summary.max_tool_output_bytes,
                output_bytes,
            )
            if output_bytes > OVERSIZED_TOOL_OUTPUT_BYTES:
                summary.oversized_tool_output_count += 1
    item_id = item.get("id")
    if item_type not in NON_TOOL_ITEM_TYPES and isinstance(item_id, str):
        summary.tool_item_ids.add(item_id)


def consume_codex_event_line(
    raw_line: str,
    summary: CodexStreamSummary,
    *,
    elapsed_seconds: float,
    progress_stream: TextIO,
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
        )


def run_codex_json_stream(
    command: Sequence[str],
    *,
    prompt: str,
    env: Mapping[str, str],
    events_path: Path,
    stderr_path: Path,
    progress_stream: TextIO,
) -> tuple[int, CodexStreamSummary]:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    summary = CodexStreamSummary()
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
        stderr_thread.start()
        try:
            process.stdin.write(prompt)
            process.stdin.close()
            for line in process.stdout:
                events_handle.write(line)
                events_handle.flush()
                consume_codex_event_line(
                    line,
                    summary,
                    elapsed_seconds=time.perf_counter() - started,
                    progress_stream=progress_stream,
                )
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        finally:
            stderr_thread.join()
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            process.stderr.close()
    return returncode, summary


def build_fresh_prompt(
    repo_root: Path,
    job_dir: Path,
    *,
    policy: Mapping[str, object],
    request_overrides: Sequence[str] = (),
) -> str:
    skill_path = repo_root / "codex/skills/minutes/SKILL.md"
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
    language_contract = (
        "Write validated minutes.md in the detected source language. Do not translate; the "
        "next phase translates it once without evidence."
        if needs_translation
        else "Write validated minutes.md in CONTENT_OUTPUT_LANGUAGE."
    )
    return (
        "$minutes fresh-context content worker for exactly one prepared media job.\n\n"
        "This is isolated. Do not use the parent conversation or relaunch "
        "run_fresh_codex_job.py.\n\n"
        f"Repository: {repo_root}\n"
        f"Authoritative skill: {skill_path}\n"
        f"Job directory: {job_dir}\n"
        f"Source evidence input (integrity only; do not read directly): {input_path}\n"
        f"Evidence chunk manifest: {evidence_chunks_path}\n"
        f"Bounded evidence coverage summary: {evidence_summary_path}\n"
        f"Bounded preprocessing/runtime summary: {runtime_summary_path}\n"
        f"Selected snapshots directory: {snapshots_dir}\n"
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
        "This is a production media job, not repository development. Read the authoritative "
        "skill and each required reference completely once; keep a checklist and Do not reread "
        "them. Do not inspect validator implementations or tests unless a validator actually "
        "fails and its bounded error cannot be resolved from the skill. Do not run repository-wide "
        "py_compile, unittest discover, pytest, lint, or git review commands. This phase's "
        "acceptance comes from the configured content audit, compact quality review, content "
        "freeze, and launcher verification.\n\n"
        "Read worker_runtime_summary.json once; do not print the raw status, process_metrics, "
        "speaker_attribution_report, or speech_activity JSON because validators consume them. "
        "Read evidence_chunks.json and every listed chunk exactly once in manifest order. Use "
        "one bounded read command per chunk and never concatenate multiple chunk files into one "
        "tool call; they "
        "contain complete timestamped STT and OCR. Do not read codex_minutes_input.md directly or "
        "use overlapping source ranges. Read evidence_coverage_summary.json before authoring, "
        "verify frame accounting and the maximum-gap gate, and inspect only material visual_only, "
        "speaker_ui_change, and forced_coverage snapshots. Use the skill's resolvable evidence refs. "
        "Do not read or print evidence_coverage.json; deterministic validators read the complete "
        "raw ledger internally. Do not read or print fresh_codex_handoff.json. Do not recursively "
        "list the job directory or frame directories. Keep command output below 8KB; redirect "
        "larger diagnostics and print only counts or targeted excerpts. Build one cumulative "
        "inventory without lossy intermediate summaries.\n\n"
        "For strict jobs, also read codex/skills/minutes/references/quality-loop.md and apply "
        "its ledger, reader-facing document blueprint, adversarial review, bounded revision, and "
        "hash-bound review. Preserve front matter, H2 roles, inventory placement, form factors, "
        "operational utility, and reader-facing evidence. Structural validity alone is not a "
        "quality pass.\n\n"
        "Complete only the evidence, inventory, blueprint, Markdown, content audit, and content "
        "quality stages. Before freezing content, remove only unreferenced archived tail Snapshots "
        "after the substantive-content boundary when they contain lock screens, unrelated "
        "application/browser activity, or private desktop material; preserve every referenced "
        "or substantively useful Snapshot and verify all Markdown Snapshot refs resolve. Do not "
        "treat raw Snapshot count as the evidence-coverage gate. Follow the artifact order "
        "ledger/inventory → blueprint → sources → minutes → audit/quality review → content "
        "freeze. Write content_quality_review.json with schema_version=3 and only the eight "
        "model-judged final checks documented by quality-loop.md; do not calculate hashes, chunk "
        "sets, or document signals yourself. Run `python scripts/content_freeze.py <job-directory>` "
        "once; that command fills deterministic bindings, reruns all content gates, and writes "
        "content_freeze.json. Do not create, edit, render, or inspect a DOCX. Do not run "
        "archive_job.py and do not change status.json to completed. Stop after the freeze verifies. "
        "Write each large artifact "
        "once, then make only corrections required by a failed validation. Do not print or "
        "repeatedly inspect full artifacts or full diffs; "
        "use hashes, counts, targeted searches, bounded excerpts, and diff summaries. Keep the "
        "final response concise and report only the content freeze hash and validation result.\n"
    )


def build_delivery_prompt(
    repo_root: Path,
    job_dir: Path,
    *,
    policy: Mapping[str, object],
) -> str:
    skill_path = repo_root / "codex/skills/minutes/SKILL.md"
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
    translation_contract = (
        f"Run `python scripts/translation.py {job_dir} --verify` once, then read only the "
        f"validated final Markdown at {minutes_path}. Do not translate, polish, summarize, or "
        "compare it with raw evidence in this phase."
        if needs_translation
        else "No translation is required; use the frozen source Markdown as the final Markdown."
    )
    return (
        "$minutes and $documents fresh-context delivery worker for one content-frozen job.\n\n"
        "This is a new ephemeral execution with no parent or content-worker conversation. "
        "Do not launch run_fresh_codex_job.py again.\n\n"
        f"Repository: {repo_root}\n"
        f"Authoritative minutes skill: {skill_path}\n"
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
        "those files without returning them to you. Read the minutes skill, its DOCX validation "
        "reference, the Documents skill, content_freeze.json, the final Markdown, and document_blueprint.json "
        "once. Run `python scripts/content_freeze.py <job-directory> --verify` before Word work.\n\n"
        f"{translation_contract}\n\n"
        "The frozen Markdown is immutable, and the validated final Markdown is immutable. Do not "
        "rewrite, shorten, translate, polish, or reorganize either file for pagination. "
        "If you find a genuine semantic blocker, write semantic_blocker.json with the exact section "
        "and reason, then fail without archiving. Otherwise use only layout-preserving DOCX edits.\n\n"
        "Run `python scripts/finalize_docx.py prepare <job-directory>` once. It generates the draft "
        "and final DOCX, cleans the render directory, renders every page, and runs structural QA in "
        "one bounded command. Inspect every latest page PNG at 100% zoom. Blocking defects are: "
        "clipped or overlapping text, missing glyph/content, blank interior page, broken TOC or "
        "bookmark, unreadable table, and orphan heading or split row. A short final page, whitespace "
        "on a single TOC page, intentional section whitespace, and mild readable wrapping are "
        "nonblocking warnings. Do not revise for warnings alone.\n\n"
        "If a blocking defect exists, edit only minutes.final.docx and run "
        "`python scripts/finalize_docx.py prepare <job-directory> --reuse-final` once. A third render "
        "is forbidden unless a blocking defect remains; then pass its exact supported code through "
        "--blocking-defect-code. Write visual_review.json with schema_version=1, status=passed, the "
        "complete ordered inspected_pages list, empty blocking_defects, and warnings using only the "
        "documented nonblocking codes. Run `python scripts/finalize_docx.py approve <job-directory>`; "
        "the command adds hashes and creates passed docx_qa.json. Then run archive_job.py and verify "
        "status.json is completed and final artifacts pass. Never dump DOCX XML or a full "
        "Markdown/JSON file. Keep command output below 8KB and use defect codes, hashes, counts, "
        "and targeted excerpts.\n"
    )


def build_translation_prompt(
    source_markdown: str,
    *,
    target_language: str,
) -> str:
    target_label = target_language_label(target_language)
    metadata_value = "한국어" if target_label == "Korean" else "English"
    return (
        f"Translate the complete Markdown document below into natural professional {target_label}.\n"
        "Return only the translated Markdown as the final response: no preamble, no code fence, "
        "no commentary, and no tool calls. This is one translation pass, not a new analysis.\n\n"
        "Preserve the exact Markdown structure and order: heading levels, tables and cell counts, "
        "list types, checklist states, image paths, link URLs, inline-code literals, evidence "
        "references, timestamps, numeric values, units, product names, acronyms, and code. Translate "
        "the title, headings, prose, table labels/cells, captions, and metadata labels naturally. "
        f"Set the output-language metadata value to {metadata_value}. Do not summarize, omit, add, "
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
    if reasoning_effort:
        command.extend(
            ["--config", f'model_reasoning_effort="{reasoning_effort}"']
        )
    command.append("-")
    return command


def phase_artifact_paths(job_dir: Path, phase: str) -> dict[str, Path]:
    if phase not in FRESH_PHASES:
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
    elapsed_seconds = 0.0
    for phase in FRESH_PHASES:
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
        help="Validate and print the small handoff without launching Codex",
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
    for phase in planned_phases:
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
        "planned_phases": list(planned_phases),
        "phase_isolation": {
            "content_reads_raw_evidence": True,
            "translation_reads_raw_evidence": False,
            "translation_reads_only_frozen_markdown": needs_translation,
            "delivery_reads_raw_evidence": False,
            "delivery_requires_content_freeze": True,
            "delivery_requires_translation_manifest": needs_translation,
        },
        "parent_conversation_inherited": False,
        "raw_evidence_embedded_in_handoff": False,
        "handoff_prompt_bytes": {
            "content": len(content_prompt.encode("utf-8")),
            "translation": None,
            "delivery": len(delivery_prompt.encode("utf-8")),
        },
        "handoff_prompt_sha256": {
            "content": hashlib.sha256(content_prompt.encode("utf-8")).hexdigest(),
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
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print("\n--- content prompt ---\n")
        print(content_prompt, end="")
        if needs_translation:
            print("\n--- translation prompt ---\n")
            print("<built once from content-frozen minutes.md; source evidence is never included>\n")
        print("\n--- delivery prompt ---\n")
        print(delivery_prompt, end="")
        return

    write_json(manifest_path, manifest)
    child_env = dict(parent_env)
    child_env[FRESH_CONTEXT_ENV] = "1"
    child_env["MINUTES_JOB_DIR"] = str(job_dir)
    started = time.perf_counter()
    phase_records: dict[str, Mapping[str, object]] = {}
    phase_checkpoints: list[dict[str, object]] = []
    try:
        from scripts.content_freeze import validate_content_freeze

        freeze_path = job_dir / CONTENT_FREEZE_NAME
        reuse_freeze = False
        if freeze_path.is_file():
            try:
                validate_content_freeze(job_dir)
                reuse_freeze = True
            except ValueError:
                reuse_freeze = False
        if reuse_freeze:
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
        else:
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
                raise RuntimeError(
                    f"fresh Codex content phase exited with status {returncode}"
                )
            phase_checkpoints.append(
                {
                    "phase": "content_completed",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
        freeze = validate_content_freeze(job_dir)
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
