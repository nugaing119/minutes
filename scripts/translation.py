from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.content_freeze import validate_content_freeze
from scripts.content_quality import (
    TRUST_SECTION_HEADINGS,
    korean_meeting_report_style_issues,
)
from scripts.document_language import language_policy_from_status, normalize_language
from scripts.utils import now_local, read_json, write_json


SCHEMA_VERSION = 1
SOURCE_NAME = "minutes.md"
TARGET_NAME = "minutes.translated.md"
MANIFEST_NAME = "translation_manifest.json"
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
TABLE_ROW_PATTERN = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
CHECKLIST_PATTERN = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+", re.MULTILINE)
BULLET_PATTERN = re.compile(r"^\s*[-*]\s+(?!\[[ xX]\]\s+)", re.MULTILINE)
NUMBERED_PATTERN = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
IMAGE_TARGET_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LINK_TARGET_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")
EVIDENCE_REF_PATTERN = re.compile(
    r"(?:STT:\d{2}:\d{2}:\d{2}-\d{2}:\d{2}:\d{2}"
    r"|OCR:\d{2}:\d{2}:\d{2}"
    r"|Snapshot:snapshot[-_]\d+@\d{2}:\d{2}:\d{2})"
)
TIMECODE_PATTERN = re.compile(
    r"(?<!\d)\d{2}:\d{2}:\d{2}(?:-\d{2}:\d{2}:\d{2})?(?!\d)"
)
NUMERIC_LITERAL_PATTERN = re.compile(r"(?<!\d)\d+(?:[.,]\d+)*(?!\d)")
OUTPUT_METADATA_PATTERN = re.compile(
    r"(?mi)^\s*-\s*(?:Output language|출력 언어)\s*:\s*(.+?)\s*$"
)
H2_HEADING_PATTERN = re.compile(r"(?m)^##[ \t]+(.+?)[ \t]*$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _table_cell_counts(markdown: str) -> list[int]:
    counts = []
    for match in TABLE_ROW_PATTERN.finditer(markdown):
        row = match.group(0).strip().strip("|")
        counts.append(len(row.split("|")))
    return counts


def markdown_signature(markdown: str) -> dict[str, Any]:
    return {
        "heading_levels": [len(value) for value in HEADING_PATTERN.findall(markdown)],
        "table_cell_counts": _table_cell_counts(markdown),
        "checklist_marks": [value.lower() for value in CHECKLIST_PATTERN.findall(markdown)],
        "bullet_count": len(BULLET_PATTERN.findall(markdown)),
        "numbered_count": len(NUMBERED_PATTERN.findall(markdown)),
        "image_targets": IMAGE_TARGET_PATTERN.findall(markdown),
        "link_targets": LINK_TARGET_PATTERN.findall(markdown),
        "inline_code": INLINE_CODE_PATTERN.findall(markdown),
        "evidence_refs": EVIDENCE_REF_PATTERN.findall(markdown),
        "timecodes": TIMECODE_PATTERN.findall(markdown),
        "numeric_literals": NUMERIC_LITERAL_PATTERN.findall(markdown),
    }


def _counter(values: list[str]) -> Counter[str]:
    return Counter(values)


def _target_metadata_is_valid(source: str, target: str, target_language: str) -> bool:
    if not OUTPUT_METADATA_PATTERN.search(source):
        return True
    values = [match.strip().lower() for match in OUTPUT_METADATA_PATTERN.findall(target)]
    normalized = normalize_language(target_language)
    if normalized == "ko":
        return any(value in {"한국어", "korean"} for value in values)
    if normalized == "en":
        return any(value in {"영어", "english"} for value in values)
    return False


def validate_translation_text(
    source: str,
    target: str,
    *,
    target_language: str,
    writing_style: str | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    if not target.strip():
        issues.append("translated Markdown is empty")
    if target.lstrip().startswith("```"):
        issues.append("translated Markdown must not be wrapped in a code fence")
    first_line = next((line for line in target.splitlines() if line.strip()), "")
    if not first_line.startswith("# "):
        issues.append("translated Markdown must start with one H1")

    source_signature = markdown_signature(source)
    target_signature = markdown_signature(target)
    for key in (
        "heading_levels",
        "table_cell_counts",
        "checklist_marks",
        "bullet_count",
        "numbered_count",
    ):
        if source_signature[key] != target_signature[key]:
            issues.append(f"Markdown structure changed: {key}")
    for key in (
        "image_targets",
        "link_targets",
        "inline_code",
        "evidence_refs",
        "timecodes",
    ):
        if _counter(source_signature[key]) != _counter(target_signature[key]):
            issues.append(f"protected {key.replace('_', ' ')} changed")
    missing_numeric_literals = _counter(source_signature["numeric_literals"]) - _counter(
        target_signature["numeric_literals"]
    )
    if missing_numeric_literals:
        issues.append("protected numeric literals changed")

    if not _target_metadata_is_valid(source, target, target_language):
        issues.append("target-language metadata is missing or incorrect")
    normalized = normalize_language(target_language)
    if normalized == "ko" and not re.search(r"[가-힣]", target):
        issues.append("Korean target contains no Hangul")
    if normalized == "en" and not re.search(r"[A-Za-z]", target):
        issues.append("English target contains no Latin text")
    if normalized == "ko" and writing_style == "meeting_minutes_objective":
        issues.extend(korean_meeting_report_style_issues(target))
    source_h2s = H2_HEADING_PATTERN.findall(source)
    target_h2s = H2_HEADING_PATTERN.findall(target)
    source_trust_roles = [
        role
        for role, headings in TRUST_SECTION_HEADINGS.items()
        if any(heading in set(headings.values()) for heading in source_h2s)
    ]
    if source_trust_roles and normalized in {"ko", "en"}:
        for role in source_trust_roles:
            expected_heading = TRUST_SECTION_HEADINGS[role][normalized]
            if expected_heading not in target_h2s:
                issues.append(
                    f"translated Markdown is missing the canonical trust heading: "
                    f"## {expected_heading}"
                )
        if set(source_trust_roles) == {"open_questions", "external_evidence"}:
            expected_tail = [
                TRUST_SECTION_HEADINGS["open_questions"][normalized],
                TRUST_SECTION_HEADINGS["external_evidence"][normalized],
            ]
            if target_h2s[-2:] != expected_tail:
                issues.append(
                    "translated Markdown must keep the canonical trust headings as "
                    "the final two H2 sections"
                )
    if issues:
        raise ValueError("translation validation failed: " + "; ".join(issues))
    return {
        "structure_preserved": True,
        "protected_literals_preserved": True,
        "target_language_metadata": "passed",
        "document_voice": "passed",
        "model_review_cycles": 0,
        "source_signature": source_signature,
        "target_signature": target_signature,
    }


def create_translation_manifest(job_dir: Path) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    status = read_json(job_dir / "status.json")
    policy = language_policy_from_status(status)
    if not policy["translation_required"]:
        raise ValueError("job language policy does not require translation")
    freeze = validate_content_freeze(job_dir)
    source_path = job_dir / SOURCE_NAME
    target_path = job_dir / TARGET_NAME
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not target_path.is_file():
        raise FileNotFoundError(target_path)
    source = source_path.read_text(encoding="utf-8")
    target = target_path.read_text(encoding="utf-8")
    blueprint = read_json(job_dir / "document_blueprint.json")
    writing_style = str(blueprint.get("writing_style", "")).strip() or None
    checks = validate_translation_text(
        source,
        target,
        target_language=str(policy["output_language"]),
        writing_style=writing_style,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "created_at": now_local().isoformat(),
        "source_language": normalize_language(policy["detected_language"]),
        "target_language": normalize_language(policy["output_language"]),
        "writing_style": writing_style,
        "content_freeze_sha256": str(freeze["content_sha256"]),
        "source": file_record(source_path),
        "target": file_record(target_path),
        "checks": checks,
    }
    write_json(job_dir / MANIFEST_NAME, result)
    return result


def validate_translation_manifest(job_dir: Path) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    status = read_json(job_dir / "status.json")
    policy = language_policy_from_status(status)
    if not policy["translation_required"]:
        raise ValueError("job language policy does not require translation")
    freeze = validate_content_freeze(job_dir)
    manifest = read_json(job_dir / MANIFEST_NAME)
    issues: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"translation manifest schema_version must be {SCHEMA_VERSION}")
    if manifest.get("status") != "passed":
        issues.append("translation manifest status must be passed")
    if manifest.get("source_language") != normalize_language(policy["detected_language"]):
        issues.append("translation source language does not match job policy")
    if manifest.get("target_language") != normalize_language(policy["output_language"]):
        issues.append("translation target language does not match job policy")
    blueprint = read_json(job_dir / "document_blueprint.json")
    writing_style = str(blueprint.get("writing_style", "")).strip() or None
    if manifest.get("writing_style") != writing_style:
        issues.append("translation writing style does not match document blueprint")
    if manifest.get("content_freeze_sha256") != freeze.get("content_sha256"):
        issues.append("translation source freeze hash does not match")

    source_path = job_dir / SOURCE_NAME
    target_path = job_dir / TARGET_NAME
    if not source_path.is_file():
        issues.append("translation source is missing")
    elif manifest.get("source") != file_record(source_path):
        issues.append("translation source hash does not match")
    if not target_path.is_file():
        issues.append("translation target is missing")
    elif manifest.get("target") != file_record(target_path):
        issues.append("translation target hash does not match")
    if not issues:
        try:
            validate_translation_text(
                source_path.read_text(encoding="utf-8"),
                target_path.read_text(encoding="utf-8"),
                target_language=str(policy["output_language"]),
                writing_style=writing_style,
            )
        except ValueError as exc:
            issues.append(str(exc))
    if issues:
        raise ValueError("translation manifest validation failed: " + "; ".join(issues))
    return manifest


def resolve_final_markdown(job_dir: Path) -> Path:
    job_dir = job_dir.expanduser().resolve()
    status = read_json(job_dir / "status.json")
    policy = language_policy_from_status(status)
    if policy["translation_required"]:
        validate_translation_manifest(job_dir)
        return job_dir / TARGET_NAME
    source_path = job_dir / SOURCE_NAME
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    return source_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Finalize or verify a one-pass translated Markdown document."
    )
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--print-path", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.print_path:
            print(resolve_final_markdown(args.job_dir))
            return
        result = (
            validate_translation_manifest(args.job_dir)
            if args.verify
            else create_translation_manifest(args.job_dir)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "status": result["status"],
                "source_language": result["source_language"],
                "target_language": result["target_language"],
                "target_sha256": result["target"]["sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
