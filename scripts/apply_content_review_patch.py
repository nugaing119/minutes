from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.content_quality import REQUIRED_ITEM_DIMENSIONS
from scripts.utils import read_json, write_json


PATCH_NAME = "content_review_patch.json"
ALLOWED_PATCH_KEYS = {
    "schema_version",
    "audit_coverage_updates",
    "review_dimension_updates",
}


def _nonempty_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    result = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(result) != len(value) or not result:
        raise ValueError(f"{label} must contain only nonempty strings")
    return result


def _unique_item(items: Any, item_id: str, *, id_field: str, label: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise ValueError(f"{label} must be a list")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get(id_field) == item_id
    ]
    if len(matches) != 1:
        raise ValueError(f"{label} must contain exactly one {item_id}")
    return matches[0]


def apply_content_review_patch(job_dir: Path) -> dict[str, int]:
    job_dir = job_dir.expanduser().resolve()
    patch_path = job_dir / PATCH_NAME
    patch = read_json(patch_path)
    unknown_keys = set(patch) - ALLOWED_PATCH_KEYS
    if unknown_keys:
        raise ValueError(f"{PATCH_NAME} has unsupported keys: {sorted(unknown_keys)}")
    if patch.get("schema_version") != 1:
        raise ValueError(f"{PATCH_NAME} schema_version must be 1")

    audit_updates = patch.get("audit_coverage_updates", [])
    review_updates = patch.get("review_dimension_updates", [])
    if not isinstance(audit_updates, list) or not isinstance(review_updates, list):
        raise ValueError("patch update fields must be lists")
    if not audit_updates and not review_updates:
        raise ValueError("patch must contain at least one update")

    audit_path = job_dir / "content_audit.json"
    review_path = job_dir / "content_quality_review.json"
    audit = read_json(audit_path)
    review = read_json(review_path)

    for index, update in enumerate(audit_updates):
        label = f"audit_coverage_updates[{index}]"
        if not isinstance(update, dict) or set(update) != {"item_id", "document_refs"}:
            raise ValueError(f"{label} must contain only item_id and document_refs")
        item_id = update.get("item_id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError(f"{label}.item_id is required")
        record = _unique_item(
            audit.get("coverage"),
            item_id.strip(),
            id_field="item_id",
            label="content_audit.json coverage",
        )
        record["document_refs"] = _nonempty_strings(
            update.get("document_refs"),
            f"{label}.document_refs",
        )

    for index, update in enumerate(review_updates):
        label = f"review_dimension_updates[{index}]"
        if not isinstance(update, dict):
            raise ValueError(f"{label} must be an object")
        allowed = {"item_id", "dimension", "status", "document_refs", "rationale"}
        if set(update) - allowed:
            raise ValueError(f"{label} contains unsupported keys")
        item_id = update.get("item_id")
        dimension = update.get("dimension")
        status = update.get("status")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError(f"{label}.item_id is required")
        if dimension not in REQUIRED_ITEM_DIMENSIONS:
            raise ValueError(f"{label}.dimension is invalid")
        record = _unique_item(
            review.get("required_item_checks"),
            item_id.strip(),
            id_field="item_id",
            label="content_quality_review.json required_item_checks",
        )
        dimensions = record.get("dimensions")
        if not isinstance(dimensions, dict) or dimension not in dimensions:
            raise ValueError(f"{label}.dimension is missing from the review item")
        if status == "covered":
            dimensions[dimension] = {
                "status": "covered",
                "document_refs": _nonempty_strings(
                    update.get("document_refs"),
                    f"{label}.document_refs",
                ),
            }
        elif status == "not_applicable":
            rationale = update.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError(f"{label}.rationale is required")
            dimensions[dimension] = {
                "status": "not_applicable",
                "rationale": rationale.strip(),
            }
        else:
            raise ValueError(f"{label}.status must be covered or not_applicable")

    if audit_updates:
        write_json(audit_path, audit)
    if review_updates:
        write_json(review_path, review)
    patch_path.unlink()
    return {
        "audit_coverage_updates": len(audit_updates),
        "review_dimension_updates": len(review_updates),
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Apply a bounded exact-reference patch without rewriting large sidecars."
    )
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = apply_content_review_patch(args.job_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
