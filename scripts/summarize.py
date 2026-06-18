from __future__ import annotations

import json
from typing import Any

from scripts.config import Settings
from scripts.llm import get_provider


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
                f"[부분 전사 {index}/{len(chunks)}]\n\n{chunk}"
            )
        )

    merged_input = "\n\n".join(
        f"[부분 회의록 {index}]\n{json.dumps(partial, ensure_ascii=False, indent=2)}"
        for index, partial in enumerate(partials, start=1)
    )
    return provider.generate_minutes_json(
        "다음은 긴 회의를 나눠 요약한 부분 회의록입니다. "
        "중복을 제거하고 전체 회의록 하나로 병합하세요.\n\n"
        f"{merged_input}"
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
    source = f"[음성 전사]\n{transcript_text.strip()}"
    clean_screen_text = screen_text.strip()
    if clean_screen_text:
        if len(clean_screen_text) > settings.ocr_max_context_chars:
            clean_screen_text = clean_screen_text[: settings.ocr_max_context_chars]
        source += (
            "\n\n[화면 공유 OCR 텍스트]\n"
            "아래 내용은 회의 화면에서 추출한 텍스트입니다. "
            "음성 전사를 보완하는 근거로만 사용하고, 불확실한 OCR 오인식은 단정하지 마세요.\n"
            f"{clean_screen_text}"
        )
    return source


def render_markdown(minutes: dict[str, Any]) -> str:
    lines = ["# 회의록", ""]

    lines.extend(["## 1. 회의 요약"])
    lines.extend(_bullet_lines(minutes.get("summary", [])))
    lines.append("")

    lines.extend(["## 2. 주요 결정사항"])
    lines.extend(_bullet_lines(minutes.get("decisions", [])))
    lines.append("")

    lines.extend(["## 3. 액션 아이템"])
    lines.extend(["| 담당자 | 할 일 | 기한 | 근거 |", "|---|---|---|---|"])
    for item in minutes.get("action_items", []):
        owner = _cell(item.get("owner", "미정"))
        task = _cell(item.get("task", ""))
        due = _cell(item.get("due", "미정"))
        evidence = _cell(item.get("evidence", ""))
        lines.append(f"| {owner} | {task} | {due} | {evidence} |")
    lines.append("")

    lines.extend(["## 4. 논의 상세"])
    discussion = minutes.get("discussion", [])
    if not discussion:
        lines.append("- 정리된 논의 상세가 없습니다.")
    for section in discussion:
        lines.append(f"### {section.get('topic', '주제 미정')}")
        lines.extend(_bullet_lines(section.get("details", []), fallback="논의 내용 없음"))
        issues = section.get("issues", [])
        if issues:
            lines.append("- 쟁점")
            lines.extend(f"  - {issue}" for issue in issues)
        conclusion = section.get("conclusion")
        if conclusion:
            lines.append(f"- 결론: {conclusion}")
        lines.append("")

    lines.extend(["## 5. 확인 필요 사항"])
    lines.extend(_bullet_lines(minutes.get("open_questions", [])))
    lines.append("")

    return "\n".join(lines)


def _bullet_lines(values: list[str], fallback: str = "없음") -> list[str]:
    if not values:
        return [f"- {fallback}"]
    return [f"- {value}" for value in values]


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()
