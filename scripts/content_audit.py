from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


AUDIT_MODES = {"off", "warn", "strict"}
OFFICIAL_SOURCE_MODES = {"off", "auto", "required"}


def content_fidelity_instruction(
    audit_mode: str,
    official_source_verification: str,
) -> str:
    """Return the Codex-side authoring and evidence-validation contract."""
    if audit_mode not in AUDIT_MODES:
        raise ValueError(f"Unsupported content audit mode: {audit_mode}")
    if official_source_verification not in OFFICIAL_SOURCE_MODES:
        raise ValueError(
            "Unsupported official source verification mode: "
            f"{official_source_verification}"
        )

    parts = [
        "## Content fidelity and length policy / 내용 보존 및 길이 정책",
        "",
        "최종 문서에는 글자 수, 토큰 수, 페이지 수, bullet 수, section 수의 "
        "하드 상한이 없습니다. 핵심 요약은 간결하게 쓸 수 있지만, 본문은 필수 "
        "근거를 정확히 보존하는 데 필요한 만큼 충분히 작성하세요.",
        "길이를 줄이기 위해 정책, 날짜, 버전, 수치, 범위, 단위, 조건, 예외, "
        "제한, 부정 표현, API·명령어, 위험, 질문과 답변, 근거 충돌을 생략하지 "
        "마세요. 인사, 말버릇, 의미가 완전히 같은 반복만 축약할 수 있습니다.",
        "원문의 의미 강도를 보존하세요. 예시·anecdotal 수치를 권장값이나 보장값으로, "
        "추정이나 발표자 주장을 확정된 공식 사실로, may/can을 must/will로 바꾸지 "
        "마세요.",
        "최종 문서 본문의 기준은 영상에서 실제로 전달된 내용입니다. 외부 문서의 현재 "
        "정보를 이용해 영상의 명확한 발언을 수정·대체하거나, 영상에서 말하지 않은 현재 "
        "사실을 영상 내용처럼 섞지 마세요.",
        "원문이 한 번에 읽기에는 길면 시간 구간별로 순차 처리해 누적 inventory를 "
        "만드세요. 구간을 서술형 부분 요약으로 바꾼 뒤 그 요약들을 다시 요약하지 "
        "마세요. context 한계는 처리 순서를 나누는 이유일 뿐 최종 내용 생략이나 "
        "길이 제한의 근거가 아닙니다.",
    ]
    if audit_mode == "off":
        return "\n".join(parts)

    parts.extend(
        [
            "",
            "## Required evidence workflow / 필수 근거 워크플로",
            "",
            f"내용 감사 모드: {audit_mode}",
            "완성본을 쓰기 전에 같은 job 폴더에 content_inventory.json을 만들고, "
            "완성본을 쓴 뒤 content_audit.json을 만드세요.",
            "content_inventory.json에는 schema_version=1, items, conflicts를 두고 "
            "시간순 evidence item과 source conflict를 기록하세요. 각 item은 다음 "
            "필드를 사용합니다: id, time_range, category, "
            "statement, importance(required|optional), qualifier, source_refs, "
            "official_verification(required|not_applicable).",
            "날짜·버전·수치·정책·결정·권고·예시·추정·제한·예외·위험·action·"
            "질문/답변·API/명령어는 원칙적으로 required로 분류하세요.",
            "STT와 OCR 또는 서로 다른 구간의 근거가 충돌하면 conflicts 배열에 "
            "id, description, source_refs를 기록하고 임의로 합치지 마세요.",
            "content_audit.json에는 schema_version=1, status, covered_item_ids, "
            "missing_item_ids, qualifier_changes, silent_conflicts, "
            "documented_conflict_ids, coverage, conflict_coverage, "
            "recording_fidelity, intentional_omissions를 기록하세요. recording_fidelity에는 "
            "preserved_item_ids와 rewritten_by_external_source_item_ids를 둡니다.",
            "coverage의 각 항목은 item_id와 document_refs를, conflict_coverage의 "
            "각 항목은 conflict_id와 document_refs를 사용합니다. document_refs에는 "
            "해당 근거가 실제 반영됐음을 입증하는 minutes.md의 짧고 고유한 원문 문자열을 "
            "넣으세요. 섹션 제목만으로 입증하지 마세요.",
            "required item은 모두 covered_item_ids에 있어야 합니다. required item을 "
            "intentional omission으로 처리할 수 없습니다. qualifier_changes, "
            "silent_conflicts, missing_item_ids, rewritten_by_external_source_item_ids는 "
            "비어 있어야 status=passed입니다.",
        ]
    )

    if official_source_verification != "off":
        requirement = (
            "외부 검증 가능한 최신성 민감 주장을 폭넓게 확인하세요."
            if official_source_verification == "required"
            else "OCR·STT가 모호하거나 서로 충돌하거나 고유명사·버전 표기가 불확실할 "
            "때만 보조적으로 확인하세요."
        )
        parts.extend(
            [
                "",
                "## Latest official-source verification / 최신 공식 문서 검증",
                "",
                f"공식 문서 검증 모드: {official_source_verification}. {requirement}",
                "auto 모드에서는 로컬 근거 교차 확인 후에도 남은 모호성·충돌에 해당하는 "
                "inventory item만 official_verification=required로 표시하고, 명확한 영상 "
                "내용은 not_applicable로 유지하세요.",
                "먼저 음성 문맥, 시간 인접 STT, OCR, Snapshot을 교차 확인하세요. 그래도 "
                "의미나 표기가 불명확할 때 제품 지원 상태, 버전, 출시·종료 일정, 규정, "
                "보안, API 동작을 최신 공식 문서, 공식 release note, 공식 service "
                "announcement, 표준 원문 또는 upstream maintainer 문서에서 조사하세요.",
                "검색 서비스에 원문 STT·OCR 문장, 고객명, 참석자명, 이메일, 내부 프로젝트명, "
                "식별자, 비밀정보를 보내지 마세요. 검색어는 공개 제품명·버전·일반화한 "
                "정책 주장으로 최소화하세요.",
                "공식 문서는 불명확한 단어·제품명·버전 표기를 보조할 수 있지만 명확한 영상 "
                "발언의 의미를 바꿀 수 없습니다. 최신 공식 문서가 영상 발언과 상충하면 "
                "본문에는 영상 내용을 그대로 유지하세요.",
                "공식 문서를 이용해 모호한 전사·OCR을 보강했거나 상충 여부를 확인했다면 "
                "최종 문서 맨 아래에 한국어 문서는 "
                "`## 외부 근거 확인`, 영어 문서는 `## External Evidence Check` 섹션을 "
                "추가하세요. 내부에서 `전사·OCR 보강 근거`와 `영상 내용과 상충하는 근거`를 "
                "구분하고, 각 항목에 영상에서 말한 내용과 timestamp, 공식 문서를 사용한 "
                "목적, 확인 결과, 차이 또는 보강한 표현, 확인일, 공식 Markdown 링크를 함께 "
                "적으세요. 이 섹션 뒤에는 다른 H2 섹션을 추가하지 마세요.",
                "같은 job 폴더에 official_sources.json을 작성하세요. schema_version=1, "
                "status(completed|not_applicable|blocked), checked_at, policy=official_only, "
                "appendix_heading, claims, privacy를 포함하세요.",
                "각 claim은 inventory_item_ids, status(verified|contradicted|"
                "partially_verified|not_found), current_official_finding, "
                "purpose(transcription_disambiguation|source_conflict_resolution|"
                "current_fact_check), recording_content_preserved, document_treatment, "
                "appendix_category(transcription_or_ocr_support|video_conflict|"
                "current_official_status), appendix_category_heading, "
                "recording_document_refs, appendix_document_refs, sources를 포함합니다. "
                "recording_document_refs는 영상 내용이 본문에 유지됐음을 입증하는 고유 문자열, "
                "appendix_document_refs는 상충 결과가 마지막 검증 섹션에 기록됐음을 입증하는 "
                "고유 문자열입니다. 각 source에는 title, url, publisher, "
                "source_type=official, published_or_updated를 기록하세요.",
                "privacy.raw_transcript_or_ocr_sent는 반드시 false여야 합니다. 관련 공식 "
                "자료에서 직접 확인하지 못하면 not_found로 표시하고, 확인한 공식 문서·검색 "
                "범위의 링크와 조사 범위를 마지막 섹션에 기록하며 현재 사실처럼 단정하지 "
                "마세요. 검증 대상 자체가 없으면 status=not_applicable과 reason을 쓰세요.",
            ]
        )

    parts.extend(
        [
            "",
            "작성 순서: content_inventory.json → 필요한 공식 문서 조사 및 "
            "official_sources.json → minutes.md → content_audit.json → archive_job.py.",
            "감사가 통과하지 않으면 문서를 보완한 뒤 다시 감사하고, 통과 전에는 "
            "archive_job.py를 실행하지 마세요.",
        ]
    )
    return "\n".join(parts)


def validate_content_artifacts(
    job_dir: Path,
    *,
    audit_mode: str,
    official_source_verification: str,
) -> dict[str, Any]:
    """Validate Codex evidence artifacts before final output is archived."""
    if audit_mode not in AUDIT_MODES:
        raise ValueError(f"Unsupported content audit mode: {audit_mode}")
    if official_source_verification not in OFFICIAL_SOURCE_MODES:
        raise ValueError(
            "Unsupported official source verification mode: "
            f"{official_source_verification}"
        )
    if audit_mode == "off":
        return {
            "mode": "off",
            "status": "not_required",
            "official_source_verification": official_source_verification,
            "issues": [],
        }

    issues: list[str] = []
    inventory = _read_object(job_dir / "content_inventory.json", issues)
    audit = _read_object(job_dir / "content_audit.json", issues)
    minutes_text = _read_text(job_dir / "minutes.md", issues)
    inventory_ids, required_ids, conflict_ids, official_ids = _validate_inventory(
        inventory,
        issues,
    )
    _validate_audit(
        audit,
        inventory_ids=inventory_ids,
        required_ids=required_ids,
        conflict_ids=conflict_ids,
        minutes_text=minutes_text,
        issues=issues,
    )

    official_status = "not_required"
    if official_source_verification != "off":
        official = _read_object(job_dir / "official_sources.json", issues)
        official_status = _validate_official_sources(
            official,
            inventory_ids=inventory_ids,
            official_ids=official_ids,
            verification_mode=official_source_verification,
            minutes_text=minutes_text,
            issues=issues,
        )

    result = {
        "mode": audit_mode,
        "status": "passed" if not issues else "failed",
        "official_source_verification": official_source_verification,
        "official_source_status": official_status,
        "inventory_items": len(inventory_ids),
        "required_items": len(required_ids),
        "conflicts": len(conflict_ids),
        "issues": issues,
    }
    if issues and audit_mode == "strict":
        raise ValueError("content audit failed: " + "; ".join(issues))
    return result


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


def _read_text(path: Path, issues: list[str]) -> str:
    if not path.is_file():
        issues.append(f"missing {path.name}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        issues.append(f"cannot read {path.name}: {exc}")
        return ""


def _validate_inventory(
    inventory: dict[str, Any],
    issues: list[str],
) -> tuple[set[str], set[str], set[str], set[str]]:
    if not inventory:
        return set(), set(), set(), set()
    if inventory.get("schema_version") != 1:
        issues.append("content_inventory.json schema_version must be 1")

    items = inventory.get("items")
    if not isinstance(items, list) or not items:
        issues.append("content_inventory.json items must be a non-empty list")
        return set(), set(), set(), set()

    item_ids: set[str] = set()
    required_ids: set[str] = set()
    official_ids: set[str] = set()
    for index, item in enumerate(items):
        label = f"content_inventory.json items[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label} must be an object")
            continue
        item_id = _nonempty_string(item.get("id"))
        if not item_id:
            issues.append(f"{label}.id is required")
            continue
        if item_id in item_ids:
            issues.append(f"duplicate inventory item id: {item_id}")
            continue
        item_ids.add(item_id)
        if not _nonempty_string(item.get("time_range")):
            issues.append(f"{label}.time_range is required")
        if not _nonempty_string(item.get("category")):
            issues.append(f"{label}.category is required")
        if not _nonempty_string(item.get("statement")):
            issues.append(f"{label}.statement is required")
        if not _nonempty_string(item.get("qualifier")):
            issues.append(f"{label}.qualifier is required")
        source_refs = item.get("source_refs")
        if not isinstance(source_refs, list) or not any(
            _nonempty_string(ref) for ref in source_refs
        ):
            issues.append(f"{label}.source_refs must contain evidence references")
        importance = item.get("importance")
        if importance not in {"required", "optional"}:
            issues.append(f"{label}.importance must be required or optional")
        elif importance == "required":
            required_ids.add(item_id)
        verification = item.get("official_verification")
        if verification not in {"required", "not_applicable"}:
            issues.append(
                f"{label}.official_verification must be required or not_applicable"
            )
        elif verification == "required":
            official_ids.add(item_id)

    conflicts = inventory.get("conflicts", [])
    conflict_ids: set[str] = set()
    if not isinstance(conflicts, list):
        issues.append("content_inventory.json conflicts must be a list")
    else:
        for index, conflict in enumerate(conflicts):
            label = f"content_inventory.json conflicts[{index}]"
            if not isinstance(conflict, dict):
                issues.append(f"{label} must be an object")
                continue
            conflict_id = _nonempty_string(conflict.get("id"))
            if not conflict_id:
                issues.append(f"{label}.id is required")
                continue
            if conflict_id in conflict_ids:
                issues.append(f"duplicate conflict id: {conflict_id}")
                continue
            conflict_ids.add(conflict_id)
            if not _nonempty_string(conflict.get("description")):
                issues.append(f"{label}.description is required")
            refs = conflict.get("source_refs")
            if not isinstance(refs, list) or len(
                [ref for ref in refs if _nonempty_string(ref)]
            ) < 2:
                issues.append(f"{label}.source_refs must contain both sides")
    return item_ids, required_ids, conflict_ids, official_ids


def _validate_audit(
    audit: dict[str, Any],
    *,
    inventory_ids: set[str],
    required_ids: set[str],
    conflict_ids: set[str],
    minutes_text: str,
    issues: list[str],
) -> None:
    if not audit:
        return
    if audit.get("schema_version") != 1:
        issues.append("content_audit.json schema_version must be 1")
    if audit.get("status") != "passed":
        issues.append("content_audit.json status must be passed")

    covered = _string_set(audit.get("covered_item_ids"), "covered_item_ids", issues)
    missing = _string_set(audit.get("missing_item_ids"), "missing_item_ids", issues)
    documented_conflicts = _string_set(
        audit.get("documented_conflict_ids"),
        "documented_conflict_ids",
        issues,
    )
    if missing:
        issues.append(f"missing required content items: {sorted(missing)}")
    uncovered = required_ids - covered
    if uncovered:
        issues.append(f"required inventory items are not covered: {sorted(uncovered)}")
    unknown_covered = covered - inventory_ids
    if unknown_covered:
        issues.append(f"audit references unknown inventory items: {sorted(unknown_covered)}")
    undocumented = conflict_ids - documented_conflicts
    if undocumented:
        issues.append(f"source conflicts are not documented: {sorted(undocumented)}")

    fidelity = audit.get("recording_fidelity")
    if not isinstance(fidelity, dict):
        issues.append("content_audit.json recording_fidelity must be an object")
    else:
        preserved = _string_set(
            fidelity.get("preserved_item_ids"),
            "recording_fidelity.preserved_item_ids",
            issues,
        )
        rewritten = _string_set(
            fidelity.get("rewritten_by_external_source_item_ids"),
            "recording_fidelity.rewritten_by_external_source_item_ids",
            issues,
        )
        unknown_preserved = preserved - inventory_ids
        if unknown_preserved:
            issues.append(
                "recording fidelity references unknown inventory items: "
                f"{sorted(unknown_preserved)}"
            )
        unpreserved = covered - preserved
        if unpreserved:
            issues.append(
                "covered video items are not marked as recording-preserved: "
                f"{sorted(unpreserved)}"
            )
        if rewritten:
            issues.append(
                "video content was rewritten from an external source: "
                f"{sorted(rewritten)}"
            )

    coverage_ids = _validate_document_coverage(
        audit.get("coverage"),
        id_field="item_id",
        valid_ids=inventory_ids,
        minutes_text=minutes_text,
        label="coverage",
        issues=issues,
    )
    missing_coverage = required_ids - coverage_ids
    if missing_coverage:
        issues.append(
            "required inventory items lack final-document evidence: "
            f"{sorted(missing_coverage)}"
        )
    claimed_without_evidence = covered - coverage_ids
    if claimed_without_evidence:
        issues.append(
            "covered items lack final-document evidence: "
            f"{sorted(claimed_without_evidence)}"
        )
    conflict_coverage_ids = _validate_document_coverage(
        audit.get("conflict_coverage"),
        id_field="conflict_id",
        valid_ids=conflict_ids,
        minutes_text=minutes_text,
        label="conflict_coverage",
        issues=issues,
    )
    conflicts_without_evidence = documented_conflicts - conflict_coverage_ids
    if conflicts_without_evidence:
        issues.append(
            "documented conflicts lack final-document evidence: "
            f"{sorted(conflicts_without_evidence)}"
        )

    qualifier_changes = audit.get("qualifier_changes")
    if not isinstance(qualifier_changes, list):
        issues.append("content_audit.json qualifier_changes must be a list")
    elif qualifier_changes:
        issues.append("content_audit.json contains qualifier changes")
    silent_conflicts = audit.get("silent_conflicts")
    if not isinstance(silent_conflicts, list):
        issues.append("content_audit.json silent_conflicts must be a list")
    elif silent_conflicts:
        issues.append("content_audit.json contains silent conflicts")

    omissions = audit.get("intentional_omissions")
    omitted_ids: set[str] = set()
    if not isinstance(omissions, list):
        issues.append("content_audit.json intentional_omissions must be a list")
    else:
        for index, omission in enumerate(omissions):
            if not isinstance(omission, dict):
                issues.append(f"intentional_omissions[{index}] must be an object")
                continue
            item_id = _nonempty_string(omission.get("item_id"))
            if not item_id or item_id not in inventory_ids:
                issues.append(f"intentional_omissions[{index}] has an unknown item_id")
            elif item_id in required_ids:
                issues.append(f"required item cannot be intentionally omitted: {item_id}")
            elif item_id in covered:
                issues.append(f"item cannot be covered and intentionally omitted: {item_id}")
            else:
                omitted_ids.add(item_id)
            if not _nonempty_string(omission.get("reason")):
                issues.append(f"intentional_omissions[{index}].reason is required")
    unaccounted = inventory_ids - covered - omitted_ids
    if unaccounted:
        issues.append(f"inventory items lack a final disposition: {sorted(unaccounted)}")


def _validate_official_sources(
    official: dict[str, Any],
    *,
    inventory_ids: set[str],
    official_ids: set[str],
    verification_mode: str,
    minutes_text: str,
    issues: list[str],
) -> str:
    if not official:
        return "missing"
    if official.get("schema_version") != 1:
        issues.append("official_sources.json schema_version must be 1")
    status = official.get("status")
    if status not in {"completed", "not_applicable"}:
        issues.append("official_sources.json status must be completed or not_applicable")
        return str(status or "invalid")
    if official.get("policy") != "official_only":
        issues.append("official_sources.json policy must be official_only")
    privacy = official.get("privacy")
    if not isinstance(privacy, dict) or privacy.get(
        "raw_transcript_or_ocr_sent"
    ) is not False:
        issues.append(
            "official_sources.json must confirm raw_transcript_or_ocr_sent=false"
        )
    checked_at = _nonempty_string(official.get("checked_at"))
    if not checked_at:
        issues.append("official_sources.json checked_at is required")
    else:
        try:
            datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        except ValueError:
            issues.append("official_sources.json checked_at must be ISO 8601")

    if status == "not_applicable":
        if official_ids:
            issues.append(
                "official verification is marked not_applicable despite required items"
            )
        if not _nonempty_string(official.get("reason")):
            issues.append("official_sources.json reason is required when not_applicable")
        return status

    claims = official.get("claims")
    if not isinstance(claims, list):
        issues.append("official_sources.json claims must be a list")
        return status
    if not claims and not official_ids:
        issues.append(
            "official_sources.json must use status=not_applicable when no claims apply"
        )

    appendix_required = bool(claims)
    appendix_heading = _nonempty_string(official.get("appendix_heading"))
    appendix_start = len(minutes_text)
    appendix_text = ""
    if appendix_required:
        if not appendix_heading:
            issues.append(
                "official_sources.json appendix_heading is required when official evidence "
                "was consulted"
            )
        else:
            appendix_start, appendix_text = _extract_final_appendix(
                minutes_text,
                appendix_heading,
                issues,
            )
    body_text = minutes_text[:appendix_start]

    checked_ids: set[str] = set()
    for index, claim in enumerate(claims):
        label = f"official_sources.json claims[{index}]"
        if not isinstance(claim, dict):
            issues.append(f"{label} must be an object")
            continue
        claim_ids = _string_set(
            claim.get("inventory_item_ids"),
            f"{label}.inventory_item_ids",
            issues,
        )
        unknown = claim_ids - inventory_ids
        if unknown:
            issues.append(f"{label} references unknown inventory items: {sorted(unknown)}")
        checked_ids.update(claim_ids & inventory_ids)
        claim_status = claim.get("status")
        if claim_status not in {
            "verified",
            "contradicted",
            "partially_verified",
            "not_found",
        }:
            issues.append(f"{label}.status is invalid")
        purpose = claim.get("purpose")
        if purpose not in {
            "transcription_disambiguation",
            "source_conflict_resolution",
            "current_fact_check",
        }:
            issues.append(f"{label}.purpose is invalid")
        elif verification_mode == "auto" and purpose == "current_fact_check":
            issues.append(
                f"{label}.purpose=current_fact_check is not allowed in auto mode"
            )
        if claim_status in {"contradicted", "partially_verified"} and purpose != (
            "source_conflict_resolution"
        ):
            issues.append(
                f"{label}.purpose must be source_conflict_resolution for a conflict"
            )
        category = claim.get("appendix_category")
        expected_category = {
            "transcription_disambiguation": "transcription_or_ocr_support",
            "source_conflict_resolution": "video_conflict",
            "current_fact_check": "current_official_status",
        }.get(purpose)
        if category != expected_category:
            issues.append(
                f"{label}.appendix_category must be {expected_category} for {purpose}"
            )
        category_heading = _nonempty_string(claim.get("appendix_category_heading"))
        if not category_heading:
            issues.append(f"{label}.appendix_category_heading is required")
        elif not re.search(
            rf"(?m)^###[ \t]+{re.escape(category_heading)}[ \t]*$",
            appendix_text,
        ):
            issues.append(
                f"{label}.appendix_category_heading was not found in the final appendix"
            )
        if claim.get("recording_content_preserved") is not True:
            issues.append(f"{label}.recording_content_preserved must be true")
        if not _nonempty_string(claim.get("current_official_finding")):
            issues.append(f"{label}.current_official_finding is required")
        if not _nonempty_string(claim.get("document_treatment")):
            issues.append(f"{label}.document_treatment is required")
        recording_refs = claim.get("recording_document_refs")
        if not isinstance(recording_refs, list) or not recording_refs:
            issues.append(
                f"{label}.recording_document_refs must be a non-empty list"
            )
        else:
            for ref_index, ref in enumerate(recording_refs):
                clean_ref = _nonempty_string(ref)
                if not clean_ref:
                    issues.append(
                        f"{label}.recording_document_refs[{ref_index}] must be non-empty"
                    )
                elif clean_ref not in body_text:
                    issues.append(
                        f"{label}.recording_document_refs[{ref_index}] was not found "
                        "in the video-content body of minutes.md"
                    )
        appendix_refs = claim.get("appendix_document_refs")
        if not isinstance(appendix_refs, list):
            issues.append(f"{label}.appendix_document_refs must be a list")
            appendix_refs = []
        if not appendix_refs:
            issues.append(
                f"{label}.appendix_document_refs must document the external evidence use"
            )
        for ref_index, ref in enumerate(appendix_refs):
            clean_ref = _nonempty_string(ref)
            if not clean_ref:
                issues.append(
                    f"{label}.appendix_document_refs[{ref_index}] must be non-empty"
                )
            elif clean_ref not in appendix_text:
                issues.append(
                    f"{label}.appendix_document_refs[{ref_index}] was not found "
                    "in the final official-evidence appendix"
                )
        sources = claim.get("sources")
        if not isinstance(sources, list):
            issues.append(f"{label}.sources must be a list")
            continue
        if not sources:
            issues.append(f"{label}.sources must contain official evidence")
        cited_in_appendix = False
        for source_index, source in enumerate(sources):
            source_label = f"{label}.sources[{source_index}]"
            if not isinstance(source, dict):
                issues.append(f"{source_label} must be an object")
                continue
            if source.get("source_type") != "official":
                issues.append(f"{source_label}.source_type must be official")
            if not _nonempty_string(source.get("title")):
                issues.append(f"{source_label}.title is required")
            if not _nonempty_string(source.get("publisher")):
                issues.append(f"{source_label}.publisher is required")
            if not _nonempty_string(source.get("published_or_updated")):
                issues.append(f"{source_label}.published_or_updated is required")
            url = _nonempty_string(source.get("url"))
            parsed = urlparse(url) if url else None
            if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                issues.append(f"{source_label}.url must be an HTTP(S) URL")
            elif url in appendix_text:
                cited_in_appendix = True
        if sources and not cited_in_appendix:
            issues.append(
                f"{label} has no official source URL cited in the final appendix"
            )

    unchecked = official_ids - checked_ids
    if unchecked:
        issues.append(f"official verification items were not checked: {sorted(unchecked)}")
    return status


def _extract_final_appendix(
    minutes_text: str,
    heading: str,
    issues: list[str],
) -> tuple[int, str]:
    exact_pattern = re.compile(
        rf"(?m)^##[ \t]+{re.escape(heading)}[ \t]*$"
    )
    exact_matches = list(exact_pattern.finditer(minutes_text))
    if not exact_matches:
        issues.append(
            f"minutes.md is missing the official-evidence appendix: ## {heading}"
        )
        return len(minutes_text), ""

    appendix_match = exact_matches[-1]
    h2_matches = list(re.finditer(r"(?m)^##[ \t]+.+$", minutes_text))
    if h2_matches and h2_matches[-1].start() != appendix_match.start():
        issues.append("the official-evidence appendix must be the final H2 section")
    return appendix_match.start(), minutes_text[appendix_match.start() :]


def _validate_document_coverage(
    value: Any,
    *,
    id_field: str,
    valid_ids: set[str],
    minutes_text: str,
    label: str,
    issues: list[str],
) -> set[str]:
    if not isinstance(value, list):
        issues.append(f"content_audit.json {label} must be a list")
        return set()
    covered_ids: set[str] = set()
    for index, entry in enumerate(value):
        entry_label = f"content_audit.json {label}[{index}]"
        if not isinstance(entry, dict):
            issues.append(f"{entry_label} must be an object")
            continue
        reference_id = _nonempty_string(entry.get(id_field))
        if not reference_id or reference_id not in valid_ids:
            issues.append(f"{entry_label}.{id_field} is unknown")
            continue
        if reference_id in covered_ids:
            issues.append(f"{entry_label}.{id_field} is duplicated")
            continue
        covered_ids.add(reference_id)
        document_refs = entry.get("document_refs")
        if not isinstance(document_refs, list) or not document_refs:
            issues.append(f"{entry_label}.document_refs must be a non-empty list")
            continue
        for ref_index, ref in enumerate(document_refs):
            clean_ref = _nonempty_string(ref)
            if not clean_ref:
                issues.append(
                    f"{entry_label}.document_refs[{ref_index}] must be non-empty"
                )
            elif clean_ref not in minutes_text:
                issues.append(
                    f"{entry_label}.document_refs[{ref_index}] was not found in minutes.md"
                )
    return covered_ids


def _string_set(value: Any, name: str, issues: list[str]) -> set[str]:
    if not isinstance(value, list):
        issues.append(f"{name} must be a list")
        return set()
    result = {_nonempty_string(item) for item in value}
    result.discard("")
    return result


def _nonempty_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
