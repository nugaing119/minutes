from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from scripts.config import Settings


MINUTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "meeting_title": {"type": "string"},
        "summary": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "task": {"type": "string"},
                    "due": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["owner", "task", "due", "evidence"],
                "additionalProperties": False,
            },
        },
        "discussion": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "details": {"type": "array", "items": {"type": "string"}},
                    "issues": {"type": "array", "items": {"type": "string"}},
                    "conclusion": {"type": "string"},
                },
                "required": ["topic", "details", "issues", "conclusion"],
                "additionalProperties": False,
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "meeting_title",
        "summary",
        "decisions",
        "action_items",
        "discussion",
        "open_questions",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """당신은 회의 전사를 한국어 회의록으로 정리하는 전문 기록자입니다.

규칙:
- 최종 출력은 반드시 JSON 하나만 반환합니다.
- 모든 회의록 내용은 한국어로 작성합니다.
- 영어는 제품명, 회사명, 사람 이름, API 이름, 코드명, 명령어, 원문 의미 보존이 필요한 짧은 인용에만 사용합니다.
- STT 오류는 문맥상 자연스럽게 보정하되, 전사에 없는 내용을 만들지 않습니다.
- 결정된 내용과 논의 중인 내용을 구분합니다.
- 담당자나 기한이 불명확하면 "미정"으로 작성합니다.
- 불확실하거나 추가 확인이 필요한 내용은 open_questions에 넣습니다.
- meeting_title은 회의 내용을 대표하는 한국어 제목으로 3~8어절 정도로 작성합니다.
"""


def build_user_prompt(transcript: str) -> str:
    return f"""다음은 회의 전사입니다.

전사:
{transcript}

위 전사를 바탕으로 회의록 JSON을 작성하세요."""


class LlmProvider(ABC):
    @abstractmethod
    def generate_minutes_json(self, transcript: str) -> dict[str, Any]:
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

    def generate_minutes_json(self, transcript: str) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(transcript)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "meeting_minutes",
                    "schema": MINUTES_SCHEMA,
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
        self.compartment_id = settings.oci_genai_compartment_id
        config = oci.config.from_file(
            file_location=str(settings.oci_config_file),
            profile_name=settings.oci_profile,
        )
        self.client = oci.generative_ai_inference.GenerativeAiInferenceClient(
            config,
            service_endpoint=settings.oci_genai_endpoint,
        )

    def generate_minutes_json(self, transcript: str) -> dict[str, Any]:
        oci = self.oci
        prompt = f"{SYSTEM_PROMPT}\n\n{build_user_prompt(transcript)}"
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
                        schema=json.dumps(MINUTES_SCHEMA, ensure_ascii=False),
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
