from __future__ import annotations

from typing import Any, Mapping


LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "ko": "ko",
    "kor": "ko",
    "korean": "ko",
}


def normalize_language(value: object) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return "unknown"
    base = raw.split("-", 1)[0]
    return LANGUAGE_ALIASES.get(raw, LANGUAGE_ALIASES.get(base, "unknown"))


def translation_required(output_language: str, detected_language: str) -> bool:
    target = normalize_language(output_language)
    source = normalize_language(detected_language)
    return target in {"en", "ko"} and source in {"en", "ko"} and target != source


def content_output_language(output_language: str, detected_language: str) -> str:
    return "auto" if translation_required(output_language, detected_language) else output_language


def language_policy_from_status(status: Mapping[str, Any]) -> dict[str, Any]:
    handoff = status.get("codex_handoff", {})
    if not isinstance(handoff, Mapping):
        handoff = {}
    output_language = str(handoff.get("output_language", "auto"))
    detected_language = str(handoff.get("detected_language", "unknown"))
    return {
        "output_language": output_language,
        "detected_language": detected_language,
        "content_output_language": content_output_language(
            output_language,
            detected_language,
        ),
        "translation_required": translation_required(
            output_language,
            detected_language,
        ),
    }


def target_language_label(language: str) -> str:
    normalized = normalize_language(language)
    if normalized == "ko":
        return "Korean"
    if normalized == "en":
        return "English"
    raise ValueError(f"unsupported translation target language: {language}")
