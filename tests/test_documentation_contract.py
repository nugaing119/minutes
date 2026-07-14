from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = (REPO_ROOT / "PLAN.md").read_text(encoding="utf-8")
        cls.skill_agent_metadata = (
            REPO_ROOT / "codex/skills/minutes/agents/openai.yaml"
        ).read_text(encoding="utf-8")
        cls.primary_docs = {
            relative_path: (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            for relative_path in (
                "README.md",
                "INSTALL_USAGE.md",
                "PLAN.md",
                "codex/skills/minutes/SKILL.md",
            )
        }

    def test_primary_docs_share_the_current_processing_contract(self) -> None:
        required_contract = (
            "OUTPUT_LANGUAGE=auto",
            "SPEAKER_ATTRIBUTION_MODE=evidence",
            "SPEAKER_ATTRIBUTION_REQUIRED=false",
            "SPEECH_ACTIVITY_VALIDATION_ENABLED=true",
            "COMPLETED_JOB_RETENTION_HOURS",
            "OCR_WORKERS",
            "OCR_TESSERACT_THREAD_LIMIT=1",
        )

        for relative_path, content in self.primary_docs.items():
            with self.subTest(path=relative_path):
                for phrase in required_contract:
                    self.assertIn(phrase, content)

    def test_plan_does_not_restore_superseded_behavior(self) -> None:
        superseded_statements = (
            "회의록은 반드시 한국어로 작성한다.",
            "회의록 출력 언어는 한국어로 고정한다.",
            "초기 구현에서는 원본 영상을 이동하지 않고 복사한다.",
            "최종 output 폴더에 screen_text.json/txt 복사",
        )

        for statement in superseded_statements:
            with self.subTest(statement=statement):
                self.assertNotIn(statement, self.plan)

    def test_plan_records_current_local_quality_and_resource_profile(self) -> None:
        for phrase in (
            "CONTENT_AUDIT_MODE=strict",
            "OFFICIAL_SOURCE_VERIFICATION=auto",
            "OCR_WORKERS=5",
            "local_audio_diarization=disabled_by_policy",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.plan)

    def test_skill_does_not_encode_a_machine_specific_cpu_threshold(self) -> None:
        skill = self.primary_docs["codex/skills/minutes/SKILL.md"]

        self.assertNotIn("70%", skill)
        self.assertNotIn("11-core M3 Pro", skill)
        self.assertIn("do not derive or change it", skill)

    def test_global_skill_requires_explicit_invocation(self) -> None:
        metadata = self.skill_agent_metadata

        self.assertIn("allow_implicit_invocation: false", metadata)
        self.assertIn("$minutes", metadata)
        self.assertNotIn("OBS recording", metadata)
        self.assertNotIn("Korean meeting minutes", metadata)

    def test_install_docs_include_korean_tesseract_language_data(self) -> None:
        for relative_path in ("README.md", "INSTALL_USAGE.md"):
            with self.subTest(path=relative_path):
                self.assertIn(
                    "brew install tesseract-lang",
                    self.primary_docs[relative_path],
                )

    def test_docs_keep_speech_validation_separate_from_speaker_identity(self) -> None:
        security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")

        for relative_path, content in self.primary_docs.items():
            with self.subTest(path=relative_path):
                self.assertIn("validate_speech_activity", content)
                self.assertNotIn("prepare_diarization_model.py", content)
                self.assertNotIn("requirements-diarization.txt", content)
        self.assertIn("발화 존재 검증에만 사용", security)
        self.assertIn("speaker_identity\": False", (REPO_ROOT / "scripts/vad_security.py").read_text(encoding="utf-8"))
        self.assertNotIn("embedding_model.ckpt", security)
        self.assertNotIn("torch.load(", security)


if __name__ == "__main__":
    unittest.main()
