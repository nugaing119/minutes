from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.content_audit import validate_content_artifacts
from scripts.content_quality import finalize_compact_review
from scripts.document_language import language_policy_from_status
from scripts.utils import now_local, read_json, write_json


SCHEMA_VERSION = 1
FREEZE_NAME = "content_freeze.json"
MAX_CLI_ERROR_CHARS = 12_000
CONTENT_ARTIFACT_NAMES = (
    "minutes.md",
    "content_inventory.json",
    "evidence_ledger.json",
    "document_blueprint.json",
    "official_sources.json",
    "content_audit.json",
    "content_quality_review.json",
    "content_density_baseline.json",
    "evidence_chunks.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _job_policy(status: Mapping[str, Any]) -> dict[str, Any]:
    content_audit = status.get("content_audit", {})
    if not isinstance(content_audit, Mapping):
        content_audit = {}
    handoff = status.get("codex_handoff", {})
    if not isinstance(handoff, Mapping):
        handoff = {}
    return {
        "audit_mode": str(
            content_audit.get(
                "mode",
                handoff.get("content_audit_mode", "off"),
            )
        ),
        "official_source_verification": str(
            content_audit.get(
                "official_source_verification",
                handoff.get("official_source_verification", "off"),
            )
        ),
        **language_policy_from_status(status),
    }


def _manifest_sha256(artifacts: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        artifacts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _bounded_error(value: object) -> str:
    compact = " ".join(str(value).split())
    if len(compact) <= MAX_CLI_ERROR_CHARS:
        return compact
    return compact[: MAX_CLI_ERROR_CHARS - 1] + "…"


def _normalize_official_checked_at(job_dir: Path) -> bool:
    """Clamp a same-day future model timestamp to the deterministic freeze clock."""
    official_path = job_dir / "official_sources.json"
    if not official_path.is_file():
        return False
    official = read_json(official_path)
    checked_at = official.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at.strip():
        return False
    try:
        checked_datetime = datetime.fromisoformat(
            checked_at.replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if checked_datetime.tzinfo is None or checked_datetime.utcoffset() is None:
        return False
    current = now_local().astimezone(checked_datetime.tzinfo)
    if checked_datetime <= current or checked_datetime.date() != current.date():
        return False
    official["checked_at"] = current.isoformat()
    write_json(official_path, official)
    return True


def create_content_freeze(job_dir: Path) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    if not job_dir.is_dir():
        raise FileNotFoundError(job_dir)
    _normalize_official_checked_at(job_dir)
    finalize_compact_review(job_dir)
    status = read_json(job_dir / "status.json")
    policy = _job_policy(status)
    validation = validate_content_artifacts(
        job_dir,
        audit_mode=policy["audit_mode"],
        official_source_verification=policy["official_source_verification"],
    )
    if validation.get("status") == "failed" or validation.get("issues"):
        raise ValueError("content cannot be frozen before validation passes")

    minutes_path = job_dir / "minutes.md"
    if not minutes_path.is_file():
        raise FileNotFoundError(minutes_path)
    artifacts = [
        _file_record(path)
        for name in CONTENT_ARTIFACT_NAMES
        if (path := job_dir / name).is_file()
    ]
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "frozen",
        "frozen_at": now_local().isoformat(),
        "content_sha256": sha256_file(minutes_path),
        "artifact_manifest_sha256": _manifest_sha256(artifacts),
        "policy": policy,
        "artifacts": artifacts,
        "validation": {
            key: validation.get(key)
            for key in (
                "mode",
                "status",
                "official_source_status",
                "inventory_items",
                "required_items",
                "conflicts",
            )
            if key in validation
        },
    }
    write_json(job_dir / FREEZE_NAME, result)
    return result


def validate_content_freeze(
    job_dir: Path,
    freeze_path: Path | None = None,
    *,
    revalidate_content: bool = True,
) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    freeze_path = (freeze_path or job_dir / FREEZE_NAME).expanduser().resolve()
    freeze = read_json(freeze_path)
    issues: list[str] = []
    if freeze.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"{FREEZE_NAME} schema_version must be {SCHEMA_VERSION}")
    if freeze.get("status") != "frozen":
        issues.append(f"{FREEZE_NAME} status must be frozen")

    artifacts = freeze.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        issues.append(f"{FREEZE_NAME} artifacts must be a non-empty list")
        artifacts = []
    current_records: list[dict[str, Any]] = []
    for index, record in enumerate(artifacts):
        if not isinstance(record, dict):
            issues.append(f"{FREEZE_NAME} artifacts[{index}] must be an object")
            continue
        name = record.get("name")
        if not isinstance(name, str) or name not in CONTENT_ARTIFACT_NAMES:
            issues.append(f"{FREEZE_NAME} artifacts[{index}] has an invalid name")
            continue
        path = job_dir / name
        if not path.is_file():
            issues.append(f"frozen content artifact is missing: {name}")
            continue
        current = _file_record(path)
        current_records.append(current)
        if current != record:
            issues.append(f"frozen content artifact changed: {name}")

    minutes_path = job_dir / "minutes.md"
    if minutes_path.is_file() and freeze.get("content_sha256") != sha256_file(minutes_path):
        issues.append("frozen minutes.md hash does not match")
    if freeze.get("artifact_manifest_sha256") != _manifest_sha256(current_records):
        issues.append("frozen content artifact manifest does not match")

    status = read_json(job_dir / "status.json")
    current_policy = _job_policy(status)
    if freeze.get("policy") != current_policy:
        issues.append("frozen content policy does not match current job policy")
    if revalidate_content and not issues:
        validation = validate_content_artifacts(
            job_dir,
            audit_mode=current_policy["audit_mode"],
            official_source_verification=current_policy[
                "official_source_verification"
            ],
        )
        if validation.get("status") == "failed" or validation.get("issues"):
            issues.append("frozen content no longer passes deterministic validation")
    if issues:
        raise ValueError("content freeze validation failed: " + "; ".join(issues))
    return freeze


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Freeze or verify validated minutes content before DOCX work."
    )
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = (
            validate_content_freeze(args.job_dir)
            if args.verify
            else create_content_freeze(args.job_dir)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {_bounded_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "status": result["status"],
                "content_sha256": result["content_sha256"],
                "artifact_count": len(result["artifacts"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
