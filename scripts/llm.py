from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from scripts.config import Settings


DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "output_language": {"type": "string", "enum": ["ko", "en"]},
        "document_title": {"type": "string"},
        "document_type": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "summary": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                    "table": {
                        "type": "object",
                        "properties": {
                            "headers": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "rows": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "cells": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        }
                                    },
                                    "required": ["cells"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["headers", "rows"],
                        "additionalProperties": False,
                    },
                },
                "required": ["heading", "summary", "points", "table"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "output_language",
        "document_title",
        "document_type",
        "sections",
    ],
    "additionalProperties": False,
}

# Backwards-compatible import name for integrations that referenced the old symbol.
MINUTES_SCHEMA = DOCUMENT_SCHEMA


def speaker_identity_instruction(
    transcript_text: str,
    screen_text: str,
    speaker_attribution_mode: str,
    *,
    snapshot_evidence_available: bool = False,
) -> str:
    if speaker_attribution_mode == "evidence":
        evidence_note = (
            "시간이 표시된 화면 OCR 근거가 함께 제공됩니다."
            if screen_text.strip()
            else "화면 OCR 근거가 없습니다. 명시적인 자기소개·직접 호명과 응답·발언 "
            "인계 같은 STT 근거가 없다면 실제 이름을 추측하지 마세요."
        )
        instructions = [
            "화자 식별 모드: evidence. 오디오에서 만든 시간 구간별 STT와 OCR·선별 "
            "Snapshot만 근거로 사용하고, 서비스나 화면 UI 형태를 가정하지 마세요.",
            "로컬 음성 화자분리(ECAPA/pyannote 군집화)는 정책상 실행되지 않았습니다. "
            "화면 근거가 없거나 약해도 로컬 화자분리, 화자 수 강제 지정, 음성 재분석, "
            "영상 전체 재추출·재스캔을 실행하거나 요구하지 마세요.",
            evidence_note,
        ]
        if snapshot_evidence_available:
            instructions.append(
                "같은 job 폴더의 snapshots/가 제공됩니다. OCR·STT가 충돌하거나 "
                "화자 전환을 확인해야 하는 시점의 Snapshot만 선택적으로 직접 확인하고, "
                "화면 구조 자체도 특정 서비스 규칙 없이 해석하세요."
            )
        instructions.extend(
            (
                "최종 문서를 쓰기 전에 제공된 근거만으로 필요한 구간의 화자 판정을 "
                "내부적으로 수행하세요. 모든 문장에 화자를 붙이지 마세요.",
                "- 이름이 명시된 자막·이름표, 자기소개, 직접 호명 뒤의 응답, 발언 인계, "
                "여러 시점에서 반복되는 일관된 OCR·STT를 시간 기준으로 교차 확인하세요.",
                "- 참가자 목록에 이름이 보이는 것, 화면을 공유한 사람, 화면에 이름이 잠깐 "
                "등장한 사실만으로 그 구간의 발언자라고 단정하지 마세요.",
                "- 특정 서비스, 색상, 테두리 또는 고정 화면 배치를 전제로 사용하지 마세요. "
                "실제 자료에 존재하는 근거만 사용하세요.",
                "- 근거가 강하고 서로 모순되지 않는 구간만 실제 이름으로 표기하세요. "
                "근거가 약하거나 충돌하면 화자 미상으로 유지하고, 이름이나 인물 수를 "
                "추측해 사실처럼 쓰지 마세요.",
                "- 화자 식별이 불확실해도 본문 내용은 STT·OCR 근거대로 보존하세요. 화자명을 "
                "정하지 못했다는 이유로 발언 내용 자체를 생략하지 마세요.",
            )
        )
        return "\n".join(instructions)
    return (
        "화자 식별은 비활성화되어 있습니다. 인물 수나 실제 신원을 추측하지 말고, "
        "명시적인 원문 근거 없이 화자명을 만들지 마세요."
    )


def language_instruction(output_language: str) -> str:
    if output_language == "ko":
        return (
            "원문 언어와 관계없이 최종 문서의 설명 문장은 한국어로 작성하고 "
            "output_language는 ko로 반환합니다. 제품명, 회사명, 사람 이름, API, "
            "코드, 명령어처럼 번역하면 의미가 손상되는 고유 표현은 원문을 유지합니다."
        )
    if output_language == "en":
        return (
            "Write the final document in English and return output_language as en. "
            "Keep product names, company names, people, APIs, code, and commands "
            "in their original form."
        )
    return (
        "최종 문서는 음성 전사의 지배적인 원문 언어를 유지하고 불필요하게 "
        "번역하지 않습니다. 영어 원문이면 영어와 output_language=en, 한국어 "
        "원문이면 한국어와 output_language=ko를 사용합니다."
    )


def build_system_prompt(output_language: str) -> str:
    return f"""당신은 영상과 음성의 실제 내용을 분류하고 구조화하는 전문 콘텐츠 분석가입니다.

최종 언어 정책:
- {language_instruction(output_language)}
- 전사와 OCR을 먼저 다른 언어의 중간 문서로 바꾼 뒤 재번역하지 말고, 원문 근거에서 목표 언어의 최종 문서를 직접 작성합니다.

규칙:
- 최종 출력은 반드시 JSON 하나만 반환합니다.
- 영상의 실제 성격을 강의, 기술 발표, 웨비나, 인터뷰, 토론, 업무 협의, 데모, 교육 자료 등에서 판단해 document_type을 정합니다. 플랫폼이나 파일명만으로 유형을 단정하지 않습니다.
- document_title은 영상의 핵심 내용을 대표하는 간결한 제목으로 작성하며 '회의록', '영상 요약' 같은 포괄적 이름을 제목으로 사용하지 않습니다.
- 고정된 회의록 항목을 강요하지 않습니다. section 수나 최종 길이에 하드 상한을 두지 말고, 필수 근거를 정확히 보존하는 데 필요한 만큼 구성합니다. 결정사항·액션 아이템·질문 같은 항목은 실제 근거가 있고 해당 영상에 적합할 때만 포함합니다.
- 실제 회의라면 대화를 받아쓰지 말고 안건·핵심 논의·결정/합의·담당자/기한·후속조치·미해결 항목·위험을 중심으로 간결하고 객관적으로 정리합니다. 한국어 회의 문장은 `~함`, `~하기로 함`, `~예정임`, `~필요함` 계열의 보고체를 일관되게 사용합니다.
- 회의가 아닌 기술 분석, 데모, 교육, 브리핑 문서는 실제 문서 유형에 맞는 전문 문체를 사용하며 회의록 종결형을 강제하지 않습니다.
- 최종 독자 문서에는 STT/OCR/Snapshot 원시 참조, skill/model/token, 전처리·렌더·QA 기록, 내부 파일명·경로·해시를 쓰지 않습니다. 이런 추적 정보는 내부 산출물에만 남깁니다.
- 각 section의 summary는 간결하게 쓸 수 있지만 points에는 필요한 정책·수치·조건·예외·제한·질의응답을 길이 때문에 생략하지 않습니다. 필요한 경우에만 비교 가능한 table을 사용하고, 표가 불필요하면 headers와 rows를 빈 배열로 둡니다.
- STT 오류는 문맥상 자연스럽게 보정하되, 전사에 없는 내용을 만들지 않습니다.
- OCR은 음성 전사를 보완하는 근거이며 불확실한 오인식을 단정하지 않습니다.
- 전사에 의미 있는 화자 라벨이 둘 이상 있으면 화자별 역할과 핵심 기여가 드러나는 section을 포함합니다. 실제 이름은 원문에서 명시적으로 확인될 때만 사용하고, 그렇지 않으면 화자 라벨과 내용 기반 역할만 표기합니다.
- 짧은 감탄사나 경계 오분류로 보이는 소수 화자 조각을 별도 인물로 과장하지 않습니다.
- 발표된 정책, 발표자의 권고, 확정된 결정, 추정 또는 추가 확인이 필요한 내용을 구분합니다.
"""


SYSTEM_PROMPT = build_system_prompt("ko")


def build_user_prompt(transcript: str, output_language: str = "ko") -> str:
    return f"""다음은 원문 언어를 유지한 영상 음성 전사와 선택적 OCR 근거입니다.

최종 언어 정책:
{language_instruction(output_language)}

원문 근거:
{transcript}

위 원문 근거를 바탕으로 영상 성격에 맞는 목표 언어의 분석 문서 JSON을 직접 작성하세요."""


class LlmProvider(ABC):
    @abstractmethod
    def generate_minutes_json(
        self,
        transcript: str,
        *,
        output_language: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


class OpenAiProvider(LlmProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if not settings.openai_model:
            raise RuntimeError("OPENAI_MODEL is required when LLM_PROVIDER=openai")
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.output_language = settings.output_language

    def generate_minutes_json(
        self,
        transcript: str,
        *,
        output_language: str | None = None,
    ) -> dict[str, Any]:
        target_language = output_language or self.output_language
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": build_system_prompt(target_language),
                },
                {
                    "role": "user",
                    "content": build_user_prompt(transcript, target_language),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "content_analysis",
                    "schema": DOCUMENT_SCHEMA,
                    "strict": True,
                },
            },
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenAI returned an empty response")
        return json.loads(content)


class OciGenAiProvider(LlmProvider):
    def __init__(self, settings: Settings) -> None:
        required = {
            "OCI_GENAI_MODEL": settings.oci_genai_model,
            "OCI_GENAI_COMPARTMENT_ID": settings.oci_genai_compartment_id,
            "OCI_GENAI_ENDPOINT": settings.oci_genai_endpoint,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} required when LLM_PROVIDER=oci"
            )
        import oci

        self.oci = oci
        self.model = settings.oci_genai_model
        self.output_language = settings.output_language
        self.compartment_id = settings.oci_genai_compartment_id
        config = oci.config.from_file(
            file_location=str(settings.oci_config_file),
            profile_name=settings.oci_profile,
        )
        self.client = oci.generative_ai_inference.GenerativeAiInferenceClient(
            config,
            service_endpoint=settings.oci_genai_endpoint,
        )

    def generate_minutes_json(
        self,
        transcript: str,
        *,
        output_language: str | None = None,
    ) -> dict[str, Any]:
        oci = self.oci
        target_language = output_language or self.output_language
        prompt = (
            f"{build_system_prompt(target_language)}\n\n"
            f"{build_user_prompt(transcript, target_language)}"
        )
        chat_response = self.client.chat(
            chat_details=oci.generative_ai_inference.models.ChatDetails(
                compartment_id=self.compartment_id,
                serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(
                    serving_type="ON_DEMAND",
                    model_id=self.model,
                ),
                chat_request=oci.generative_ai_inference.models.CohereChatRequest(
                    api_format="COHERE",
                    message=prompt,
                    response_format=oci.generative_ai_inference.models.CohereResponseJsonFormat(
                        type="JSON_OBJECT",
                        schema=json.dumps(DOCUMENT_SCHEMA, ensure_ascii=False),
                    ),
                    is_stream=False,
                    temperature=0,
                ),
            )
        )
        text = _extract_oci_text(chat_response)
        if not text:
            raise RuntimeError("OCI GenAI returned an empty response")
        return json.loads(text)


def get_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "openai":
        return OpenAiProvider(settings)
    if settings.llm_provider == "oci":
        return OciGenAiProvider(settings)
    raise RuntimeError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")


def _extract_oci_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    for attr in ("data", "chat_response", "text", "message", "content"):
        if hasattr(value, attr):
            found = _extract_oci_text(getattr(value, attr))
            if found:
                return found
    if isinstance(value, dict):
        for key in ("text", "message", "content", "chat_response", "data"):
            if key in value:
                found = _extract_oci_text(value[key])
                if found:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _extract_oci_text(item)
            if found:
                return found
    if hasattr(value, "to_dict"):
        return _extract_oci_text(value.to_dict())
    return None
