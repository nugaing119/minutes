from __future__ import annotations

import argparse
import hashlib
import json
import math
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
    "meeting_minutes",
    "technical_session_analysis",
    "product_demo_analysis",
    "technical_decision_record",
    "strategy_session_analysis",
    "general_recording_analysis",
}
BLUEPRINT_WRITING_STYLES = {
    "meeting_minutes_objective",
    "content_adaptive",
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
TRUST_SECTION_HEADINGS = {
    "open_questions": {
        "ko": "추가 검증이 필요한 항목",
        "en": "Items Requiring Further Verification",
    },
    "external_evidence": {
        "ko": "외부 근거 확인",
        "en": "External Evidence Check",
    },
}
TRUST_APPENDIX_DISCLOSURES = {
    "ko": (
        "영상 발언을 문서의 기준으로 유지하며, 외부 자료는 모호성 보강과 충돌 확인에만 사용함.",
        "녹화 원문과 참석자·고객·내부 식별정보를 외부 검색에 전송하지 않음.",
    ),
    "en": (
        "The recording remains the source of truth; any external evidence is limited to clarifying ambiguity or documenting conflicts.",
        "No recording content, participant, customer, or internal identifiers were sent to external search.",
    ),
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
    "recording_datetime",
    "duration",
}
FORBIDDEN_FRONT_MATTER_KEYS = {
    "source_language",
    "output_language",
    "evidence_basis",
    "external_evidence_policy",
    "process_metrics",
    "skill",
    "model",
    "worker",
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
INTERNAL_WORKFLOW_REFERENCE_RE = re.compile(
    r"\b(?:process_metrics|content_freeze|content_quality_review|fresh_codex_handoff|"
    r"worker_runtime_summary|evidence_coverage|docx_qa)\.json\b|"
    r"\b(?:run_fresh_codex_job|process_file|finalize_docx)\.py\b|"
    r"\b(?:OCR_WORKERS|OCR_FFMPEG_THREADS|OCR_PRESTART_COOLDOWN_SECONDS|"
    r"worker_contract_passed)\b",
    re.I,
)
KOREAN_REPORT_ENDING_RE = re.compile(
    r"(?:함|됨|임|음|예정|필요|완료|보류|미정|진행 중|검토 중|확인 중|대기 중)$"
)
KOREAN_NARRATIVE_ENDING_RE = re.compile(
    r"(?:습니다|입니다|했습니다|하였다|했다|한다|된다|이다|있다|없다|필요하다|예정이다)$"
)
MARKDOWN_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\((?P<path>[^)]+)\)\s*$")
MARKDOWN_TABLE_SEPARATOR_RE = re.compile(
    r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$"
)
QUALITY_CONTRACT_VERSION = 3
REQUIRED_ITEM_DIMENSIONS = (
    "core_facts",
    "conditions_exceptions",
    "risks_limitations",
    "impact",
    "actions_decisions",
)
DENSITY_WARNING_MIN_DURATION_SECONDS = 20 * 60
DENSITY_WARNING_MIN_INFORMATION_CHARS_PER_MINUTE = 110.0
DENSITY_BASELINE_NAME = "content_density_baseline.json"
DENSITY_TARGET_MAX_SECTIONS = 3
DENSITY_TARGET_DEFICIT_CHARS_PER_SECTION = 600
DENSITY_TARGET_MIN_GAIN_PER_SECTION = 120
DENSITY_REVISION_DEFICIT_RECOVERY_RATIO = 0.80
DENSITY_REVISION_MIN_TOTAL_GAIN = 400
DENSITY_REVISION_MAX_REQUIRED_GAIN = 1_800
VISUAL_PLAN_STATUSES = {"embedded", "limited", "not_applicable"}


def korean_meeting_report_style_issues(markdown: str) -> list[str]:
    """Return concise style failures for Korean meeting-minutes prose."""
    candidates: list[str] = []
    in_code_fence = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if (
            in_code_fence
            or not line
            or line.startswith("#")
            or line.startswith("|")
            or MARKDOWN_IMAGE_RE.match(line)
            or re.match(r"^(?:[-*]\s+)?[^:]{1,30}:\s+", line)
        ):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        for sentence in re.split(r"[.!?。]+", line):
            sentence = sentence.strip().rstrip(";:")
            if len(re.findall(r"[가-힣]", sentence)) >= 5:
                candidates.append(sentence)
    if not candidates:
        return []
    narrative = [
        sentence
        for sentence in candidates
        if KOREAN_NARRATIVE_ENDING_RE.search(sentence)
    ]
    report_count = sum(
        bool(KOREAN_REPORT_ENDING_RE.search(sentence)) for sentence in candidates
    )
    required_report_count = max(1, math.ceil(len(candidates) * 0.6))
    issues: list[str] = []
    if narrative:
        issues.append(
            "Korean meeting minutes contain narrative/polite sentence endings instead of "
            "objective report style"
        )
    if report_count < required_report_count:
        issues.append(
            "Korean meeting minutes must consistently end substantive prose with concise "
            "report forms such as ~함, ~하기로 함, ~예정임, or ~필요함"
        )
    return issues


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
    quality_contract_version = manifest.get("quality_contract_version", 1)
    if not isinstance(quality_contract_version, int) or quality_contract_version < 1:
        issues.append("evidence_chunks.json quality_contract_version must be a positive integer")
        quality_contract_version = 1
    elif quality_contract_version > QUALITY_CONTRACT_VERSION:
        issues.append(
            "evidence_chunks.json quality_contract_version is newer than this validator"
        )
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
        job_dir=job_dir,
        quality_contract_version=quality_contract_version,
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
        required_ids=required_ids,
        minutes_text=minutes_text,
        blueprint_summary=blueprint_summary,
        quality_contract_version=quality_contract_version,
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
        "quality_contract_version": quality_contract_version,
        "density_warning": blueprint_summary.get("document_signals", {}).get(
            "density_warning",
            False,
        ),
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
    job_dir: Path,
    quality_contract_version: int,
    inventory: dict[str, Any],
    inventory_ids: set[str],
    required_ids: set[str],
    minutes_text: str,
    official_sources: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    signals = deterministic_document_signals(job_dir, minutes_text, blueprint)
    summary: dict[str, Any] = {
        "section_count": 0,
        "reader_body_raw_evidence_refs": signals["raw_evidence_refs"],
        "document_signals": signals,
        "primary_section_by_item": {},
        "section_text_by_id": {},
        "section_ids": set(),
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
    writing_style = _nonempty_string(blueprint.get("writing_style"))
    if quality_contract_version >= 3 and writing_style not in BLUEPRINT_WRITING_STYLES:
        issues.append(
            "document_blueprint.json writing_style must be one of "
            f"{sorted(BLUEPRINT_WRITING_STYLES)}"
        )
    elif archetype == "meeting_minutes" and writing_style != (
        "meeting_minutes_objective"
    ):
        issues.append(
            "meeting_minutes requires writing_style=meeting_minutes_objective"
        )
    elif archetype != "meeting_minutes" and writing_style != "content_adaptive":
        issues.append(
            "non-meeting documents require writing_style=content_adaptive"
        )

    preamble, rendered_sections = _split_h2_sections(minutes_text)
    h1_matches = re.findall(r"(?m)^#[ \t]+(.+?)\s*$", minutes_text)
    if len(h1_matches) != 1:
        issues.append("minutes.md must contain exactly one H1 display title")
    if document_type and not re.search(
        rf"(?mi)^[ \t]*(?:-[ \t]+)?(?:문서 유형|Document type)\s*:\s*"
        rf"{re.escape(document_type)}\s*$",
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
            if (
                quality_contract_version >= 3
                and key.lower() in FORBIDDEN_FRONT_MATTER_KEYS
            ):
                issues.append(
                    f"{label} exposes internal production metadata: {key}"
                )
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
    action_section_applicability: str | None = None
    open_section_applicability: str | None = None
    external_section_applicability: str | None = None
    primary_section_by_item: dict[str, str] = {}
    section_text_by_id: dict[str, str] = {}

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
        for item_id in item_ids:
            if item_id in inventory_ids and item_id not in primary_section_by_item:
                primary_section_by_item[item_id] = section_id

        section_text = rendered_sections.get(heading, "")
        if not section_text:
            issues.append(f"{label}.heading was not found as an H2 in minutes.md")
            continue
        section_text_by_id[section_id] = section_text
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
        if role == "external_evidence":
            external_section_applicability = applicability
        if role in TRUST_SECTION_HEADINGS and heading not in set(
            TRUST_SECTION_HEADINGS[role].values()
        ):
            issues.append(
                f"{label}.heading must use the canonical {role} heading: "
                f"{sorted(TRUST_SECTION_HEADINGS[role].values())}"
            )
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
    if official_sources:
        if role_counts["external_evidence"] != 1:
            issues.append(
                "official-source verification requires exactly one external_evidence section"
            )
        if official_sources.get("status") == "not_applicable" and (
            external_section_applicability != "not_applicable"
        ):
            issues.append(
                "not_applicable official verification requires a not_applicable "
                "external_evidence section"
            )
        if official_sources.get("status") == "completed" and isinstance(claims, list) and (
            claims and external_section_applicability != "required"
        ):
            issues.append(
                "completed official claims require a required external_evidence section"
            )
    if role_counts["external_evidence"] == 1:
        final_role = sections[-1].get("role") if isinstance(sections[-1], dict) else None
        penultimate_role = (
            sections[-2].get("role")
            if len(sections) >= 2 and isinstance(sections[-2], dict)
            else None
        )
        if final_role != "external_evidence":
            issues.append("the external_evidence section must be the final H2")
        if penultimate_role != "open_questions":
            issues.append(
                "open_questions and external_evidence must be the final two H2 sections"
            )
    unresolved_official_claim = isinstance(claims, list) and any(
        isinstance(claim, dict)
        and claim.get("status") in {"not_found", "partially_verified"}
        for claim in claims
    )
    if unresolved_official_claim and open_section_applicability != "required":
        issues.append(
            "open_questions cannot be not_applicable when official verification remains unresolved"
        )

    raw_ref_count = len(RAW_EVIDENCE_REF_RE.findall(minutes_text))
    legacy_raw_ref_allowance = max(2, conflict_count * 2)
    if quality_contract_version >= 3 and raw_ref_count:
        issues.append(
            "reader document must not expose raw STT/OCR/Snapshot references; keep "
            "traceability in internal sidecars"
        )
    elif quality_contract_version < 3 and raw_ref_count > legacy_raw_ref_allowance:
        issues.append(
            "reader-facing body contains too many raw STT/OCR/Snapshot references: "
            f"{raw_ref_count} > {legacy_raw_ref_allowance}"
        )
    internal_workflow_refs = sorted(
        set(INTERNAL_WORKFLOW_REFERENCE_RE.findall(minutes_text))
    )
    if quality_contract_version >= 3 and internal_workflow_refs:
        issues.append(
            "reader document exposes internal workflow artifacts or settings: "
            f"{internal_workflow_refs}"
        )
    if (
        quality_contract_version >= 3
        and writing_style == "meeting_minutes_objective"
        and re.search(r"[가-힣]", minutes_text)
    ):
        issues.extend(korean_meeting_report_style_issues(minutes_text))
    summary["reader_body_raw_evidence_refs"] = raw_ref_count
    summary["document_signals"] = deterministic_document_signals(
        job_dir,
        minutes_text,
        blueprint,
    )
    summary["primary_section_by_item"] = primary_section_by_item
    summary["section_text_by_id"] = section_text_by_id
    summary["section_ids"] = seen_section_ids
    if quality_contract_version >= 2:
        _validate_visual_evidence_plan(
            job_dir,
            blueprint,
            minutes_text=minutes_text,
            section_ids=seen_section_ids,
            section_text_by_id=section_text_by_id,
            issues=issues,
        )
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
                signals["image_count"] > 0,
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
        "image_count": sum(
            bool(MARKDOWN_IMAGE_RE.fullmatch(line.strip())) for line in lines
        ),
    }


def _markdown_image_records(minutes_text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current_h2: str | None = None
    for line_index, line in enumerate(minutes_text.splitlines()):
        heading = re.match(r"^##[ \t]+(.+?)\s*$", line)
        if heading:
            current_h2 = heading.group(1).strip()
            continue
        image = MARKDOWN_IMAGE_RE.fullmatch(line.strip())
        if image:
            records.append(
                {
                    "path": image.group("path").strip(),
                    "heading": current_h2,
                    "line_index": line_index,
                }
            )
    return records


def _is_substantive_markdown_line(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped
        and not re.match(r"^#{1,6}[ \t]+", stripped)
        and not MARKDOWN_IMAGE_RE.fullmatch(stripped)
    )


def _adjacent_image_pair_count(minutes_text: str) -> int:
    lines = minutes_text.splitlines()
    records = _markdown_image_records(minutes_text)
    return sum(
        not any(
            _is_substantive_markdown_line(line)
            for line in lines[left["line_index"] + 1 : right["line_index"]]
        )
        for left, right in zip(records, records[1:])
    )


def _trailing_image_risk(minutes_text: str) -> bool:
    lines = minutes_text.splitlines()
    records = _markdown_image_records(minutes_text)
    if not records:
        return False
    return not any(
        _is_substantive_markdown_line(line)
        for line in lines[records[-1]["line_index"] + 1 :]
    )


def _validate_visual_evidence_plan(
    job_dir: Path,
    blueprint: dict[str, Any],
    *,
    minutes_text: str,
    section_ids: set[str],
    section_text_by_id: dict[str, str],
    issues: list[str],
) -> None:
    plan = blueprint.get("visual_evidence_plan")
    if not isinstance(plan, dict):
        issues.append("document_blueprint.json visual_evidence_plan must be an object")
        return
    status = _nonempty_string(plan.get("status"))
    if status not in VISUAL_PLAN_STATUSES:
        issues.append(
            "document_blueprint.json visual_evidence_plan.status must be embedded, "
            "limited, or not_applicable"
        )
    if not _nonempty_string(plan.get("rationale")):
        issues.append(
            "document_blueprint.json visual_evidence_plan.rationale is required"
        )
    items = plan.get("items")
    if not isinstance(items, list):
        issues.append("document_blueprint.json visual_evidence_plan.items must be a list")
        items = []

    markdown_records = _markdown_image_records(minutes_text)
    markdown_paths = [str(record["path"]) for record in markdown_records]
    available_snapshots = [
        path
        for path in (job_dir / "snapshots").glob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    if status == "embedded" and not 3 <= len(items) <= 5:
        issues.append(
            "document_blueprint.json embedded visual plan must contain 3-5 core images"
        )
    if status == "limited":
        if not 1 <= len(items) <= 2:
            issues.append(
                "document_blueprint.json limited visual plan must contain 1-2 images"
            )
        if len(available_snapshots) >= 3:
            issues.append(
                "document_blueprint.json limited visual plan is allowed only when fewer "
                "than three selected snapshots exist"
            )
    if status == "not_applicable" and (items or markdown_paths):
        issues.append(
            "document_blueprint.json not_applicable visual plan must not embed images"
        )

    section_heading_by_id = {
        str(section.get("id", "")).strip(): str(section.get("heading", "")).strip()
        for section in blueprint.get("sections", [])
        if isinstance(section, dict)
    }
    planned_paths: list[str] = []
    planned_sections: Counter[str] = Counter()
    for index, item in enumerate(items):
        label = f"document_blueprint.json visual_evidence_plan.items[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label} must be an object")
            continue
        snapshot_path = _nonempty_string(item.get("snapshot_path"))
        section_id = _nonempty_string(item.get("section_id"))
        if not snapshot_path or not section_id:
            issues.append(f"{label} snapshot_path and section_id are required")
            continue
        planned_paths.append(snapshot_path)
        planned_sections[section_id] += 1
        if section_id not in section_ids:
            issues.append(f"{label}.section_id must resolve to a blueprint section")
        for field in ("purpose", "reader_value"):
            if not _nonempty_string(item.get(field)):
                issues.append(f"{label}.{field} is required")
        raw_path = Path(snapshot_path)
        resolved = (job_dir / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
        try:
            resolved.relative_to((job_dir / "snapshots").resolve())
        except ValueError:
            issues.append(f"{label}.snapshot_path must stay inside the job snapshots directory")
        else:
            if not resolved.is_file():
                issues.append(f"{label}.snapshot_path does not exist")
        matching_records = [
            record for record in markdown_records if record["path"] == snapshot_path
        ]
        expected_heading = section_heading_by_id.get(section_id)
        if len(matching_records) != 1:
            issues.append(f"{label}.snapshot_path must appear exactly once in minutes.md")
        elif matching_records[0].get("heading") != expected_heading:
            issues.append(f"{label}.snapshot_path is embedded in the wrong H2 section")
        if section_id in section_text_by_id and snapshot_path not in section_text_by_id[section_id]:
            issues.append(f"{label}.snapshot_path was not found in its assigned section")

    if len(planned_paths) != len(set(planned_paths)):
        issues.append("document_blueprint.json visual plan contains duplicate images")
    if planned_paths != markdown_paths:
        issues.append(
            "document_blueprint.json visual plan image order must exactly match minutes.md"
        )
    crowded_sections = sorted(
        section_id for section_id, count in planned_sections.items() if count > 2
    )
    if crowded_sections:
        issues.append(
            "document_blueprint.json visual plan exceeds two full-width images per H2: "
            f"{crowded_sections}"
        )
    if _adjacent_image_pair_count(minutes_text):
        issues.append(
            "minutes.md contains adjacent full-width Markdown images without substantive "
            "reader content between them"
        )
    if _trailing_image_risk(minutes_text):
        issues.append(
            "minutes.md ends with a full-width image; place substantive content after the "
            "last image to reduce short-final-page risk"
        )


def _duration_from_text(value: str) -> float | None:
    clock = re.search(r"\b(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?\b", value)
    if clock:
        hours, minutes, seconds = (int(part) for part in clock.groups())
        return float(hours * 3_600 + minutes * 60 + seconds)
    total = 0.0
    matched = False
    for pattern, multiplier in (
        (r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|시간)", 3_600),
        (r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|분)", 60),
        (r"(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|초)", 1),
    ):
        match = re.search(pattern, value, re.I)
        if match:
            total += float(match.group(1)) * multiplier
            matched = True
    return total if matched and total > 0 else None


def _recording_duration_seconds(job_dir: Path, blueprint: dict[str, Any]) -> float | None:
    for path, field_path in (
        (job_dir / "speech_activity.json", ("audio_duration_seconds",)),
        (
            job_dir / "worker_runtime_summary.json",
            ("speech_activity", "fields", "audio_duration_seconds"),
        ),
    ):
        try:
            value: Any = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for key in field_path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return round(float(value), 3)
    for item in blueprint.get("front_matter", []):
        if not isinstance(item, dict) or item.get("key") != "duration":
            continue
        parsed = _duration_from_text(str(item.get("value", "")))
        if parsed:
            return round(parsed, 3)
    return None


def _reader_body_text(minutes_text: str, blueprint: dict[str, Any]) -> str:
    _, sections = _split_h2_sections(minutes_text)
    if not sections:
        return minutes_text
    excluded = {
        str(section.get("heading", "")).strip()
        for section in blueprint.get("sections", [])
        if isinstance(section, dict)
        and section.get("role") in {"evidence_appendix", "external_evidence"}
    }
    return "\n\n".join(
        text for heading, text in sections.items() if heading not in excluded
    )


def _information_char_count(value: str) -> int:
    without_images = "\n".join(
        line
        for line in value.splitlines()
        if not MARKDOWN_IMAGE_RE.fullmatch(line.strip())
    )
    without_urls = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", without_images)
    return sum(character.isalnum() for character in without_urls)


def _required_inventory_ids(job_dir: Path, blueprint: dict[str, Any]) -> set[str]:
    try:
        inventory = read_json(job_dir / "content_inventory.json")
    except (OSError, json.JSONDecodeError):
        inventory = {}
    items = inventory.get("items") if isinstance(inventory, dict) else None
    required = {
        str(item.get("id", "")).strip()
        for item in items or []
        if isinstance(item, dict)
        and item.get("importance") == "required"
        and str(item.get("id", "")).strip()
    }
    if required:
        return required
    return {
        str(item_id).strip()
        for section in blueprint.get("sections", [])
        if isinstance(section, dict)
        for item_id in section.get("primary_inventory_item_ids", [])
        if str(item_id).strip()
    }


def _density_section_metrics(
    job_dir: Path,
    minutes_text: str,
    blueprint: dict[str, Any],
) -> list[dict[str, Any]]:
    _, rendered_sections = _split_h2_sections(minutes_text)
    required_ids = _required_inventory_ids(job_dir, blueprint)
    metrics: list[dict[str, Any]] = []
    for index, section in enumerate(blueprint.get("sections", [])):
        if not isinstance(section, dict):
            continue
        if section.get("role") in {"evidence_appendix", "external_evidence"}:
            continue
        section_id = str(section.get("id", "")).strip()
        heading = str(section.get("heading", "")).strip()
        item_ids = [
            str(item_id).strip()
            for item_id in section.get("primary_inventory_item_ids", [])
            if str(item_id).strip() in required_ids
        ]
        if not section_id or not heading or not item_ids:
            continue
        information_chars = _information_char_count(
            rendered_sections.get(heading, "")
        )
        metrics.append(
            {
                "section_id": section_id,
                "blueprint_index": index,
                "required_item_count": len(item_ids),
                "information_chars": information_chars,
                "information_chars_per_required_item": round(
                    information_chars / len(item_ids),
                    2,
                ),
            }
        )
    return metrics


def deterministic_document_signals(
    job_dir: Path,
    minutes_text: str,
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    signals: dict[str, Any] = markdown_document_signals(minutes_text)
    body = _reader_body_text(minutes_text, blueprint)
    information_chars = _information_char_count(body)
    duration_seconds = _recording_duration_seconds(job_dir, blueprint)
    density = (
        round(information_chars / (duration_seconds / 60), 2)
        if duration_seconds
        else None
    )
    density_warning = bool(
        duration_seconds
        and duration_seconds >= DENSITY_WARNING_MIN_DURATION_SECONDS
        and density is not None
        and density < DENSITY_WARNING_MIN_INFORMATION_CHARS_PER_MINUTE
    )
    density_deficit = (
        max(
            0,
            math.ceil(
                DENSITY_WARNING_MIN_INFORMATION_CHARS_PER_MINUTE
                * (duration_seconds / 60)
                - information_chars
            ),
        )
        if density_warning and duration_seconds
        else 0
    )
    section_metrics = _density_section_metrics(job_dir, minutes_text, blueprint)
    ranked_sections = sorted(
        section_metrics,
        key=lambda item: (
            item["information_chars_per_required_item"],
            item["blueprint_index"],
        ),
    )
    target_count = (
        min(
            len(ranked_sections),
            DENSITY_TARGET_MAX_SECTIONS,
            max(
                1,
                math.ceil(
                    density_deficit / DENSITY_TARGET_DEFICIT_CHARS_PER_SECTION
                ),
            ),
        )
        if density_warning and ranked_sections
        else 0
    )
    target_sections = ranked_sections[:target_count]
    signals.update(
        {
            "reader_body_raw_evidence_refs": reader_body_raw_evidence_refs(
                minutes_text,
                blueprint,
            ),
            "reader_body_information_chars": information_chars,
            "duration_seconds": duration_seconds,
            "reader_body_information_chars_per_minute": density,
            "density_warning_threshold_chars_per_minute": (
                DENSITY_WARNING_MIN_INFORMATION_CHARS_PER_MINUTE
            ),
            "density_warning": density_warning,
            "density_deficit_information_chars": density_deficit,
            "density_target_section_ids": [
                item["section_id"] for item in target_sections
            ],
            "density_section_metrics": section_metrics,
            "embedded_image_count": len(_markdown_image_records(minutes_text)),
            "adjacent_image_pair_count": _adjacent_image_pair_count(minutes_text),
            "trailing_image_risk": _trailing_image_risk(minutes_text),
        }
    )
    return signals


def markdown_document_signals(minutes_text: str) -> dict[str, Any]:
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


def _normalize_compact_review_contract(review: dict[str, Any]) -> None:
    """Canonicalize harmless model aliases before strict semantic validation."""
    checks = review.get("required_item_checks")
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict):
                continue
            if "item_id" not in item and "inventory_item_id" in item:
                item["item_id"] = item.pop("inventory_item_id")
            if "section_id" not in item and "primary_section_id" in item:
                item["section_id"] = item.pop("primary_section_id")
            if "dimensions" not in item and isinstance(item.get("checks"), dict):
                item["dimensions"] = item.pop("checks")

    cycles = review.get("review_cycles")
    if not isinstance(cycles, list) or len(cycles) != 2:
        return
    revised, passed = cycles
    if not (
        isinstance(revised, dict)
        and isinstance(passed, dict)
        and revised.get("status") == "revised"
        and passed.get("status") == "passed"
    ):
        return
    for field in ("changes", "target_section_ids"):
        revised_value = revised.get(field)
        passed_value = passed.get(field)
        if (
            (not isinstance(revised_value, list) or not revised_value)
            and isinstance(passed_value, list)
            and passed_value
        ):
            revised[field] = passed_value
            if field == "changes":
                passed[field] = []
            else:
                passed.pop(field, None)


def _completed_density_revision_cycles(
    baseline: dict[str, Any],
    document_signals: dict[str, Any],
) -> list[dict[str, Any]] | None:
    target_ids = baseline.get("target_section_ids")
    baseline_chars = baseline.get("target_section_information_chars")
    minimum_section_gain = baseline.get("minimum_section_information_gain")
    minimum_total_gain = baseline.get("minimum_total_information_gain")
    baseline_total = baseline.get("reader_body_information_chars")
    current_total = document_signals.get("reader_body_information_chars")
    if (
        not isinstance(target_ids, list)
        or not target_ids
        or not all(isinstance(section_id, str) and section_id for section_id in target_ids)
        or not isinstance(baseline_chars, dict)
        or not isinstance(minimum_section_gain, int)
        or minimum_section_gain < 0
        or not isinstance(minimum_total_gain, int)
        or minimum_total_gain < 0
        or not isinstance(baseline_total, int)
        or not isinstance(current_total, int)
        or current_total - baseline_total < minimum_total_gain
    ):
        return None
    current_chars = {
        item.get("section_id"): item.get("information_chars")
        for item in document_signals.get("density_section_metrics", [])
        if isinstance(item, dict)
    }
    changes: list[str] = []
    for section_id in target_ids:
        before = baseline_chars.get(section_id)
        after = current_chars.get(section_id)
        if (
            not isinstance(before, int)
            or not isinstance(after, int)
            or after - before < minimum_section_gain
        ):
            return None
        changes.append(
            f"{section_id}: information_chars {before}->{after} (+{after - before})"
        )
    changes.append(
        "reader_body_information_chars "
        f"{baseline_total}->{current_total} (+{current_total - baseline_total})"
    )
    return [
        {
            "cycle": 1,
            "status": "revised",
            "findings": [
                "LOW_INFORMATION_DENSITY: validator-selected substantive sections "
                "required evidence-backed expansion."
            ],
            "changes": changes,
            "target_section_ids": list(target_ids),
        },
        {
            "cycle": 2,
            "status": "passed",
            "findings": [],
            "changes": [],
        },
    ]


def finalize_compact_review(job_dir: Path) -> dict[str, Any]:
    """Bind a schema-v3 model review to deterministic artifact facts."""
    review_path = job_dir / "content_quality_review.json"
    review = read_json(review_path)
    if review.get("schema_version") != 3:
        return review
    _normalize_compact_review_contract(review)

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
    document_signals = deterministic_document_signals(
        job_dir,
        minutes_text,
        blueprint,
    )
    review["document_signals"] = {
        **document_signals,
        "inventory_item_count": inventory_count,
    }
    cycles = review.get("review_cycles")
    density_revision_named = any(
        isinstance(finding, str) and "LOW_INFORMATION_DENSITY" in finding
        for cycle in cycles or []
        if isinstance(cycle, dict)
        for finding in cycle.get("findings", [])
    )
    baseline_path = job_dir / DENSITY_BASELINE_NAME
    if (
        document_signals.get("density_warning")
        and not baseline_path.exists()
        and not density_revision_named
    ):
        metrics = {
            item["section_id"]: item["information_chars"]
            for item in document_signals.get("density_section_metrics", [])
            if isinstance(item, dict)
        }
        target_ids = document_signals.get("density_target_section_ids", [])
        density_deficit = int(
            document_signals.get("density_deficit_information_chars", 0)
        )
        minimum_total_gain = min(
            DENSITY_REVISION_MAX_REQUIRED_GAIN,
            max(
                DENSITY_REVISION_MIN_TOTAL_GAIN,
                math.ceil(
                    density_deficit
                    * DENSITY_REVISION_DEFICIT_RECOVERY_RATIO
                ),
            ),
        )
        write_json(
            baseline_path,
            {
                "schema_version": 1,
                "length_policy": "minimum_completeness_only_no_maximum",
                "source_minutes_sha256": _sha256_file(minutes_path),
                "reader_body_information_chars": document_signals.get(
                    "reader_body_information_chars"
                ),
                "reader_body_information_chars_per_minute": document_signals.get(
                    "reader_body_information_chars_per_minute"
                ),
                "density_deficit_information_chars": density_deficit,
                "minimum_total_information_gain": minimum_total_gain,
                "minimum_section_information_gain": (
                    DENSITY_TARGET_MIN_GAIN_PER_SECTION
                ),
                "target_section_ids": target_ids,
                "target_section_information_chars": {
                    section_id: metrics.get(section_id, 0)
                    for section_id in target_ids
                },
            },
        )
    if baseline_path.is_file():
        try:
            baseline = read_json(baseline_path)
        except (OSError, json.JSONDecodeError):
            baseline = {}
        completed_cycles = _completed_density_revision_cycles(
            baseline,
            document_signals,
        )
        if completed_cycles is not None:
            review["review_cycles"] = completed_cycles
    write_json(review_path, review)
    return review


def _validate_review(
    review: dict[str, Any],
    *,
    job_dir: Path,
    expected_chunk_indexes: set[int],
    inventory_ids: set[str],
    required_ids: set[str],
    minutes_text: str,
    blueprint_summary: dict[str, Any],
    quality_contract_version: int,
    issues: list[str],
) -> None:
    if not review:
        return
    schema_version = review.get("schema_version")
    if schema_version not in {2, 3}:
        issues.append("content_quality_review.json schema_version must be 2 or 3")
    if quality_contract_version >= 2 and schema_version != 3:
        issues.append(
            "quality contract v2+ requires content_quality_review.json schema_version 3"
        )
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
    max_cycles = 2 if quality_contract_version >= 2 else 3
    if not isinstance(review_cycles, list) or not review_cycles:
        issues.append("content_quality_review.json review_cycles must be non-empty")
    elif len(review_cycles) > max_cycles:
        issues.append(
            "content_quality_review.json review_cycles must not exceed "
            f"{max_cycles} for this quality contract"
        )
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
                if quality_contract_version >= 2:
                    targets = _string_list(
                        cycle.get("target_section_ids"),
                        f"{label}.target_section_ids",
                        issues,
                    )
                    if not targets:
                        issues.append(
                            f"{label} revised cycle requires target_section_ids"
                        )
                    unknown_targets = set(targets) - set(
                        blueprint_summary.get("section_ids", set())
                    )
                    if unknown_targets:
                        issues.append(
                            f"{label}.target_section_ids contains unknown sections: "
                            f"{sorted(unknown_targets)}"
                        )
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
        if (
            quality_contract_version < 2
            and schema_version == 3
            and len(review_cycles) == 3
        ):
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

        expected_signals = blueprint_summary.get("document_signals", {})
        baseline_path = job_dir / DENSITY_BASELINE_NAME
        density_contract_required = bool(
            quality_contract_version >= 2
            and (expected_signals.get("density_warning") or baseline_path.is_file())
        )
        if density_contract_required:
            first_cycle = review_cycles[0] if review_cycles else None
            findings = first_cycle.get("findings", []) if isinstance(first_cycle, dict) else []
            density_named = any(
                isinstance(finding, str) and "LOW_INFORMATION_DENSITY" in finding
                for finding in findings
            )
            if (
                len(review_cycles) != 2
                or not isinstance(first_cycle, dict)
                or first_cycle.get("status") != "revised"
                or not density_named
            ):
                issues.append(
                    "LOW_INFORMATION_DENSITY requires exactly one targeted revision "
                    "cycle naming the warning and failed section IDs"
                )
            try:
                baseline = read_json(baseline_path)
            except (OSError, json.JSONDecodeError):
                baseline = {}
                issues.append(
                    f"LOW_INFORMATION_DENSITY requires validator-owned {DENSITY_BASELINE_NAME}"
                )
            expected_targets = _string_list(
                baseline.get("target_section_ids"),
                f"{DENSITY_BASELINE_NAME}.target_section_ids",
                issues,
            )
            actual_targets = _string_list(
                first_cycle.get("target_section_ids")
                if isinstance(first_cycle, dict)
                else None,
                "content_quality_review.json review_cycles[0].target_section_ids",
                issues,
            )
            if set(actual_targets) != set(expected_targets):
                issues.append(
                    "LOW_INFORMATION_DENSITY target_section_ids must exactly match "
                    f"validator-selected substantive sections: {expected_targets}"
                )
            baseline_chars = baseline.get("target_section_information_chars")
            if not isinstance(baseline_chars, dict):
                baseline_chars = {}
                issues.append(
                    f"{DENSITY_BASELINE_NAME}.target_section_information_chars must be an object"
                )
            current_chars = {
                item.get("section_id"): item.get("information_chars")
                for item in expected_signals.get("density_section_metrics", [])
                if isinstance(item, dict)
            }
            minimum_section_gain = baseline.get(
                "minimum_section_information_gain"
            )
            if not isinstance(minimum_section_gain, int) or minimum_section_gain < 0:
                minimum_section_gain = DENSITY_TARGET_MIN_GAIN_PER_SECTION
                issues.append(
                    f"{DENSITY_BASELINE_NAME}.minimum_section_information_gain "
                    "must be a nonnegative integer"
                )
            insufficient_gains = []
            for section_id in expected_targets:
                before = baseline_chars.get(section_id)
                after = current_chars.get(section_id)
                if (
                    not isinstance(before, int)
                    or not isinstance(after, int)
                    or after - before < minimum_section_gain
                ):
                    insufficient_gains.append(
                        {
                            "section_id": section_id,
                            "before": before,
                            "after": after,
                            "minimum_gain": minimum_section_gain,
                        }
                    )
            if insufficient_gains:
                issues.append(
                    "LOW_INFORMATION_DENSITY targeted sections did not gain enough "
                    f"information: {insufficient_gains}"
                )
            baseline_total = baseline.get("reader_body_information_chars")
            current_total = expected_signals.get("reader_body_information_chars")
            minimum_total_gain = baseline.get("minimum_total_information_gain")
            if not isinstance(minimum_total_gain, int) or minimum_total_gain < 0:
                issues.append(
                    f"{DENSITY_BASELINE_NAME}.minimum_total_information_gain "
                    "must be a nonnegative integer"
                )
            elif (
                not isinstance(baseline_total, int)
                or not isinstance(current_total, int)
                or current_total - baseline_total < minimum_total_gain
            ):
                issues.append(
                    "LOW_INFORMATION_DENSITY revision did not meet the required minimum total "
                    "information gain: "
                    f"before={baseline_total}, after={current_total}, "
                    f"minimum_gain={minimum_total_gain}"
                )

    checks = review.get("final_checks")
    if not isinstance(checks, dict):
        issues.append("content_quality_review.json final_checks must be an object")
        checks = {}
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

    if quality_contract_version >= 2:
        _validate_required_item_checks(
            review.get("required_item_checks"),
            required_ids=required_ids,
            blueprint_summary=blueprint_summary,
            issues=issues,
        )

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


def _validate_required_item_checks(
    value: Any,
    *,
    required_ids: set[str],
    blueprint_summary: dict[str, Any],
    issues: list[str],
) -> None:
    if not isinstance(value, list):
        issues.append("content_quality_review.json required_item_checks must be a list")
        return
    primary_section_by_item = blueprint_summary.get("primary_section_by_item", {})
    section_text_by_id = blueprint_summary.get("section_text_by_id", {})
    seen_ids: list[str] = []
    for index, item in enumerate(value):
        label = f"content_quality_review.json required_item_checks[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label} must be an object")
            continue
        item_id = _nonempty_string(item.get("item_id"))
        section_id = _nonempty_string(item.get("section_id"))
        if not item_id or not section_id:
            issues.append(f"{label} item_id and section_id are required")
            continue
        seen_ids.append(item_id)
        expected_section = primary_section_by_item.get(item_id)
        if expected_section != section_id:
            issues.append(
                f"{label}.section_id must match the item's primary blueprint section"
            )
        section_text = str(section_text_by_id.get(section_id, ""))
        dimensions = item.get("dimensions")
        if not isinstance(dimensions, dict):
            issues.append(f"{label}.dimensions must be an object")
            continue
        if set(dimensions) != set(REQUIRED_ITEM_DIMENSIONS):
            issues.append(
                f"{label}.dimensions must contain exactly "
                f"{list(REQUIRED_ITEM_DIMENSIONS)}"
            )
        for dimension_name in REQUIRED_ITEM_DIMENSIONS:
            dimension = dimensions.get(dimension_name)
            dimension_label = f"{label}.dimensions.{dimension_name}"
            if not isinstance(dimension, dict):
                issues.append(f"{dimension_label} must be an object")
                continue
            status = dimension.get("status")
            if status not in {"covered", "not_applicable"}:
                issues.append(
                    f"{dimension_label}.status must be covered or not_applicable"
                )
                continue
            if dimension_name == "core_facts" and status != "covered":
                issues.append(f"{dimension_label} must be covered")
            if status == "not_applicable":
                if not _nonempty_string(dimension.get("rationale")):
                    issues.append(
                        f"{dimension_label}.rationale is required when not_applicable"
                    )
                continue
            refs = _string_list(
                dimension.get("document_refs"),
                f"{dimension_label}.document_refs",
                issues,
            )
            if not refs:
                issues.append(f"{dimension_label} covered status requires document_refs")
            for ref_index, ref in enumerate(refs):
                if len(ref) > 240:
                    issues.append(
                        f"{dimension_label}.document_refs[{ref_index}] must be concise"
                    )
                if ref not in section_text:
                    issues.append(
                        f"{dimension_label}.document_refs[{ref_index}] was not found "
                        "in the assigned minutes.md section"
                    )

    if set(seen_ids) != required_ids or len(seen_ids) != len(required_ids):
        issues.append(
            "content_quality_review.json required_item_checks must exactly cover "
            "every required inventory item once"
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
