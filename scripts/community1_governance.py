from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


MODEL_ID = "pyannote/speaker-diarization-community-1"
MODEL_LICENSE = "CC BY 4.0"
APPROVAL_SCHEMA_VERSION = 1
MODEL_MANIFEST_SCHEMA_VERSION = 1
REQUIRED_APPROVAL_FIELDS = (
    "model_id",
    "license",
    "gate_text_sha256",
    "gate_capture_path",
    "accepted_at",
    "accepted_by_hf_user",
    "company_owner",
    "approved_use",
    "source_url",
)
FORBIDDEN_ENV_KEYS = {
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGINGFACE_TOKEN",
}
SECRET_KEY_PATTERN = re.compile(r"(?:token|secret|api[_-]?key|password)", re.I)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class GovernanceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_community1_governance(
    approval_path: Path,
    model_dir: Path,
) -> dict[str, Any]:
    approval_path = approval_path.expanduser().resolve()
    model_dir = model_dir.expanduser().resolve()
    if not approval_path.is_file():
        return {
            "status": "disabled_by_governance",
            "activation_allowed": False,
            "reason": "approval artifact is missing",
            "approval_path": str(approval_path),
            "network_access": "forbidden",
            "model_download": "forbidden",
        }
    try:
        approval = read_json_object(approval_path)
        approval_summary = validate_approval_artifact(approval_path, approval)
    except (OSError, json.JSONDecodeError, GovernanceError) as exc:
        return {
            "status": "disabled_by_governance",
            "activation_allowed": False,
            "reason": str(exc),
            "approval_path": str(approval_path),
            "network_access": "forbidden",
            "model_download": "forbidden",
        }

    manifest_path = model_dir / "model_manifest.json"
    if not manifest_path.is_file():
        return {
            "status": "skipped_unavailable",
            "activation_allowed": False,
            "reason": "approved offline model mirror is missing",
            "approval": approval_summary,
            "model_dir": str(model_dir),
            "network_access": "forbidden",
            "model_download": "provisioning_only_after_governance_approval",
        }
    try:
        manifest = read_json_object(manifest_path)
        manifest_summary = validate_model_manifest(
            model_dir,
            manifest,
            approval_sha256=approval_summary["approval_sha256"],
        )
    except (OSError, json.JSONDecodeError, GovernanceError) as exc:
        return {
            "status": "skipped_unavailable",
            "activation_allowed": False,
            "reason": str(exc),
            "approval": approval_summary,
            "model_dir": str(model_dir),
            "network_access": "forbidden",
            "model_download": "forbidden_until_mirror_is_repaired",
        }
    return {
        "status": "ready_offline",
        "activation_allowed": True,
        "approval": approval_summary,
        "model": manifest_summary,
        "runtime_environment": offline_runtime_environment({}),
        "network_access": "forbidden",
        "model_download": "forbidden_during_jobs",
    }


def validate_approval_artifact(
    approval_path: Path,
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    if approval.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        raise GovernanceError("approval artifact schema_version must be 1")
    missing = [field for field in REQUIRED_APPROVAL_FIELDS if not _text(approval.get(field))]
    if missing:
        raise GovernanceError(f"approval artifact is missing required fields: {missing}")
    if approval.get("model_id") != MODEL_ID:
        raise GovernanceError(f"approval artifact model_id must be {MODEL_ID}")
    if normalize_license(str(approval.get("license"))) != normalize_license(MODEL_LICENSE):
        raise GovernanceError(f"approval artifact license must be {MODEL_LICENSE}")
    gate_hash = _text(approval.get("gate_text_sha256")).lower()
    if not SHA256_PATTERN.fullmatch(gate_hash):
        raise GovernanceError("gate_text_sha256 must be a lowercase SHA-256 digest")
    accepted_at = parse_aware_datetime(_text(approval.get("accepted_at")))
    source_url = _text(approval.get("source_url"))
    parsed_url = urlparse(source_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc.lower() != "huggingface.co"
        or MODEL_ID not in parsed_url.path.strip("/")
    ):
        raise GovernanceError("source_url must be the official Hugging Face model page")
    capture_path = Path(_text(approval.get("gate_capture_path"))).expanduser()
    if not capture_path.is_absolute():
        capture_path = approval_path.parent / capture_path
    capture_path = capture_path.resolve()
    if not capture_path.is_file():
        raise GovernanceError("gate_capture_path does not exist")
    if contains_secret_material(approval):
        raise GovernanceError("approval artifact contains a token or secret-like value")
    return {
        "approval_path": str(approval_path),
        "approval_sha256": sha256_file(approval_path),
        "gate_text_sha256": gate_hash,
        "gate_capture_path": str(capture_path),
        "gate_capture_sha256": sha256_file(capture_path),
        "accepted_at": accepted_at.isoformat(),
        "company_owner": _text(approval.get("company_owner")),
        "approved_use": _text(approval.get("approved_use")),
        "source_url": source_url,
    }


def validate_model_manifest(
    model_dir: Path,
    manifest: Mapping[str, Any],
    *,
    approval_sha256: str,
) -> dict[str, Any]:
    if manifest.get("schema_version") != MODEL_MANIFEST_SCHEMA_VERSION:
        raise GovernanceError("model_manifest.json schema_version must be 1")
    if manifest.get("model_id") != MODEL_ID:
        raise GovernanceError(f"model_manifest.json model_id must be {MODEL_ID}")
    if normalize_license(str(manifest.get("license"))) != normalize_license(MODEL_LICENSE):
        raise GovernanceError(f"model_manifest.json license must be {MODEL_LICENSE}")
    if manifest.get("approval_artifact_sha256") != approval_sha256:
        raise GovernanceError("model manifest approval artifact hash does not match")
    revision = _text(manifest.get("revision"))
    if not revision or revision.lower() in {"main", "latest", "head"}:
        raise GovernanceError("model manifest revision must be an immutable commit/revision")
    if contains_secret_material(manifest):
        raise GovernanceError("model manifest contains a token or secret-like value")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise GovernanceError("model manifest files must be a non-empty list")
    verified_files = []
    seen_paths: set[str] = set()
    for index, record in enumerate(files):
        if not isinstance(record, Mapping):
            raise GovernanceError(f"model manifest files[{index}] must be an object")
        relative = _text(record.get("path"))
        expected_hash = _text(record.get("sha256")).lower()
        if not relative or relative in seen_paths:
            raise GovernanceError(f"model manifest files[{index}] path must be unique")
        seen_paths.add(relative)
        if not SHA256_PATTERN.fullmatch(expected_hash):
            raise GovernanceError(f"model manifest files[{index}] has invalid SHA-256")
        path = safe_model_file(model_dir, relative)
        if not path.is_file():
            raise GovernanceError(f"model mirror file is missing: {relative}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise GovernanceError(f"model mirror file hash mismatch: {relative}")
        verified_files.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": actual_hash}
        )
    required_notices = {
        _text(manifest.get("model_card_path")),
        _text(manifest.get("attribution_path")),
    }
    if "" in required_notices or not required_notices.issubset(seen_paths):
        raise GovernanceError(
            "model manifest must include model_card_path and attribution_path in files"
        )
    return {
        "model_dir": str(model_dir),
        "manifest_path": str(model_dir / "model_manifest.json"),
        "manifest_sha256": sha256_file(model_dir / "model_manifest.json"),
        "revision": revision,
        "file_count": len(verified_files),
        "total_bytes": sum(item["bytes"] for item in verified_files),
        "files": verified_files,
    }


def offline_runtime_environment(
    base_environment: Mapping[str, str],
) -> dict[str, str]:
    forbidden = sorted(key for key in FORBIDDEN_ENV_KEYS if base_environment.get(key))
    if forbidden:
        raise GovernanceError(
            f"Community-1 job runtime must not receive Hugging Face tokens: {forbidden}"
        )
    result = dict(base_environment)
    for key in FORBIDDEN_ENV_KEYS:
        result.pop(key, None)
    result.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYANNOTE_METRICS_ENABLED": "0",
        }
    )
    return result


def safe_model_file(model_dir: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise GovernanceError("model manifest paths must be relative")
    resolved = (model_dir / relative_path).resolve()
    try:
        resolved.relative_to(model_dir.resolve())
    except ValueError as exc:
        raise GovernanceError("model manifest path escapes the model directory") from exc
    return resolved


def contains_secret_material(value: Any, *, key_path: str = "") -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{key_path}.{key}" if key_path else str(key)
            if SECRET_KEY_PATTERN.search(str(key)):
                return True
            if contains_secret_material(child, key_path=child_path):
                return True
        return False
    if isinstance(value, list):
        return any(contains_secret_material(item, key_path=key_path) for item in value)
    if isinstance(value, str):
        return bool(re.search(r"\bhf_[A-Za-z0-9]{10,}\b", value))
    return False


def normalize_license(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError("accepted_at must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise GovernanceError("accepted_at must include a timezone")
    return parsed


def read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GovernanceError(f"{path.name} must contain a JSON object")
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Community-1 governance/offline-mirror check. "
            "This command never downloads a model."
        )
    )
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = evaluate_community1_governance(args.approval, args.model_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
