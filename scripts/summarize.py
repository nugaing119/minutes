from __future__ import annotations

import json
from typing import Any

from scripts.config import Settings
from scripts.llm import get_provider, speaker_identity_instruction


MAX_CHARS_PER_CHUNK = 24_000


def generate_minutes(
    transcript_text: str,
    settings: Settings,
    screen_text: str = "",
) -> dict[str, Any]:
    provider = get_provider(settings)
    source_text = build_minutes_source(transcript_text, screen_text, settings)
    chunks = split_text(source_text, MAX_CHARS_PER_CHUNK)
    if len(chunks) == 1:
        return provider.generate_minutes_json(chunks[0])

    partials = []
    for index, chunk in enumerate(chunks, start=1):
        partials.append(
            provider.generate_minutes_json(
                f"[부분 전사 {index}/{len(chunks)}]\n\n{chunk}",
                output_language="auto",
            )
        )

    merged_input = "\n\n".join(
        f"[부분 분석 {index}]\n{json.dumps(partial, ensure_ascii=False, indent=2)}"
        for index, partial in enumerate(partials, start=1)
    )
    return provider.generate_minutes_json(
        "다음은 긴 영상의 원문 언어를 유지해 만든 부분 분석입니다. "
        "중복을 제거하고, 이 최종 단계에서만 설정된 목표 언어로 전체 "
        "내용에 맞는 문서 하나를 직접 작성하세요.\n\n"
        f"{merged_input}",
        output_language=settings.output_language,
    )


def split_text(text: str, max_chars: int) -> list[str]:
    clean = text.strip()
    if len(clean) <= max_chars:
        return [clean]

    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + max_chars, len(clean))
        if end < len(clean):
            boundary = clean.rfind("\n", start, end)
            if boundary <= start:
                boundary = clean.rfind(". ", start, end)
            if boundary > start:
                end = boundary + 1
        chunks.append(clean[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def build_minutes_source(
    transcript_text: str,
    screen_text: str,
    settings: Settings,
) -> str:
    speaker_policy = speaker_identity_instruction(
        transcript_text,
        screen_text,
        getattr(settings, "speaker_attribution_mode", "off"),
    )
    source = (
        "[Speaker identity evidence policy / 화자 식별 근거 정책]\n"
        f"{speaker_policy}\n\n"
        f"[Audio transcript / 음성 전사]\n{transcript_text.strip()}"
    )
    clean_screen_text = screen_text.strip()
    if clean_screen_text:
        if len(clean_screen_text) > settings.ocr_max_context_chars:
            clean_screen_text = clean_screen_text[: settings.ocr_max_context_chars]
        source += (
            "\n\n[Screen OCR evidence / 화면 OCR 근거]\n"
            "아래 내용은 영상 화면에서 추출한 텍스트입니다. "
            "음성 전사를 보완하는 근거로만 사용하고, 불확실한 OCR 오인식은 단정하지 마세요.\n"
            f"{clean_screen_text}"
        )
    return source


def render_markdown(
    minutes: dict[str, Any],
    requested_output_language: str = "ko",
) -> str:
    if isinstance(minutes.get("sections"), list):
        return _render_content_document(minutes, requested_output_language)
    return _render_legacy_minutes(minutes, requested_output_language)


def _render_content_document(
    document: dict[str, Any],
    requested_output_language: str,
) -> str:
    language = _resolved_output_language(document, requested_output_language)
    title = str(document.get("document_title", "")).strip()
    if not title:
        title = "Content Analysis" if language == "en" else "콘텐츠 분석"
    document_type = str(document.get("document_type", "")).strip()
    type_label = "Document type" if language == "en" else "문서 유형"
    lines = [f"# {title}", ""]
    if document_type:
        lines.extend([f"{type_label}: {document_type}", ""])

    sections = document.get("sections", [])
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading", "")).strip()
        if not heading:
            continue
        lines.extend([f"## {heading}", ""])
        summary = str(section.get("summary", "")).strip()
        if summary:
            lines.extend([summary, ""])
        points = section.get("points", [])
        if isinstance(points, list):
            clean_points = [str(point).strip() for point in points if str(point).strip()]
            if clean_points:
                lines.extend(f"- {point}" for point in clean_points)
                lines.append("")
        table = section.get("table", {})
        if isinstance(table, dict):
            headers = [str(value).strip() for value in table.get("headers", [])]
            rows = table.get("rows", [])
            if headers and isinstance(rows, list):
                lines.append("| " + " | ".join(_cell(value) for value in headers) + " |")
                lines.append("|" + "|".join("---" for _ in headers) + "|")
                for row in rows:
                    cells = row.get("cells", []) if isinstance(row, dict) else []
                    normalized = [
                        _cell(cells[index]) if index < len(cells) else ""
                        for index in range(len(headers))
                    ]
                    lines.append("| " + " | ".join(normalized) + " |")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_legacy_minutes(
    minutes: dict[str, Any],
    requested_output_language: str,
) -> str:
    language = _resolved_output_language(minutes, requested_output_language)
    labels = _markdown_labels(language)
    lines = [f"# {labels['title']}", ""]

    lines.extend([f"## 1. {labels['summary']}"])
    lines.extend(
        _bullet_lines(minutes.get("summary", []), fallback=labels["none"])
    )
    lines.append("")

    lines.extend([f"## 2. {labels['decisions']}"])
    lines.extend(
        _bullet_lines(minutes.get("decisions", []), fallback=labels["none"])
    )
    lines.append("")

    lines.extend([f"## 3. {labels['actions']}"])
    lines.extend(
        [
            "| "
            + " | ".join(
                (
                    labels["owner"],
                    labels["task"],
                    labels["due"],
                    labels["evidence"],
                )
            )
            + " |",
            "|---|---|---|---|",
        ]
    )
    for item in minutes.get("action_items", []):
        owner = _cell(item.get("owner", labels["tbd"]))
        task = _cell(item.get("task", ""))
        due = _cell(item.get("due", labels["tbd"]))
        evidence = _cell(item.get("evidence", ""))
        lines.append(f"| {owner} | {task} | {due} | {evidence} |")
    lines.append("")

    lines.extend([f"## 4. {labels['discussion']}"])
    discussion = minutes.get("discussion", [])
    if not discussion:
        lines.append(f"- {labels['no_discussion']}")
    for section in discussion:
        lines.append(f"### {section.get('topic', labels['topic_tbd'])}")
        lines.extend(
            _bullet_lines(
                section.get("details", []),
                fallback=labels["no_discussion"],
            )
        )
        issues = section.get("issues", [])
        if issues:
            lines.append(f"- {labels['issues']}")
            lines.extend(f"  - {issue}" for issue in issues)
        conclusion = section.get("conclusion")
        if conclusion:
            lines.append(f"- {labels['conclusion']}: {conclusion}")
        lines.append("")

    lines.extend([f"## 5. {labels['open_questions']}"])
    lines.extend(
        _bullet_lines(minutes.get("open_questions", []), fallback=labels["none"])
    )
    lines.append("")

    return "\n".join(lines)


def _bullet_lines(values: list[str], fallback: str = "없음") -> list[str]:
    if not values:
        return [f"- {fallback}"]
    return [f"- {value}" for value in values]


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _resolved_output_language(
    minutes: dict[str, Any],
    requested_output_language: str,
) -> str:
    generated = str(minutes.get("output_language", "")).lower()
    if generated in {"en", "ko"}:
        return generated
    if requested_output_language in {"en", "ko"}:
        return requested_output_language
    return "ko"


def _markdown_labels(language: str) -> dict[str, str]:
    if language == "en":
        return {
            "title": "Content Analysis",
            "summary": "Meeting Summary",
            "decisions": "Key Decisions",
            "actions": "Action Items",
            "owner": "Owner",
            "task": "Action",
            "due": "Due",
            "evidence": "Evidence",
            "discussion": "Discussion Details",
            "issues": "Issues",
            "conclusion": "Conclusion",
            "open_questions": "Open Questions",
            "none": "None",
            "tbd": "TBD",
            "topic_tbd": "Topic TBD",
            "no_discussion": "No discussion details were identified.",
        }
    return {
        "title": "콘텐츠 분석",
        "summary": "회의 요약",
        "decisions": "주요 결정사항",
        "actions": "액션 아이템",
        "owner": "담당자",
        "task": "할 일",
        "due": "기한",
        "evidence": "근거",
        "discussion": "논의 상세",
        "issues": "쟁점",
        "conclusion": "결론",
        "open_questions": "확인 필요 사항",
        "none": "없음",
        "tbd": "미정",
        "topic_tbd": "주제 미정",
        "no_discussion": "정리된 논의 상세가 없습니다.",
    }
