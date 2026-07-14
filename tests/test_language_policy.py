from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.llm import DOCUMENT_SCHEMA, build_system_prompt, language_instruction
from scripts.process_file import build_codex_minutes_input
from scripts.summarize import MAX_CHARS_PER_CHUNK, generate_minutes, render_markdown


class FakeProvider:
    def __init__(self) -> None:
        self.languages: list[str | None] = []

    def generate_minutes_json(
        self,
        transcript: str,
        *,
        output_language: str | None = None,
    ) -> dict:
        self.languages.append(output_language)
        language = "en" if output_language == "auto" else (output_language or "ko")
        return {
            "output_language": language,
            "document_title": "Test analysis",
            "document_type": "Technical briefing",
            "sections": [
                {
                    "heading": "Architecture",
                    "summary": "",
                    "points": [],
                    "table": {"headers": [], "rows": []},
                }
            ],
        }


class LanguagePolicyTests(unittest.TestCase):
    def test_korean_is_explicit_only_at_requested_final_output(self) -> None:
        instruction = language_instruction("ko")
        self.assertIn("최종 문서", instruction)
        self.assertIn("한국어", instruction)
        self.assertIn("원문 언어를 유지", build_system_prompt("auto"))

    def test_codex_prompt_preserves_english_evidence_and_requests_korean(self) -> None:
        prompt = build_codex_minutes_input(
            "We approved the release plan.",
            "Release readiness",
            output_language="ko",
            detected_language="en",
        )
        self.assertIn("STT 감지 언어: en", prompt)
        self.assertIn("한국어", prompt)
        self.assertIn("We approved the release plan.", prompt)
        self.assertIn("Release readiness", prompt)
        self.assertIn("먼저 번역한 뒤 다시 요약하지 마세요", prompt)
        self.assertNotIn("# 회의록", prompt)
        self.assertNotIn("## 1. 회의 요약", prompt)
        self.assertIn("내용에 맞는 문서 제목", prompt)

    def test_auto_codex_prompt_does_not_force_korean(self) -> None:
        prompt = build_codex_minutes_input(
            "English transcript",
            "",
            output_language="auto",
            detected_language="en",
        )
        self.assertIn("영어와 output_language=en", prompt)
        self.assertNotIn("모든 회의록 내용은 한국어", prompt)

    def test_strict_codex_prompt_requires_lossless_audit_without_length_cap(self) -> None:
        prompt = build_codex_minutes_input(
            "The range is one to eight and the example is anecdotal.",
            "No service will be available for new tenancies.",
            output_language="ko",
            detected_language="en",
            content_audit_mode="strict",
            official_source_verification="auto",
        )

        self.assertIn("하드 상한이 없습니다", prompt)
        self.assertIn("content_inventory.json", prompt)
        self.assertIn("content_audit.json", prompt)
        self.assertIn("official_sources.json", prompt)
        self.assertIn("raw_transcript_or_ocr_sent", prompt)
        self.assertIn("예시·anecdotal 수치를 권장값", prompt)
        self.assertIn("누적 inventory", prompt)
        self.assertIn("다시 요약하지", prompt)
        self.assertIn("영상에서 실제로 전달된 내용", prompt)
        self.assertIn("전사·OCR 보강 근거", prompt)
        self.assertIn("영상 내용과 상충하는 근거", prompt)
        self.assertNotIn("3~8개", prompt)

    def test_evidence_codex_prompt_resolves_only_from_timed_evidence(self) -> None:
        prompt = build_codex_minutes_input(
            "[00:00:00 - 00:00:10] 화자 1: Welcome.\n"
            "[00:00:11 - 00:00:20] 화자 1: I will continue.",
            "[00:00:00]\nAlex Kim\n\n[00:00:11]\nJordan Lee",
            output_language="en",
            detected_language="en",
            speaker_attribution_mode="evidence",
            snapshot_evidence_available=True,
        )
        self.assertIn("화자 식별 모드: evidence", prompt)
        self.assertIn("로컬 음성 화자분리", prompt)
        self.assertIn("정책상 실행되지 않았습니다", prompt)
        self.assertIn("화면 근거가 없거나 약해도", prompt)
        self.assertIn("모든 문장에 화자를 붙이지 마세요", prompt)
        self.assertIn("참가자 목록", prompt)
        self.assertIn("화면을 공유한 사람", prompt)
        self.assertIn("서비스나 화면 UI 형태를 가정하지 마세요", prompt)
        self.assertIn("같은 job 폴더의 snapshots/", prompt)
        self.assertIn("Snapshot만 선택적으로 직접 확인", prompt)
        self.assertNotIn("Zoom", prompt)
        self.assertNotIn("초록색", prompt)

    def test_generic_json_renders_content_specific_headings(self) -> None:
        markdown = render_markdown(
            {
                "output_language": "en",
                "document_title": "HeatWave concurrency architecture",
                "document_type": "Technical session analysis",
                "sections": [
                    {
                        "heading": "DASK worker limits",
                        "summary": "Workers are shared.",
                        "points": ["Four workers per node"],
                        "table": {"headers": [], "rows": []},
                    }
                ],
            },
            "auto",
        )
        self.assertIn("# HeatWave concurrency architecture", markdown)
        self.assertIn("## DASK worker limits", markdown)
        self.assertIn("Document type: Technical session analysis", markdown)
        self.assertNotIn("# 회의록", markdown)

    def test_document_schema_is_content_driven(self) -> None:
        required = set(DOCUMENT_SCHEMA["required"])
        self.assertEqual(
            required,
            {"output_language", "document_title", "document_type", "sections"},
        )
        prompt = build_system_prompt("ko")
        self.assertIn("영상의 실제 성격", prompt)
        self.assertIn("고정된 회의록 항목", prompt)
        self.assertIn("화자별 역할", prompt)
        self.assertIn("하드 상한", prompt)
        self.assertNotIn("3~8", prompt)

    def test_long_meeting_keeps_partial_summaries_in_source_language(self) -> None:
        provider = FakeProvider()
        settings = SimpleNamespace(
            output_language="ko",
            ocr_max_context_chars=12_000,
        )
        transcript = "English source sentence.\n" * (MAX_CHARS_PER_CHUNK // 10)
        with patch("scripts.summarize.get_provider", return_value=provider):
            generate_minutes(transcript, settings)
        self.assertGreater(len(provider.languages), 1)
        self.assertTrue(all(value == "auto" for value in provider.languages[:-1]))
        self.assertEqual(provider.languages[-1], "ko")


if __name__ == "__main__":
    unittest.main()
