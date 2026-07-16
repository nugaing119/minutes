from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import read_json, write_json


PATCH_NAME = "content_repair_patch.json"
MAX_PATCH_BYTES = 64_000
MAX_JSON_UPDATES = 96
MAX_MARKDOWN_REPLACEMENTS = 12
MAX_JSON_PATH_DEPTH = 12
ALLOWED_PATCH_KEYS = {
    "schema_version",
    "json_updates",
    "markdown_replacements",
}
MODEL_OWNED_FIELDS = {
    "content_audit.json": {
        "schema_version",
        "status",
        "covered_item_ids",
        "missing_item_ids",
        "documented_conflict_ids",
        "recording_fidelity",
        "coverage",
        "conflict_coverage",
        "qualifier_changes",
        "silent_conflicts",
        "intentional_omissions",
    },
    "content_quality_review.json": {
        "schema_version",
        "status",
        "review_cycles",
        "final_checks",
        "required_item_checks",
    },
    "document_blueprint.json": {
        "schema_version",
        "status",
        "document_archetype",
        "document_type",
        "reader_goal",
        "writing_style",
        "front_matter",
        "sections",
        "visual_evidence_plan",
    },
    "official_sources.json": {
        "schema_version",
        "status",
        "policy",
        "checked_at",
        "appendix_heading",
        "reason",
        "claims",
        "privacy",
    },
}


def _json_path(value: Any, label: str) -> list[str | int]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    if len(value) > MAX_JSON_PATH_DEPTH:
        raise ValueError(f"{label} exceeds the maximum path depth")
    result: list[str | int] = []
    for index, component in enumerate(value):
        if isinstance(component, str) and component:
            result.append(component)
        elif isinstance(component, int) and not isinstance(component, bool) and component >= 0:
            result.append(component)
        else:
            raise ValueError(f"{label}[{index}] must be a non-empty key or index")
    return result


def _set_json_path(document: Any, path: Sequence[str | int], value: Any, label: str) -> None:
    parent = document
    for index, component in enumerate(path[:-1]):
        if isinstance(parent, dict) and isinstance(component, str):
            if component not in parent:
                raise ValueError(f"{label} intermediate path does not exist at {path[:index + 1]}")
            parent = parent[component]
        elif isinstance(parent, list) and isinstance(component, int):
            if component >= len(parent):
                raise ValueError(f"{label} list index is out of range at {path[:index + 1]}")
            parent = parent[component]
        else:
            raise ValueError(f"{label} path type does not match at {path[:index + 1]}")

    final = path[-1]
    replacement = copy.deepcopy(value)
    if isinstance(parent, dict) and isinstance(final, str):
        parent[final] = replacement
    elif isinstance(parent, list) and isinstance(final, int):
        if final >= len(parent):
            raise ValueError(f"{label} final list index is out of range")
        parent[final] = replacement
    else:
        raise ValueError(f"{label} final path type does not match")


def apply_content_repair_patch(job_dir: Path) -> dict[str, int]:
    job_dir = job_dir.expanduser().resolve()
    patch_path = job_dir / PATCH_NAME
    if patch_path.stat().st_size > MAX_PATCH_BYTES:
        raise ValueError(f"{PATCH_NAME} exceeds {MAX_PATCH_BYTES} bytes")
    patch = read_json(patch_path)
    if not isinstance(patch, dict):
        raise ValueError(f"{PATCH_NAME} must contain a JSON object")
    unknown_keys = set(patch) - ALLOWED_PATCH_KEYS
    if unknown_keys:
        raise ValueError(f"{PATCH_NAME} has unsupported keys: {sorted(unknown_keys)}")
    if patch.get("schema_version") != 1:
        raise ValueError(f"{PATCH_NAME} schema_version must be 1")

    json_updates = patch.get("json_updates", [])
    markdown_replacements = patch.get("markdown_replacements", [])
    if not isinstance(json_updates, list) or not isinstance(markdown_replacements, list):
        raise ValueError("patch update fields must be lists")
    if not json_updates and not markdown_replacements:
        raise ValueError("patch must contain at least one update")
    if len(json_updates) > MAX_JSON_UPDATES:
        raise ValueError(f"patch exceeds {MAX_JSON_UPDATES} JSON updates")
    if len(markdown_replacements) > MAX_MARKDOWN_REPLACEMENTS:
        raise ValueError(
            f"patch exceeds {MAX_MARKDOWN_REPLACEMENTS} Markdown replacements"
        )

    documents: dict[str, dict[str, Any]] = {}
    seen_paths: set[tuple[str, tuple[str | int, ...]]] = set()
    prepared_updates: list[tuple[str, list[str | int], Any, str]] = []
    for index, update in enumerate(json_updates):
        label = f"json_updates[{index}]"
        if not isinstance(update, dict) or set(update) != {"file", "path", "value"}:
            raise ValueError(f"{label} must contain only file, path, and value")
        name = update.get("file")
        if not isinstance(name, str) or name not in MODEL_OWNED_FIELDS:
            raise ValueError(f"{label}.file is not repairable")
        path = _json_path(update.get("path"), f"{label}.path")
        first = path[0]
        if not isinstance(first, str) or first not in MODEL_OWNED_FIELDS[name]:
            raise ValueError(f"{label}.path must target a model-owned field")
        identity = (name, tuple(path))
        if identity in seen_paths:
            raise ValueError(f"{label}.path is duplicated")
        seen_paths.add(identity)
        if name not in documents:
            document = read_json(job_dir / name)
            if not isinstance(document, dict):
                raise ValueError(f"{name} must contain a JSON object")
            documents[name] = copy.deepcopy(document)
        prepared_updates.append((name, path, update.get("value"), label))

    minutes_path = job_dir / "minutes.md"
    minutes_text: str | None = None
    prepared_replacements: list[tuple[str, str, str]] = []
    for index, replacement in enumerate(markdown_replacements):
        label = f"markdown_replacements[{index}]"
        if not isinstance(replacement, dict) or set(replacement) != {
            "old",
            "new",
            "expected_count",
        }:
            raise ValueError(f"{label} must contain only old, new, and expected_count")
        old = replacement.get("old")
        new = replacement.get("new")
        expected_count = replacement.get("expected_count")
        if not isinstance(old, str) or not old:
            raise ValueError(f"{label}.old must be a non-empty string")
        if not isinstance(new, str) or new == old:
            raise ValueError(f"{label}.new must be a different string")
        if expected_count != 1:
            raise ValueError(f"{label}.expected_count must be 1")
        prepared_replacements.append((old, new, label))

    for name, path, value, label in prepared_updates:
        _set_json_path(documents[name], path, value, label)

    if prepared_replacements:
        minutes_text = minutes_path.read_text(encoding="utf-8")
        for old, new, label in prepared_replacements:
            count = minutes_text.count(old)
            if count != 1:
                raise ValueError(f"{label} expected 1 occurrence but found {count}")
            minutes_text = minutes_text.replace(old, new, 1)

    for name in sorted(documents):
        write_json(job_dir / name, documents[name])
    if minutes_text is not None:
        minutes_path.write_text(minutes_text, encoding="utf-8")
    patch_path.unlink()
    return {
        "json_updates": len(prepared_updates),
        "markdown_replacements": len(prepared_replacements),
        "files_changed": len(documents) + int(minutes_text is not None),
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Apply one bounded content-repair patch without emitting large diffs."
    )
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = apply_content_repair_patch(args.job_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
