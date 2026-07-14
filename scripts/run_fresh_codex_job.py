#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.run_codex import resolve_configured_path
from scripts.utils import now_local, read_json, write_json


FRESH_CONTEXT_ENV = "MINUTES_FRESH_CONTEXT"
HANDOFF_SCHEMA_VERSION = 1
MAX_REQUEST_OVERRIDES = 4
MAX_REQUEST_CHARS = 500


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
        "speaker_attribution_report.json",
        "speech_activity.json",
    )
    evidence_files = [
        file_record(path)
        for name in evidence_names
        if (path := job_dir / name).is_file()
    ]
    snapshots_dir = job_dir / "snapshots"
    snapshots = (
        [file_record(path) for path in sorted(snapshots_dir.glob("*.jpg"))]
        if snapshots_dir.is_dir()
        else []
    )
    return {
        "files": evidence_files,
        "snapshots": snapshots,
        "snapshot_count": len(snapshots),
        "total_bytes": sum(int(item["bytes"]) for item in evidence_files + snapshots),
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


def job_policy(status: Mapping[str, object]) -> dict[str, str]:
    handoff = status.get("codex_handoff", {})
    content_audit = status.get("content_audit", {})
    if not isinstance(handoff, Mapping):
        handoff = {}
    if not isinstance(content_audit, Mapping):
        content_audit = {}
    return {
        "output_language": str(handoff.get("output_language", "auto")),
        "detected_language": str(handoff.get("detected_language", "unknown")),
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


def build_fresh_prompt(
    repo_root: Path,
    job_dir: Path,
    *,
    policy: Mapping[str, str],
    request_overrides: Sequence[str] = (),
) -> str:
    skill_path = repo_root / "codex/skills/minutes/SKILL.md"
    input_path = job_dir / "codex_minutes_input.md"
    snapshots_dir = job_dir / "snapshots"
    overrides = normalize_request_overrides(request_overrides)
    override_text = "\n".join(f"- {item}" for item in overrides) or "- 없음 / none"
    return (
        "$minutes fresh-context worker for exactly one prepared media job.\n\n"
        "This is a new ephemeral execution. Do not rely on, reconstruct, or request the "
        "parent conversation. Do not launch run_fresh_codex_job.py again.\n\n"
        f"Repository: {repo_root}\n"
        f"Authoritative skill: {skill_path}\n"
        f"Job directory: {job_dir}\n"
        f"Complete evidence input: {input_path}\n"
        f"Selected snapshots directory: {snapshots_dir}\n"
        f"OUTPUT_LANGUAGE={policy.get('output_language', 'auto')}\n"
        f"Detected source language: {policy.get('detected_language', 'unknown')}\n"
        f"CONTENT_AUDIT_MODE={policy.get('content_audit_mode', 'off')}\n"
        "OFFICIAL_SOURCE_VERIFICATION="
        f"{policy.get('official_source_verification', 'off')}\n\n"
        "Per-job request overrides not already encoded above:\n"
        f"{override_text}\n\n"
        "First read the authoritative skill completely. Then read the complete evidence input "
        "from disk; it contains the full timestamped STT and OCR rather than a handoff summary. "
        "Inspect selected snapshots only as required by the evidence policy. If the input is too "
        "large for one context, read it sequentially by timestamp into one cumulative inventory; "
        "never replace source evidence with lossy section summaries.\n\n"
        "Complete every remaining job step in the skill, including the configured inventory and "
        "content audit, Markdown and DOCX generation, archive, render inspection, and final-folder "
        "verification. Do not report success unless status.json is completed and the archived "
        "artifacts verify. Keep the final response concise and include the output directory and "
        "validation result.\n"
    )


def build_fresh_codex_command(
    codex_binary: str,
    repo_root: Path,
    minutes_home: Path,
    last_message_path: Path,
) -> list[str]:
    return [
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
        "--color",
        "never",
        "-",
    ]


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
    if handoff.get("docx_enabled", False):
        docx_path = Path(str(files.get("docx", ""))).expanduser()
        if not docx_path.is_file():
            raise RuntimeError(f"completed job is missing docx: {docx_path}")
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
    evidence = collect_evidence_manifest(job_dir)
    request_overrides = normalize_request_overrides(args.request)
    prompt = build_fresh_prompt(
        repo_root,
        job_dir,
        policy=policy,
        request_overrides=request_overrides,
    )
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    last_message_path = job_dir / "fresh_codex_last_message.txt"
    codex_binary = shutil.which("codex", path=parent_env.get("PATH"))
    if codex_binary is None:
        raise SystemExit("error: codex executable was not found on PATH")
    command = build_fresh_codex_command(
        codex_binary,
        repo_root,
        minutes_home,
        last_message_path,
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
        "parent_conversation_inherited": False,
        "raw_evidence_embedded_in_handoff": False,
        "handoff_prompt_bytes": len(prompt.encode("utf-8")),
        "handoff_prompt_sha256": prompt_sha256,
        "evidence": evidence,
        "command": command[:-1] + ["<prompt-via-stdin>"],
    }

    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print("\n--- fresh-context prompt ---\n")
        print(prompt, end="")
        return

    write_json(manifest_path, manifest)
    child_env = dict(parent_env)
    child_env[FRESH_CONTEXT_ENV] = "1"
    child_env["MINUTES_JOB_DIR"] = str(job_dir)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            env=child_env,
            check=False,
        )
        elapsed_seconds = round(time.perf_counter() - started, 3)
        manifest.update(
            {
                "finished_at": now_local().isoformat(),
                "elapsed_seconds": elapsed_seconds,
                "codex_returncode": completed.returncode,
            }
        )
        if completed.returncode != 0:
            manifest["state"] = "failed"
            write_json(manifest_path, manifest)
            raise SystemExit(
                f"error: fresh Codex exited with status {completed.returncode}; "
                f"see {last_message_path}"
            )
        final_status = verify_completed_job(job_dir)
        manifest.update(
            {
                "state": "completed",
                "output_dir": final_status["output_dir"],
            }
        )
        write_json(manifest_path, manifest)
        print(f"fresh Codex completed: {final_status['output_dir']}")
    except BaseException as exc:
        if manifest.get("state") != "completed":
            manifest.update(
                {
                    "state": "failed",
                    "finished_at": now_local().isoformat(),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error": str(exc),
                }
            )
            write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    main()
