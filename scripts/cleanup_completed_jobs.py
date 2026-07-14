from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings
from scripts.utils import now_local, read_json


def _completed_at(status: dict[str, Any]) -> datetime | None:
    raw_value = str(status.get("completed_at", "")).strip()
    if not raw_value:
        return None
    try:
        value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _job_size(job_dir: Path) -> int:
    total = 0
    for path in job_dir.rglob("*"):
        try:
            if path.is_file() or path.is_symlink():
                total += path.lstat().st_size
        except OSError:
            continue
    return total


def _verify_final_artifacts(status: dict[str, Any]) -> tuple[bool, str, list[str]]:
    raw_output_dir = str(status.get("output_dir", "")).strip()
    if not raw_output_dir:
        return False, "output_dir is missing from status", []

    output_dir = Path(raw_output_dir).expanduser()
    if output_dir.is_symlink() or not output_dir.is_dir():
        return False, "final output directory is missing or unsafe", []

    files = status.get("files")
    if not isinstance(files, dict):
        return False, "final files are missing from status", []

    required: dict[str, str] = {}
    media = files.get("source") or files.get("video") or files.get("audio")
    if media:
        required["media"] = str(media)
    minutes = files.get("minutes")
    if minutes:
        required["minutes"] = str(minutes)
    if files.get("docx"):
        required["docx"] = str(files["docx"])
    if files.get("snapshots"):
        required["snapshots"] = str(files["snapshots"])

    if "media" not in required or "minutes" not in required:
        return False, "final media or Markdown path is missing from status", []

    output_root = output_dir.resolve()
    verified: list[str] = []
    for name, raw_path in required.items():
        path = Path(raw_path).expanduser()
        if path.is_symlink():
            return False, f"final {name} path is a symlink", verified
        expected_type_exists = path.is_dir() if name == "snapshots" else path.is_file()
        if not expected_type_exists:
            return False, f"final {name} artifact is missing", verified
        resolved = path.resolve()
        if not _path_within(resolved, output_root):
            return False, f"final {name} artifact is outside output_dir", verified
        verified.append(str(resolved))
    return True, "", verified


def inspect_completed_job(
    job_dir: Path,
    *,
    retention_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if retention_hours < 0:
        raise ValueError("retention_hours must be zero or greater")
    if job_dir.is_symlink() or not job_dir.is_dir():
        return {
            "job": job_dir.name,
            "eligible": False,
            "reason": "job directory is missing or unsafe",
        }

    try:
        status = read_json(job_dir / "status.json")
    except (OSError, json.JSONDecodeError):
        return {
            "job": job_dir.name,
            "eligible": False,
            "reason": "status.json is unreadable or invalid",
        }
    if status.get("status") != "completed":
        return {"job": job_dir.name, "eligible": False, "reason": "not completed"}

    completed_at = _completed_at(status)
    if completed_at is None:
        return {
            "job": job_dir.name,
            "eligible": False,
            "reason": "completed_at is missing, invalid, or lacks a timezone",
        }

    current_time = now or now_local()
    if current_time.tzinfo is None:
        raise ValueError("now must include a timezone")
    expires_at = completed_at + timedelta(hours=retention_hours)
    if current_time < expires_at:
        remaining_seconds = max(0, int((expires_at - current_time).total_seconds()))
        return {
            "job": job_dir.name,
            "eligible": False,
            "reason": "retention period is still active",
            "completed_at": completed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "remaining_seconds": remaining_seconds,
        }

    verified, reason, verified_artifacts = _verify_final_artifacts(status)
    if not verified:
        return {
            "job": job_dir.name,
            "eligible": False,
            "reason": reason,
            "completed_at": completed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

    return {
        "job": job_dir.name,
        "eligible": True,
        "completed_at": completed_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "verified_artifacts": verified_artifacts,
        "reclaimed_bytes": _job_size(job_dir),
    }


def cleanup_completed_job(
    job_dir: Path,
    *,
    apply: bool,
    retention_hours: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    inspected = inspect_completed_job(
        job_dir,
        retention_hours=retention_hours,
        now=now,
    )
    if not apply or not inspected["eligible"]:
        return {**inspected, "purged": False}

    if job_dir.is_symlink() or not job_dir.is_dir():
        return {
            **inspected,
            "eligible": False,
            "purged": False,
            "reason": "job directory changed before deletion",
        }
    shutil.rmtree(job_dir)
    return {**inspected, "purged": True}


def cleanup_completed_jobs(
    jobs_dir: Path,
    *,
    apply: bool,
    retention_hours: int,
    selected_jobs: list[Path] | None = None,
    excluded_jobs: set[Path] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    jobs_root = jobs_dir.expanduser().resolve()
    excluded = {path.expanduser().resolve() for path in (excluded_jobs or set())}
    jobs = selected_jobs or sorted(path for path in jobs_root.iterdir() if path.is_dir())
    jobs = [path for path in jobs if path.expanduser().resolve() not in excluded]
    results = []
    for path in jobs:
        resolved = path.expanduser().resolve()
        if resolved == jobs_root or not _path_within(resolved, jobs_root):
            results.append(
                {
                    "job": path.name,
                    "eligible": False,
                    "purged": False,
                    "reason": "job directory is outside the configured jobs root",
                }
            )
            continue
        results.append(
            cleanup_completed_job(
                path,
                apply=apply,
                retention_hours=retention_hours,
                now=now,
            )
        )
    return {
        "mode": "apply" if apply else "dry-run",
        "retention_hours": retention_hours,
        "jobs": results,
        "eligible_jobs": sum(bool(item.get("eligible")) for item in results),
        "purged_jobs": sum(bool(item.get("purged")) for item in results),
        "reclaimed_bytes": sum(
            int(item.get("reclaimed_bytes", 0))
            for item in results
            if apply and item.get("purged")
        ),
        "potential_reclaim_bytes": sum(
            int(item.get("reclaimed_bytes", 0))
            for item in results
            if item.get("eligible")
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify final artifacts and optionally delete completed job directories "
            "after their configured retention period."
        )
    )
    parser.add_argument(
        "job_dirs",
        nargs="*",
        help="Specific job directories; defaults to every job under MINUTES_HOME/jobs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete eligible job directories. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--retention-hours",
        type=int,
        help="Override COMPLETED_JOB_RETENTION_HOURS for this invocation.",
    )
    args = parser.parse_args()
    settings = load_settings()
    retention_hours = (
        args.retention_hours
        if args.retention_hours is not None
        else settings.completed_job_retention_hours
    )
    selected = [Path(value).expanduser().resolve() for value in args.job_dirs] or None
    result = cleanup_completed_jobs(
        settings.jobs_dir,
        apply=args.apply,
        retention_hours=retention_hours,
        selected_jobs=selected,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
