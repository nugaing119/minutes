from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings
from scripts.utils import now_local, read_json, safe_filename, unique_path, write_json


EVIDENCE_FILES = (
    "codex_minutes_input.md",
    "transcript.evidence.txt",
    "transcript.json",
    "transcript.srt",
    "transcript.txt",
    "screen_text.json",
    "screen_text.txt",
    "evidence_coverage.json",
    "speaker_attribution_report.json",
    "speech_activity.json",
    "source_metadata.json",
    "process_metrics.json",
)
EVIDENCE_DIRECTORIES = ("frames", "snapshots")


def prepare_quality_rework(
    source_job: Path,
    *,
    jobs_dir: Path,
    allow_prepared_retry: bool = False,
) -> Path:
    jobs_dir = jobs_dir.expanduser().resolve()
    source_job = source_job.expanduser().resolve()
    if source_job.parent != jobs_dir:
        raise ValueError("source job must be a direct child of the configured jobs directory")
    status = read_json(source_job / "status.json")
    source_status = status.get("status")
    prepared_retry = allow_prepared_retry and source_status == "awaiting_codex"
    if source_status != "completed" and not prepared_retry:
        raise ValueError("source job must be completed before quality rework")
    source_output = (
        _prepared_source(status, source_job)
        if prepared_retry
        else _archived_source(status)
    )
    if not source_output.is_file():
        raise FileNotFoundError(source_output)
    input_path = source_job / "codex_minutes_input.md"
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    timestamp = now_local().strftime("%Y%m%d_%H%M%S")
    source_label = safe_filename(source_job.name, max_length=42) or "job"
    destination = unique_path(jobs_dir / f"{timestamp}_quality-rework_{source_label}")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name in EVIDENCE_FILES:
            source = source_job / name
            if source.is_file():
                shutil.copy2(source, destination / name)
        for name in EVIDENCE_DIRECTORIES:
            source = source_job / name
            if source.is_dir():
                shutil.copytree(source, destination / name)
        staged_source = destination / f"source{source_output.suffix.lower()}"
        shutil.copy2(source_output, staged_source)

        codex_handoff = dict(status.get("codex_handoff", {}))
        codex_handoff.update(
            {
                "fresh_context_required": True,
                "input_path": str(destination / "codex_minutes_input.md"),
                "snapshots_path": str(destination / "snapshots"),
            }
        )
        source_content_audit = status.get("content_audit", {})
        if not isinstance(source_content_audit, dict):
            source_content_audit = {}
        content_audit = {
            "mode": str(source_content_audit.get("mode", "strict")),
            "status": "pending",
            "official_source_verification": str(
                source_content_audit.get("official_source_verification", "auto")
            ),
            "official_source_status": "pending",
            "issues": [],
        }
        rework_status = {
            "status": "awaiting_codex",
            "state": "awaiting_codex",
            "step": "quality_rework",
            "job_id": destination.name,
            "source": str(staged_source),
            "source_job": str(source_job),
            "recording_date": status.get("recording_date"),
            "speaker_attribution": status.get("speaker_attribution", {}),
            "codex_handoff": codex_handoff,
            "content_audit": content_audit,
            "resource_policy": status.get("resource_policy", {}),
            "process_metrics": status.get("process_metrics", {}),
            "files": {
                "source": str(staged_source),
                "codex_input": str(destination / "codex_minutes_input.md"),
                "snapshots": str(destination / "snapshots"),
            },
        }
        write_json(destination / "status.json", rework_status)
        write_json(
            destination / "rework_provenance.json",
            {
                "schema_version": 1,
                "created_at": now_local().isoformat(),
                "purpose": (
                    "content_quality_retry_without_preprocessing"
                    if prepared_retry
                    else "content_quality_rework_without_preprocessing"
                ),
                "source_job": str(source_job),
                "source_output": str(source_output),
                "source_media_sha256": _sha256_file(staged_source),
                "codex_input_sha256": _sha256_file(
                    destination / "codex_minutes_input.md"
                ),
            },
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def sanitize_existing_rework(job_dir: Path, *, jobs_dir: Path) -> None:
    jobs_dir = jobs_dir.expanduser().resolve()
    job_dir = job_dir.expanduser().resolve()
    if job_dir.parent != jobs_dir:
        raise ValueError("rework job must be a direct child of the configured jobs directory")
    status_path = job_dir / "status.json"
    status = read_json(status_path)
    if status.get("status") != "awaiting_codex" or status.get("step") != "quality_rework":
        raise ValueError("only an awaiting quality rework job can be sanitized")
    source_content_audit = status.get("content_audit", {})
    if not isinstance(source_content_audit, dict):
        source_content_audit = {}
    status["content_audit"] = {
        "mode": str(source_content_audit.get("mode", "strict")),
        "status": "pending",
        "official_source_verification": str(
            source_content_audit.get("official_source_verification", "auto")
        ),
        "official_source_status": "pending",
        "issues": [],
    }
    write_json(status_path, status)


def _archived_source(status: dict) -> Path:
    files = status.get("files")
    if not isinstance(files, dict):
        raise ValueError("completed source job has no files mapping")
    raw = files.get("source") or files.get("video") or files.get("audio")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("completed source job has no archived source path")
    return Path(raw).expanduser().resolve()


def _prepared_source(status: dict, source_job: Path) -> Path:
    candidates: list[Path] = []
    managed = status.get("managed_source")
    if isinstance(managed, str) and managed.strip():
        candidates.append(Path(managed).expanduser().resolve())
    files = status.get("files")
    if isinstance(files, dict):
        raw = files.get("source") or files.get("video") or files.get("audio")
        if isinstance(raw, str) and raw.strip():
            candidates.append(Path(raw).expanduser().resolve())
    candidates.extend(sorted(source_job.glob("source.*")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("prepared source job has no staged source media")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clone verified evidence into a new fresh-Codex quality rework job."
    )
    parser.add_argument("source_job", type=Path)
    parser.add_argument(
        "--sanitize-existing",
        action="store_true",
        help="Remove inherited result counts from an awaiting quality rework status.",
    )
    parser.add_argument(
        "--from-prepared",
        action="store_true",
        help="Clone an awaiting prepared job for a clean content retry without STT/OCR.",
    )
    args = parser.parse_args()
    settings = load_settings()
    if args.sanitize_existing:
        sanitize_existing_rework(
            args.source_job,
            jobs_dir=Path(settings.jobs_dir),
        )
        print(args.source_job.expanduser().resolve())
        return
    destination = prepare_quality_rework(
        args.source_job,
        jobs_dir=Path(settings.jobs_dir),
        allow_prepared_retry=args.from_prepared,
    )
    print(destination)


if __name__ == "__main__":
    main()
