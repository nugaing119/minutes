from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.media_types import is_video_extension
from scripts.content_quality import (
    TRUST_APPENDIX_DISCLOSURES,
    TRUST_SECTION_HEADINGS,
    validate_content_quality_artifacts,
)


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
            "영상 job에서는 bounded evidence_coverage_summary.json을 먼저 확인하고 "
            "원시 evidence_coverage.json을 직접 출력하지 마세요. coverage_passed=true, "
            "accounting_complete=true, 최대 Snapshot 간격 정책 통과 여부를 검증하세요. "
            "source_refs의 로컬 근거는 `STT:HH:MM:SS-HH:MM:SS`, "
            "`OCR:HH:MM:SS`, `Snapshot:snapshot-NNNN@HH:MM:SS` 형식으로 기록하세요. "
            "STT 없이 시각 자료만으로 뒷받침되는 항목은 반드시 Snapshot ref를 포함하세요.",
        ]
    )

    if official_source_verification != "off":
        requirement = (
            "외부 검증 가능한 최신성 민감 주장을 폭넓게 확인하세요."
            if official_source_verification == "required"
            else "로컬 교차 확인 후에도 모호·충돌하거나, 외부에서 확인 가능한 제품 지원·"
            "버전·출시·종료 일정·정책·보안·API 주장이 미해결 상태일 때 확인하세요."
        )
        parts.extend(
            [
                "",
                "## Latest official-source verification / 최신 공식 문서 검증",
                "",
                f"공식 문서 검증 모드: {official_source_verification}. {requirement}",
                "auto 모드에서는 로컬 근거 교차 확인 후에도 남은 모호성·충돌과, 외부에서 "
                "확인 가능한 제품 지원·버전·출시·EOL·정책·보안·API 주장을 "
                "official_verification=required로 표시하세요. 발표자 설명이나 추정이라는 "
                "이유만으로 확인을 생략하지 말고 그 qualifier를 본문에 보존하세요. 사내 "
                "결정과 POC 측정값은 로컬 추가 검증 대상으로 남길 수 있습니다.",
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
                "최종 두 H2는 한국어 문서의 `## 추가 검증이 필요한 항목`과 "
                "`## 외부 근거 확인`, 영어 문서의 "
                "`## Items Requiring Further Verification`과 "
                "`## External Evidence Check`로 고정하세요. 번호는 앞선 주제 수에 따라 "
                "자동으로 이어지며 제목에 하드코딩하지 마세요. 미해결 항목이 없거나 외부 "
                "확인이 필요하지 않아도 섹션을 삭제하지 말고 구체적인 판단 사유를 표시하세요.",
                "외부 확인 섹션에는 확인일과 다음 언어별 정책 문장을 그대로 넣으세요: "
                f"한국어 `{TRUST_APPENDIX_DISCLOSURES['ko'][0]}` / "
                f"`{TRUST_APPENDIX_DISCLOSURES['ko'][1]}`; 영어 "
                f"`{TRUST_APPENDIX_DISCLOSURES['en'][0]}` / "
                f"`{TRUST_APPENDIX_DISCLOSURES['en'][1]}`.",
                "공식 문서를 이용했다면 내부에서 `전사·OCR 보강 근거`와 "
                "`영상 내용과 상충하는 근거`를 구분하고, 각 항목에 영상 내용과 timestamp, "
                "조사 목적, 확인 결과, 차이 또는 보강한 표현, 확인일, 공식 Markdown 링크를 "
                "함께 적으세요. 외부 확인 섹션 뒤에는 다른 H2를 추가하지 마세요.",
                "같은 job 폴더에 official_sources.json을 작성하세요. schema_version=1, "
                "status(completed|not_applicable), 실제 현재 시각의 timezone-aware checked_at "
                "(미래 시각 금지), policy=official_only, "
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
                "마세요. 검증 대상 자체가 없으면 status=not_applicable과 reason을 쓰고, "
                "같은 reason을 마지막 외부 확인 섹션에 그대로 표시하세요.",
            ]
        )

    parts.extend(
        [
            "",
            "작성 순서: evidence_ledger.json → content_inventory.json → "
            "document_blueprint.json → 필요한 공식 문서 조사 및 official_sources.json → "
            "minutes.md → content_audit.json → content_quality_review.json → "
            "content_freeze.py. fresh-context strict job에서는 minutes 스킬의 "
            "references/quality-loop.md에 따라 reader-facing blueprint와 adversarial "
            "review를 모두 통과하고 본문을 동결하세요. 별도 delivery worker만 DOCX와 "
            "archive_job.py를 실행합니다.",
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
    (
        inventory_ids,
        required_ids,
        conflict_ids,
        official_ids,
        inventory_source_refs,
    ) = _validate_inventory(
        inventory,
        issues,
    )
    video_sources = [
        path for path in job_dir.glob("source.*") if is_video_extension(path.suffix)
    ]
    coverage_required = bool(video_sources) or (job_dir / "evidence_coverage.json").exists()
    coverage_summary: dict[str, Any] = {
        "required": coverage_required,
        "status": "not_required",
        "resolved_inventory_refs": 0,
    }
    if coverage_required:
        evidence_coverage = _read_object(job_dir / "evidence_coverage.json", issues)
        coverage_summary = _validate_evidence_coverage(
            job_dir,
            evidence_coverage,
            issues,
        )
        reference_summary = _validate_inventory_evidence_refs(
            job_dir,
            inventory_source_refs,
            required_ids=required_ids,
            evidence_coverage=evidence_coverage,
            issues=issues,
        )
        coverage_summary.update(reference_summary)
    _validate_audit(
        audit,
        inventory_ids=inventory_ids,
        required_ids=required_ids,
        conflict_ids=conflict_ids,
        minutes_text=minutes_text,
        issues=issues,
    )

    quality_summary = validate_content_quality_artifacts(
        job_dir,
        inventory_ids=inventory_ids,
        required_ids=required_ids,
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
        "captured_evidence_coverage": coverage_summary,
        "content_quality_review": quality_summary,
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
) -> tuple[
    set[str],
    set[str],
    set[str],
    set[str],
    dict[str, list[str]],
]:
    if not inventory:
        return set(), set(), set(), set(), {}
    if inventory.get("schema_version") != 1:
        issues.append("content_inventory.json schema_version must be 1")

    items = inventory.get("items")
    if not isinstance(items, list) or not items:
        issues.append("content_inventory.json items must be a non-empty list")
        return set(), set(), set(), set(), {}

    item_ids: set[str] = set()
    required_ids: set[str] = set()
    official_ids: set[str] = set()
    item_source_refs: dict[str, list[str]] = {}
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
        else:
            item_source_refs[item_id] = [
                clean
                for ref in source_refs
                if (clean := _nonempty_string(ref))
            ]
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
    return item_ids, required_ids, conflict_ids, official_ids, item_source_refs


def _validate_evidence_coverage(
    job_dir: Path,
    coverage: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    if not coverage:
        return {
            "required": True,
            "status": "failed",
            "resolved_inventory_refs": 0,
        }
    if coverage.get("schema_version") != 1:
        issues.append("evidence_coverage.json schema_version must be 1")
    if coverage.get("coverage_passed") is not True:
        issues.append("evidence_coverage.json coverage_passed must be true")
    if coverage.get("accounting_complete") is not True:
        issues.append("evidence_coverage.json accounting_complete must be true")

    frames = coverage.get("frames")
    if not isinstance(frames, list) or not frames:
        issues.append("evidence_coverage.json frames must be a non-empty list")
        frames = []
    raw_frame_count = _nonnegative_int(coverage.get("raw_frame_count"))
    selected_snapshot_count = _nonnegative_int(
        coverage.get("selected_snapshot_count")
    )
    accounted_frame_count = _nonnegative_int(
        coverage.get("accounted_frame_count")
    )
    if raw_frame_count is None or raw_frame_count != len(frames):
        issues.append("evidence coverage raw_frame_count does not match frame records")
    if accounted_frame_count is None or accounted_frame_count != len(frames):
        issues.append("evidence coverage accounted_frame_count does not match frame records")

    reason_counts = coverage.get("reason_counts")
    if not isinstance(reason_counts, dict) or any(
        not isinstance(value, int) or value < 0 for value in reason_counts.values()
    ):
        issues.append("evidence_coverage.json reason_counts must contain nonnegative integers")
        reason_counts = {}
    if sum(reason_counts.values()) != len(frames):
        issues.append("evidence coverage reason counts do not account for every raw frame")

    selected_records = 0
    seen_ids: set[str] = set()
    for index, frame in enumerate(frames):
        label = f"evidence_coverage.json frames[{index}]"
        if not isinstance(frame, dict):
            issues.append(f"{label} must be an object")
            continue
        evidence_id = _nonempty_string(frame.get("evidence_id"))
        if not evidence_id or evidence_id in seen_ids:
            issues.append(f"{label}.evidence_id must be unique and non-empty")
        else:
            seen_ids.add(evidence_id)
        raw_path = _safe_job_evidence_path(job_dir, frame.get("raw_frame"))
        if raw_path is None or not raw_path.is_file():
            issues.append(f"{label} raw frame is missing or outside the job")
        else:
            expected_hash = _nonempty_string(frame.get("raw_frame_sha256"))
            if not expected_hash or _sha256_file(raw_path) != expected_hash:
                issues.append(f"{label} raw frame hash does not match")
        if frame.get("selected") is True:
            selected_records += 1
            snapshot_path = _safe_job_evidence_path(job_dir, frame.get("snapshot"))
            if snapshot_path is None or not snapshot_path.is_file():
                issues.append(f"{label} selected snapshot is missing or outside the job")
            else:
                expected_snapshot_hash = _nonempty_string(
                    frame.get("snapshot_sha256")
                )
                if (
                    not expected_snapshot_hash
                    or _sha256_file(snapshot_path) != expected_snapshot_hash
                ):
                    issues.append(f"{label} selected snapshot hash does not match")
    if selected_snapshot_count is None or selected_snapshot_count != selected_records:
        issues.append("evidence coverage selected_snapshot_count does not match records")

    max_gap = _nonnegative_number(
        coverage.get("max_selected_snapshot_gap_seconds")
    )
    gap_limit = _nonnegative_number(
        coverage.get("max_snapshot_gap_limit_seconds")
    )
    if max_gap is None or gap_limit is None or max_gap > gap_limit:
        issues.append("evidence coverage maximum snapshot gap exceeds its policy limit")

    return {
        "required": True,
        "status": "passed" if not any("evidence coverage" in issue or "evidence_coverage.json" in issue for issue in issues) else "failed",
        "raw_frame_count": raw_frame_count or 0,
        "selected_snapshot_count": selected_snapshot_count or 0,
        "max_selected_snapshot_gap_seconds": max_gap,
        "resolved_inventory_refs": 0,
    }


def _validate_inventory_evidence_refs(
    job_dir: Path,
    inventory_source_refs: dict[str, list[str]],
    *,
    required_ids: set[str],
    evidence_coverage: dict[str, Any],
    issues: list[str],
) -> dict[str, int]:
    transcript = _read_optional_object(job_dir / "transcript.json")
    transcript_ranges: list[tuple[float, float]] = []
    for segment in transcript.get("segments", []):
        if not isinstance(segment, dict):
            continue
        start = _number(segment.get("start"))
        end = _number(segment.get("end"))
        if start is not None and end is not None and end >= start:
            transcript_ranges.append((start, end))

    screen_text = _read_optional_object(job_dir / "screen_text.json")
    ocr_timestamps = {
        int(value)
        for frame in screen_text.get("frames", [])
        if isinstance(frame, dict)
        and (value := _number(frame.get("timestamp_seconds"))) is not None
    }
    snapshot_timestamps: set[int] = set()
    snapshot_names: set[str] = set()
    snapshot_ids: set[str] = set()
    for frame in evidence_coverage.get("frames", []):
        if not isinstance(frame, dict) or frame.get("selected") is not True:
            continue
        timestamp = _number(frame.get("timestamp_seconds"))
        if timestamp is not None:
            snapshot_timestamps.add(int(timestamp))
        snapshot = _nonempty_string(frame.get("snapshot"))
        if snapshot:
            snapshot_names.add(Path(snapshot).name)
        snapshot_id = _nonempty_string(frame.get("snapshot_evidence_id"))
        if snapshot_id:
            snapshot_ids.add(snapshot_id)

    resolved_count = 0
    checked_count = 0
    visual_only_items = 0
    for item_id, refs in inventory_source_refs.items():
        recognized = 0
        resolved = 0
        has_stt = False
        has_snapshot = False
        has_visual = False
        for ref in refs:
            kind = _evidence_ref_kind(ref)
            if kind is None:
                continue
            recognized += 1
            checked_count += 1
            timestamps = [_timestamp_seconds(value) for value in TIMESTAMP_RE.findall(ref)]
            timestamps = [value for value in timestamps if value is not None]
            valid = False
            if kind == "stt":
                has_stt = True
                valid = _stt_ref_resolves(timestamps, transcript_ranges)
            elif kind == "ocr":
                has_visual = True
                valid = _timestamp_ref_resolves(timestamps, ocr_timestamps)
            else:
                has_visual = True
                valid = _snapshot_ref_resolves(
                    ref,
                    timestamps,
                    snapshot_timestamps,
                    snapshot_names,
                    snapshot_ids,
                )
                has_snapshot = has_snapshot or valid
            if valid:
                resolved += 1
                resolved_count += 1
            else:
                issues.append(
                    f"inventory item {item_id} evidence ref does not resolve: {ref}"
                )
        if item_id in required_ids and (recognized == 0 or resolved == 0):
            issues.append(
                f"required inventory item {item_id} has no resolvable local evidence ref"
            )
        if item_id in required_ids and has_visual and not has_stt:
            visual_only_items += 1
            if not has_snapshot:
                issues.append(
                    f"visual-only inventory item {item_id} requires a Snapshot ref"
                )
    return {
        "checked_inventory_refs": checked_count,
        "resolved_inventory_refs": resolved_count,
        "visual_only_inventory_items": visual_only_items,
    }


TIMESTAMP_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}:\d{2})(?!\d)")


def _evidence_ref_kind(ref: str) -> str | None:
    lowered = ref.lower()
    if "snapshot" in lowered or re.search(r"snapshot_\d+", lowered):
        return "snapshot"
    if re.search(r"(?:^|[^a-z])ocr(?:[^a-z]|$)", lowered):
        return "ocr"
    if re.search(r"(?:^|[^a-z])stt(?:[^a-z]|$)", lowered):
        return "stt"
    return None


def _stt_ref_resolves(
    timestamps: list[int],
    transcript_ranges: list[tuple[float, float]],
) -> bool:
    if not timestamps or not transcript_ranges:
        return False
    start = float(timestamps[0])
    end = float(timestamps[-1])
    return any(segment_start <= end and segment_end >= start for segment_start, segment_end in transcript_ranges)


def _timestamp_ref_resolves(timestamps: list[int], available: set[int]) -> bool:
    return bool(timestamps) and all(timestamp in available for timestamp in timestamps)


def _snapshot_ref_resolves(
    ref: str,
    timestamps: list[int],
    available_timestamps: set[int],
    available_names: set[str],
    available_ids: set[str],
) -> bool:
    if any(name in ref for name in available_names):
        return True
    if any(evidence_id in ref for evidence_id in available_ids):
        return True
    return _timestamp_ref_resolves(timestamps, available_timestamps)


def _timestamp_seconds(value: str) -> int | None:
    try:
        hours, minutes, seconds = (int(part) for part in value.split(":"))
    except (TypeError, ValueError):
        return None
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _safe_job_evidence_path(job_dir: Path, value: Any) -> Path | None:
    raw = _nonempty_string(value)
    if not raw:
        return None
    path = Path(raw).expanduser()
    resolved = (path if path.is_absolute() else job_dir / path).resolve()
    try:
        resolved.relative_to(job_dir.resolve())
    except ValueError:
        return None
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_optional_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _nonnegative_number(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number >= 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


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
    checked_date: str | None = None
    if not checked_at:
        issues.append("official_sources.json checked_at is required")
    else:
        try:
            checked_datetime = datetime.fromisoformat(
                checked_at.replace("Z", "+00:00")
            )
            if checked_datetime.tzinfo is None or checked_datetime.utcoffset() is None:
                issues.append(
                    "official_sources.json checked_at must include a timezone offset"
                )
            else:
                now = datetime.now(checked_datetime.tzinfo)
                if checked_datetime > now + timedelta(minutes=5):
                    issues.append(
                        "official_sources.json checked_at cannot be in the future"
                    )
            checked_date = checked_datetime.date().isoformat()
        except ValueError:
            issues.append("official_sources.json checked_at must be ISO 8601")

    claims = official.get("claims")
    if not isinstance(claims, list):
        issues.append("official_sources.json claims must be a list")
        claims = []
    appendix_heading = _nonempty_string(official.get("appendix_heading"))
    appendix_start = len(minutes_text)
    appendix_text = ""
    canonical_external_headings = set(
        TRUST_SECTION_HEADINGS["external_evidence"].values()
    )
    appendix_language: str | None = None
    if not appendix_heading:
        issues.append("official_sources.json appendix_heading is required")
    else:
        if appendix_heading not in canonical_external_headings:
            issues.append(
                "official_sources.json appendix_heading must use the canonical "
                "external-evidence heading"
            )
        else:
            appendix_language = next(
                language
                for language, heading in TRUST_SECTION_HEADINGS[
                    "external_evidence"
                ].items()
                if heading == appendix_heading
            )
        appendix_start, appendix_text = _extract_final_appendix(
            minutes_text,
            appendix_heading,
            issues,
        )
    if checked_date and checked_date not in appendix_text:
        issues.append(
            "the final official-evidence appendix must display the checked_at date"
        )
    if appendix_language:
        for disclosure in TRUST_APPENDIX_DISCLOSURES[appendix_language]:
            if disclosure not in appendix_text:
                issues.append(
                    "the final official-evidence appendix is missing a required "
                    "recording/privacy disclosure"
                )

    if status == "not_applicable":
        if official_ids:
            issues.append(
                "official verification is marked not_applicable despite required items"
            )
        reason = _nonempty_string(official.get("reason"))
        if not reason:
            issues.append("official_sources.json reason is required when not_applicable")
        elif reason not in appendix_text:
            issues.append(
                "the not_applicable official verification reason must appear in the "
                "final appendix"
            )
        if claims:
            issues.append(
                "official_sources.json claims must be empty when status=not_applicable"
            )
        return status

    if not claims and not official_ids:
        issues.append(
            "official_sources.json must use status=not_applicable when no claims apply"
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
