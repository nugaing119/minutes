from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from collections import Counter
from collections.abc import Sequence
from typing import Any

from scripts.utils import read_json, write_json


LEDGER_CLASSIFICATIONS = {"material", "mixed", "repetition", "technical_noise"}
REQUIRED_FINAL_CHECKS = (
    "overcompression",
    "inventory_granularity",
    "question_answer_retention",
    "conditions_exceptions_risks",
    "document_archetype_fit",
    "evidence_citation_usability",
    "visual_evidence_plan",
    "reader_usability",
    "front_matter_completeness",
    "section_cohesion",
    "operational_utility",
    "reader_facing_evidence",
    "form_factor_fit",
)
MODEL_FINAL_CHECKS = (
    "overcompression",
    "inventory_granularity",
    "question_answer_retention",
    "conditions_exceptions_risks",
    "document_archetype_fit",
    "evidence_citation_usability",
    "visual_evidence_plan",
    "reader_usability",
)
BLUEPRINT_ARCHETYPES = {
    "technical_session_analysis",
    "product_demo_analysis",
    "technical_decision_record",
    "strategy_session_analysis",
    "general_recording_analysis",
}
BLUEPRINT_ROLES = {
    "executive_synthesis",
    "session_context",
    "speaker_map",
    "topic_analysis",
    "operational_actions",
    "open_questions",
    "evidence_appendix",
    "external_evidence",
}
BLUEPRINT_FORM_FACTORS = {
    "prose",
    "grouped_bullets",
    "table",
    "checklist",
    "timeline",
    "definition_list",
    "mixed",
    "source_list",
}
REQUIRED_FRONT_MATTER_KEYS = {
    "source",
    "recording_datetime",
    "duration",
    "source_language",
    "output_language",
    "evidence_basis",
    "external_evidence_policy",
}
ACTION_CATEGORIES = {
    "action",
    "availability",
    "decision",
    "follow_up",
    "iam",
    "open_question",
    "policy",
    "roadmap",
    "retention",
}
OPEN_QUESTION_CATEGORIES = {
    "follow_up",
    "limitation",
    "open_question",
    "roadmap",
    "uncertainty",
}
RAW_EVIDENCE_REF_RE = re.compile(r"\b(?:STT|OCR|Snapshot):", re.I)
MARKDOWN_TABLE_SEPARATOR_RE = re.compile(
    r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$"
)


def validate_content_quality_artifacts(
    job_dir: Path,
    *,
    inventory_ids: set[str],
    required_ids: set[str],
    minutes_text: str,
    issues: list[str],
) -> dict[str, Any]:
    """Validate the fresh-worker evidence ledger and adversarial review artifact."""
    manifest_path = job_dir / "evidence_chunks.json"
    if not manifest_path.is_file():
        return {"required": False, "status": "not_required"}

    initial_issue_count = len(issues)
    manifest = _read_object(manifest_path, issues)
    inventory = _read_object(job_dir / "content_inventory.json", issues)
    ledger_path = job_dir / "evidence_ledger.json"
    blueprint_path = job_dir / "document_blueprint.json"
    review_path = job_dir / "content_quality_review.json"
    ledger = _read_object(ledger_path, issues)
    blueprint = _read_object(blueprint_path, issues)
    review = _read_object(review_path, issues)
    manifest_chunks = manifest.get("chunks")
    if not isinstance(manifest_chunks, list) or not manifest_chunks:
        issues.append("evidence_chunks.json chunks must be a non-empty list")
        manifest_chunks = []

    expected_chunks: dict[int, dict[str, Any]] = {}
    for item in manifest_chunks:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
            issues.append("evidence_chunks.json contains an invalid chunk record")
            continue
        expected_chunks[item["index"]] = item

    ledger_inventory_ids = _validate_ledger(
        ledger,
        expected_chunks=expected_chunks,
        inventory_ids=inventory_ids,
        issues=issues,
    )
    missing_required = required_ids - ledger_inventory_ids
    if missing_required:
        issues.append(
            "required inventory items are not mapped from the evidence ledger: "
            f"{sorted(missing_required)}"
        )

    blueprint_summary = _validate_blueprint(
        blueprint,
        inventory=inventory,
        inventory_ids=inventory_ids,
        required_ids=required_ids,
        minutes_text=minutes_text,
        official_sources=_read_optional_object(job_dir / "official_sources.json"),
        issues=issues,
    )
    _validate_review(
        review,
        job_dir=job_dir,
        expected_chunk_indexes=set(expected_chunks),
        inventory_ids=inventory_ids,
        minutes_text=minutes_text,
        blueprint_summary=blueprint_summary,
        issues=issues,
    )

    return {
        "required": True,
        "status": "passed" if len(issues) == initial_issue_count else "failed",
        "chunk_count": len(expected_chunks),
        "ledger_inventory_items": len(ledger_inventory_ids),
        "blueprint_sections": blueprint_summary.get("section_count", 0),
        "reader_body_raw_evidence_refs": blueprint_summary.get(
            "reader_body_raw_evidence_refs",
            0,
        ),
        "review_cycles": len(review.get("review_cycles", []))
        if isinstance(review.get("review_cycles"), list)
        else 0,
        "minutes_bytes": len(minutes_text.encode("utf-8")),
    }


def _validate_ledger(
    ledger: dict[str, Any],
    *,
    expected_chunks: dict[int, dict[str, Any]],
    inventory_ids: set[str],
    issues: list[str],
) -> set[str]:
    if not ledger:
        return set()
    if ledger.get("schema_version") != 1:
        issues.append("evidence_ledger.json schema_version must be 1")
    chunks = ledger.get("chunks")
    if not isinstance(chunks, list):
        issues.append("evidence_ledger.json chunks must be a list")
        return set()

    seen_indexes: set[int] = set()
    mapped_inventory_ids: set[str] = set()
    for position, chunk in enumerate(chunks):
        label = f"evidence_ledger.json chunks[{position}]"
        if not isinstance(chunk, dict):
            issues.append(f"{label} must be an object")
            continue
        index = chunk.get("index")
        if not isinstance(index, int) or index not in expected_chunks:
            issues.append(f"{label}.index must resolve to evidence_chunks.json")
            continue
        if index in seen_indexes:
            issues.append(f"duplicate evidence ledger chunk index: {index}")
            continue
        seen_indexes.add(index)
        expected = expected_chunks[index]
        if chunk.get("source_sha256") != expected.get("sha256"):
            issues.append(f"{label}.source_sha256 does not match the chunk manifest")
        classification = chunk.get("classification")
        if classification not in LEDGER_CLASSIFICATIONS:
            issues.append(
                f"{label}.classification must be one of "
                f"{sorted(LEDGER_CLASSIFICATIONS)}"
            )
        rationale = _nonempty_string(chunk.get("rationale"))
        if not rationale:
            issues.append(f"{label}.rationale is required")
        topics = chunk.get("material_topics")
        if not isinstance(topics, list):
            issues.append(f"{label}.material_topics must be a list")
            topics = []
        topic_values = [value for value in topics if _nonempty_string(value)]
        item_ids = _string_set(chunk.get("inventory_item_ids"), label, issues)
        unknown_ids = item_ids - inventory_ids
        if unknown_ids:
            issues.append(f"{label} references unknown inventory ids: {sorted(unknown_ids)}")
        mapped_inventory_ids.update(item_ids & inventory_ids)
        if classification in {"material", "mixed"}:
            if not topic_values:
                issues.append(f"{label} material chunk requires material_topics")
            if not item_ids:
                issues.append(f"{label} material chunk requires inventory_item_ids")
        elif item_ids:
            issues.append(
                f"{label} non-material chunk must not map inventory_item_ids"
            )

    missing_indexes = set(expected_chunks) - seen_indexes
    extra_indexes = seen_indexes - set(expected_chunks)
    if missing_indexes:
        issues.append(f"evidence ledger is missing chunks: {sorted(missing_indexes)}")
    if extra_indexes:
        issues.append(f"evidence ledger contains unknown chunks: {sorted(extra_indexes)}")
    if ledger.get("chunk_count") != len(expected_chunks):
        issues.append("evidence_ledger.json chunk_count does not match the manifest")
    if ledger.get("status") != "completed":
        issues.append("evidence_ledger.json status must be completed")
    return mapped_inventory_ids


def _validate_blueprint(
    blueprint: dict[str, Any],
    *,
    inventory: dict[str, Any],
    inventory_ids: set[str],
    required_ids: set[str],
    minutes_text: str,
    official_sources: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    signals = markdown_document_signals(minutes_text)
    summary: dict[str, Any] = {
        "section_count": 0,
        "reader_body_raw_evidence_refs": signals["raw_evidence_refs"],
        "document_signals": signals,
    }
    if not blueprint:
        return summary
    if blueprint.get("schema_version") != 1:
        issues.append("document_blueprint.json schema_version must be 1")
    if blueprint.get("status") != "completed":
        issues.append("document_blueprint.json status must be completed")
    archetype = _nonempty_string(blueprint.get("document_archetype"))
    if archetype not in BLUEPRINT_ARCHETYPES:
        issues.append(
            "document_blueprint.json document_archetype must be one of "
            f"{sorted(BLUEPRINT_ARCHETYPES)}"
        )
    document_type = _nonempty_string(blueprint.get("document_type"))
    if not document_type:
        issues.append("document_blueprint.json document_type is required")
    if not _nonempty_string(blueprint.get("reader_goal")):
        issues.append("document_blueprint.json reader_goal is required")

    preamble, rendered_sections = _split_h2_sections(minutes_text)
    h1_matches = re.findall(r"(?m)^#[ \t]+(.+?)\s*$", minutes_text)
    if len(h1_matches) != 1:
        issues.append("minutes.md must contain exactly one H1 display title")
    if document_type and not re.search(
        rf"(?mi)^(?:문서 유형|Document type)\s*:\s*{re.escape(document_type)}\s*$",
        preamble,
    ):
        issues.append(
            "minutes.md front matter must contain the blueprint document type line"
        )

    front_matter = blueprint.get("front_matter")
    seen_front_keys: set[str] = set()
    if not isinstance(front_matter, list):
        issues.append("document_blueprint.json front_matter must be a list")
    else:
        for index, item in enumerate(front_matter):
            label = f"document_blueprint.json front_matter[{index}]"
            if not isinstance(item, dict):
                issues.append(f"{label} must be an object")
                continue
            key = _nonempty_string(item.get("key"))
            display_label = _nonempty_string(item.get("label"))
            value = _nonempty_string(item.get("value"))
            if not key or not display_label or not value:
                issues.append(f"{label} key, label, and value are required")
                continue
            if key in seen_front_keys:
                issues.append(f"duplicate document blueprint front-matter key: {key}")
            seen_front_keys.add(key)
            expected_line = f"- {display_label}: {value}"
            if expected_line not in preamble:
                issues.append(f"{label} was not found verbatim in minutes.md front matter")
    missing_front_keys = REQUIRED_FRONT_MATTER_KEYS - seen_front_keys
    if missing_front_keys:
        issues.append(
            "document_blueprint.json front_matter is missing required keys: "
            f"{sorted(missing_front_keys)}"
        )

    inventory_items = inventory.get("items")
    inventory_categories: dict[str, str] = {}
    if isinstance(inventory_items, list):
        for item in inventory_items:
            if not isinstance(item, dict):
                continue
            item_id = _nonempty_string(item.get("id"))
            category = _nonempty_string(item.get("category"))
            if item_id and category:
                inventory_categories[item_id] = category
    conflict_count = len(inventory.get("conflicts", [])) if isinstance(
        inventory.get("conflicts"), list
    ) else 0

    sections = blueprint.get("sections")
    if not isinstance(sections, list) or not sections:
        issues.append("document_blueprint.json sections must be a non-empty list")
        return summary
    summary["section_count"] = len(sections)
    seen_section_ids: set[str] = set()
    seen_headings: set[str] = set()
    role_counts: Counter[str] = Counter()
    primary_assignment: Counter[str] = Counter()
    blueprint_headings: list[str] = []
    reader_body_raw_refs = 0
    action_section_applicability: str | None = None
    open_section_applicability: str | None = None

    for index, section in enumerate(sections):
        label = f"document_blueprint.json sections[{index}]"
        if not isinstance(section, dict):
            issues.append(f"{label} must be an object")
            continue
        section_id = _nonempty_string(section.get("id"))
        heading = _nonempty_string(section.get("heading"))
        role = _nonempty_string(section.get("role"))
        form_factor = _nonempty_string(section.get("form_factor"))
        applicability = _nonempty_string(section.get("applicability")) or "required"
        if not section_id or not heading:
            issues.append(f"{label} id and heading are required")
            continue
        if section_id in seen_section_ids:
            issues.append(f"duplicate document blueprint section id: {section_id}")
        if heading in seen_headings:
            issues.append(f"duplicate document blueprint section heading: {heading}")
        seen_section_ids.add(section_id)
        seen_headings.add(heading)
        blueprint_headings.append(heading)
        if role not in BLUEPRINT_ROLES:
            issues.append(f"{label}.role must be one of {sorted(BLUEPRINT_ROLES)}")
            continue
        role_counts[role] += 1
        if form_factor not in BLUEPRINT_FORM_FACTORS:
            issues.append(
                f"{label}.form_factor must be one of {sorted(BLUEPRINT_FORM_FACTORS)}"
            )
        if applicability not in {"required", "not_applicable"}:
            issues.append(f"{label}.applicability must be required or not_applicable")
        rationale = _nonempty_string(section.get("rationale"))
        if applicability == "not_applicable" and not rationale:
            issues.append(f"{label}.rationale is required when not_applicable")

        item_ids = _string_list(
            section.get("primary_inventory_item_ids"),
            f"{label}.primary_inventory_item_ids",
            issues,
        )
        unknown_ids = set(item_ids) - inventory_ids
        if unknown_ids:
            issues.append(f"{label} references unknown inventory ids: {sorted(unknown_ids)}")
        primary_assignment.update(item_id for item_id in item_ids if item_id in inventory_ids)

        section_text = rendered_sections.get(heading, "")
        if not section_text:
            issues.append(f"{label}.heading was not found as an H2 in minutes.md")
            continue
        if applicability == "not_applicable" and rationale and rationale not in section_text:
            issues.append(f"{label}.rationale was not found in its minutes.md section")
        form_signals = _section_form_signals(section_text)
        if applicability == "required":
            _validate_form_factor(
                form_factor,
                form_signals,
                label=label,
                issues=issues,
            )
        if role == "executive_synthesis":
            if applicability != "required" or form_signals["bullet_count"] < 3:
                issues.append(
                    f"{label} executive_synthesis requires at least three grouped bullets"
                )
        if role == "operational_actions":
            action_section_applicability = applicability
            if applicability == "required" and max(
                form_signals["bullet_count"],
                form_signals["table_data_rows"],
            ) < 3:
                issues.append(
                    f"{label} operational_actions requires at least three actionable entries"
                )
        if role == "open_questions":
            open_section_applicability = applicability
        if role not in {"evidence_appendix", "external_evidence"}:
            reader_body_raw_refs += len(RAW_EVIDENCE_REF_RE.findall(section_text))

    rendered_headings = list(rendered_sections)
    if blueprint_headings != rendered_headings:
        issues.append(
            "document_blueprint.json section headings and order must exactly match minutes.md H2s"
        )
    for role in ("executive_synthesis", "open_questions"):
        if role_counts[role] != 1:
            issues.append(f"document_blueprint.json requires exactly one {role} section")
    if role_counts["topic_analysis"] < 1:
        issues.append("document_blueprint.json requires at least one topic_analysis section")
    if role_counts["topic_analysis"] > 6:
        issues.append(
            "document_blueprint.json has more than six topic_analysis H2 sections; "
            "group related topics under H3 subsections"
        )
    duplicate_assignments = sorted(
        item_id
        for item_id, count in primary_assignment.items()
        if item_id in required_ids and count > 1
    )
    if duplicate_assignments:
        issues.append(
            "required inventory items must have one primary blueprint section: "
            f"{duplicate_assignments}"
        )
    missing_primary = required_ids - set(primary_assignment)
    if missing_primary:
        issues.append(
            "required inventory items lack a primary blueprint section: "
            f"{sorted(missing_primary)}"
        )

    required_categories = {
        inventory_categories[item_id]
        for item_id in required_ids
        if item_id in inventory_categories
    }
    if "speaker_roles" in required_categories:
        if role_counts["speaker_map"] != 1:
            issues.append(
                "speaker_roles evidence requires exactly one speaker_map section"
            )
    if required_categories & ACTION_CATEGORIES:
        if role_counts["operational_actions"] != 1:
            issues.append(
                "actionable evidence requires exactly one operational_actions section"
            )
        elif action_section_applicability != "required":
            issues.append(
                "operational_actions cannot be not_applicable when actionable evidence exists"
            )
    if required_categories & OPEN_QUESTION_CATEGORIES and open_section_applicability != (
        "required"
    ):
        issues.append(
            "open_questions cannot be not_applicable when unresolved evidence exists"
        )

    claims = official_sources.get("claims")
    if isinstance(claims, list) and claims:
        if role_counts["external_evidence"] != 1:
            issues.append(
                "official-source claims require exactly one external_evidence section"
            )
        elif not isinstance(sections[-1], dict) or sections[-1].get("role") != (
            "external_evidence"
        ):
            issues.append("the external_evidence section must be the final H2")

    raw_ref_allowance = max(2, conflict_count * 2)
    if reader_body_raw_refs > raw_ref_allowance:
        issues.append(
            "reader-facing body contains too many raw STT/OCR/Snapshot references: "
            f"{reader_body_raw_refs} > {raw_ref_allowance}; keep traceability in sidecars "
            "and evidence appendices"
        )
    summary["reader_body_raw_evidence_refs"] = reader_body_raw_refs
    summary["document_signals"] = {
        **signals,
        "reader_body_raw_evidence_refs": reader_body_raw_refs,
    }
    return summary


def _validate_form_factor(
    form_factor: str | None,
    signals: dict[str, int],
    *,
    label: str,
    issues: list[str],
) -> None:
    if form_factor == "grouped_bullets" and signals["bullet_count"] < 3:
        issues.append(f"{label} grouped_bullets requires at least three bullets")
    elif form_factor in {"table", "timeline"} and signals["table_count"] < 1:
        issues.append(f"{label} {form_factor} requires a Markdown table")
    elif form_factor == "checklist" and max(
        signals["bullet_count"], signals["table_data_rows"]
    ) < 3:
        issues.append(f"{label} checklist requires at least three entries")
    elif form_factor == "definition_list" and signals["definition_like_count"] < 2:
        issues.append(f"{label} definition_list requires at least two labeled entries")
    elif form_factor == "source_list" and signals["link_count"] < 1:
        issues.append(f"{label} source_list requires at least one Markdown link")
    elif form_factor == "prose" and signals["prose_block_count"] < 1:
        issues.append(f"{label} prose requires at least one prose block")
    elif form_factor == "mixed":
        present = sum(
            (
                signals["bullet_count"] > 0,
                signals["table_count"] > 0,
                signals["prose_block_count"] > 0,
            )
        )
        if present < 2:
            issues.append(f"{label} mixed requires at least two form factors")


def _split_h2_sections(minutes_text: str) -> tuple[str, dict[str, str]]:
    matches = list(re.finditer(r"(?m)^##[ \t]+(.+?)\s*$", minutes_text))
    if not matches:
        return minutes_text, {}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(minutes_text)
        sections[match.group(1).strip()] = minutes_text[match.end() : end].strip()
    return minutes_text[: matches[0].start()], sections


def _section_form_signals(section_text: str) -> dict[str, int]:
    lines = section_text.splitlines()
    table_separators = [
        index
        for index, line in enumerate(lines)
        if MARKDOWN_TABLE_SEPARATOR_RE.match(line.strip())
    ]
    table_data_rows = 0
    for separator_index in table_separators:
        index = separator_index + 1
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            table_data_rows += 1
            index += 1
    prose_blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n", section_text)
        if block.strip()
        and not block.lstrip().startswith(("#", "|", "- ", "* ", "+ "))
    ]
    return {
        "bullet_count": sum(
            bool(re.match(r"^\s*[-*+]\s+", line)) for line in lines
        ),
        "table_count": len(table_separators),
        "table_data_rows": table_data_rows,
        "prose_block_count": len(prose_blocks),
        "definition_like_count": sum(
            bool(re.match(r"^\s*(?:[-*+]\s+)?\*{0,2}[^:：]{1,40}[:：]", line))
            for line in lines
        ),
        "link_count": len(re.findall(r"\[[^\]]+\]\([^)]+\)", section_text)),
    }


def markdown_document_signals(minutes_text: str) -> dict[str, int]:
    lines = minutes_text.splitlines()
    return {
        "minutes_bytes": len(minutes_text.encode("utf-8")),
        "h1_count": sum(bool(re.match(r"^#[ \t]+", line)) for line in lines),
        "h2_count": sum(bool(re.match(r"^##[ \t]+", line)) for line in lines),
        "h3_count": sum(bool(re.match(r"^###[ \t]+", line)) for line in lines),
        "bullet_count": sum(
            bool(re.match(r"^\s*[-*+][ \t]+", line)) for line in lines
        ),
        "table_count": sum(
            bool(MARKDOWN_TABLE_SEPARATOR_RE.match(line.strip())) for line in lines
        ),
        "raw_evidence_refs": len(RAW_EVIDENCE_REF_RE.findall(minutes_text)),
    }


def reader_body_raw_evidence_refs(
    minutes_text: str,
    blueprint: dict[str, Any],
) -> int:
    _, sections = _split_h2_sections(minutes_text)
    evidence_headings = {
        str(section.get("heading", "")).strip()
        for section in blueprint.get("sections", [])
        if isinstance(section, dict)
        and section.get("role") in {"evidence_appendix", "external_evidence"}
    }
    return sum(
        len(RAW_EVIDENCE_REF_RE.findall(text))
        for heading, text in sections.items()
        if heading not in evidence_headings
    )


def finalize_compact_review(job_dir: Path) -> dict[str, Any]:
    """Bind a schema-v3 model review to deterministic artifact facts."""
    review_path = job_dir / "content_quality_review.json"
    review = read_json(review_path)
    if review.get("schema_version") != 3:
        return review

    minutes_path = job_dir / "minutes.md"
    inventory_path = job_dir / "content_inventory.json"
    ledger_path = job_dir / "evidence_ledger.json"
    blueprint_path = job_dir / "document_blueprint.json"
    manifest = read_json(job_dir / "evidence_chunks.json")
    inventory = read_json(inventory_path)
    blueprint = read_json(blueprint_path)
    minutes_text = minutes_path.read_text(encoding="utf-8")
    chunk_indexes = sorted(
        item["index"]
        for item in manifest.get("chunks", [])
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    )
    inventory_items = inventory.get("items", [])
    inventory_count = sum(isinstance(item, dict) for item in inventory_items)
    review["bindings"] = {
        "minutes_sha256": _sha256_file(minutes_path),
        "inventory_sha256": _sha256_file(inventory_path),
        "evidence_ledger_sha256": _sha256_file(ledger_path),
        "document_blueprint_sha256": _sha256_file(blueprint_path),
        "reviewed_chunk_indexes": chunk_indexes,
    }
    review["document_signals"] = {
        **markdown_document_signals(minutes_text),
        "reader_body_raw_evidence_refs": reader_body_raw_evidence_refs(
            minutes_text,
            blueprint,
        ),
        "inventory_item_count": inventory_count,
    }
    write_json(review_path, review)
    return review


def _validate_review(
    review: dict[str, Any],
    *,
    job_dir: Path,
    expected_chunk_indexes: set[int],
    inventory_ids: set[str],
    minutes_text: str,
    blueprint_summary: dict[str, Any],
    issues: list[str],
) -> None:
    if not review:
        return
    schema_version = review.get("schema_version")
    if schema_version not in {2, 3}:
        issues.append("content_quality_review.json schema_version must be 2 or 3")
    if review.get("status") != "passed":
        issues.append("content_quality_review.json status must be passed")

    expected_hashes = {
        "minutes_sha256": _sha256_bytes(minutes_text.encode("utf-8")),
        "inventory_sha256": _sha256_file(job_dir / "content_inventory.json"),
        "evidence_ledger_sha256": _sha256_file(job_dir / "evidence_ledger.json"),
        "document_blueprint_sha256": _sha256_file(
            job_dir / "document_blueprint.json"
        ),
    }
    bindings = review if schema_version == 2 else review.get("bindings", {})
    if not isinstance(bindings, dict):
        bindings = {}
        issues.append("content_quality_review.json bindings must be an object")
    for field, expected in expected_hashes.items():
        if not expected or bindings.get(field) != expected:
            issues.append(f"content_quality_review.json {field} does not match")

    reviewed_indexes = _integer_set(
        bindings.get("reviewed_chunk_indexes"),
        "content_quality_review.json reviewed_chunk_indexes",
        issues,
    )
    if reviewed_indexes != expected_chunk_indexes:
        issues.append(
            "content_quality_review.json reviewed_chunk_indexes must exactly match "
            "the evidence chunk manifest"
        )

    review_cycles = review.get("review_cycles")
    if not isinstance(review_cycles, list) or not review_cycles:
        issues.append("content_quality_review.json review_cycles must be non-empty")
    elif len(review_cycles) > 3:
        issues.append("content_quality_review.json review_cycles must not exceed 3")
    else:
        for index, cycle in enumerate(review_cycles):
            label = f"content_quality_review.json review_cycles[{index}]"
            if not isinstance(cycle, dict):
                issues.append(f"{label} must be an object")
                continue
            if cycle.get("cycle") != index + 1:
                issues.append(f"{label}.cycle must be sequential")
            if cycle.get("status") not in {"passed", "revised"}:
                issues.append(f"{label}.status must be passed or revised")
            findings = cycle.get("findings")
            changes = cycle.get("changes")
            if not isinstance(findings, list) or not isinstance(changes, list):
                issues.append(f"{label} findings and changes must be lists")
            elif cycle.get("status") == "revised":
                if not findings:
                    issues.append(f"{label} revised cycle requires findings")
                if not changes:
                    issues.append(f"{label} revised findings require changes")
        if schema_version == 3 and review_cycles:
            for index, cycle in enumerate(review_cycles[:-1]):
                if isinstance(cycle, dict) and cycle.get("status") != "revised":
                    issues.append(
                        "content_quality_review.json only the final cycle may pass"
                    )
            final_cycle = review_cycles[-1]
            if isinstance(final_cycle, dict) and final_cycle.get("status") != "passed":
                issues.append(
                    "content_quality_review.json final review cycle must pass"
                )
        if schema_version == 3 and len(review_cycles) == 3:
            final_cycle = review_cycles[-1]
            blocker = (
                final_cycle.get("blocking_defect_code")
                if isinstance(final_cycle, dict)
                else None
            )
            if not _nonempty_string(blocker):
                issues.append(
                    "content_quality_review.json third cycle requires "
                    "blocking_defect_code"
                )

    checks = review.get("final_checks")
    if not isinstance(checks, dict):
        issues.append("content_quality_review.json final_checks must be an object")
        return
    required_checks = REQUIRED_FINAL_CHECKS if schema_version == 2 else MODEL_FINAL_CHECKS
    if schema_version == 3 and set(checks) != set(MODEL_FINAL_CHECKS):
        issues.append(
            "content_quality_review.json schema-v3 final_checks must contain only "
            "model-judged checks"
        )
    for name in required_checks:
        check = checks.get(name)
        label = f"content_quality_review.json final_checks.{name}"
        if not isinstance(check, dict):
            issues.append(f"{label} must be an object")
            continue
        if check.get("status") != "passed":
            issues.append(f"{label}.status must be passed")
        if not _nonempty_string(check.get("finding")):
            issues.append(f"{label}.finding is required")

    signals = review.get("document_signals")
    if not isinstance(signals, dict):
        issues.append("content_quality_review.json document_signals must be an object")
    else:
        expected_signals = blueprint_summary.get("document_signals", {})
        for name, expected in expected_signals.items():
            if signals.get(name) != expected:
                issues.append(
                    "content_quality_review.json document_signals."
                    f"{name} does not match"
                )
        if signals.get("inventory_item_count") != len(inventory_ids):
            issues.append(
                "content_quality_review.json document_signals.inventory_item_count does not match"
            )


def _read_object(path: Path, issues: list[str]) -> dict[str, Any]:
    if not path.is_file():
        issues.append(f"missing {path.name}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"invalid {path.name}: {exc}")
        return {}
    if not isinstance(value, dict):
        issues.append(f"{path.name} must contain a JSON object")
        return {}
    return value


def _read_optional_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _string_set(value: Any, label: str, issues: list[str]) -> set[str]:
    if not isinstance(value, list):
        issues.append(f"{label}.inventory_item_ids must be a list")
        return set()
    return {clean for item in value if (clean := _nonempty_string(item))}


def _string_list(value: Any, label: str, issues: list[str]) -> list[str]:
    if not isinstance(value, list):
        issues.append(f"{label} must be a list")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        clean = _nonempty_string(item)
        if not clean:
            issues.append(f"{label}[{index}] must be a non-empty string")
        else:
            result.append(clean)
    return result


def _integer_set(value: Any, label: str, issues: list[str]) -> set[int]:
    if not isinstance(value, list) or any(not isinstance(item, int) for item in value):
        issues.append(f"{label} must be a list of integers")
        return set()
    return set(value)


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Print deterministic reader-facing Markdown quality signals."
    )
    parser.add_argument("--signals", type=Path, required=True, help="minutes.md path")
    parser.add_argument(
        "--blueprint",
        type=Path,
        help="optional document_blueprint.json for reader-body evidence signals",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    minutes_text = args.signals.read_text(encoding="utf-8")
    signals = markdown_document_signals(minutes_text)
    if args.blueprint:
        blueprint = json.loads(args.blueprint.read_text(encoding="utf-8"))
        if not isinstance(blueprint, dict):
            raise SystemExit("error: blueprint must be a JSON object")
        signals["reader_body_raw_evidence_refs"] = reader_body_raw_evidence_refs(
            minutes_text,
            blueprint,
        )
    print(json.dumps(signals, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
