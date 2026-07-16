from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agents_guidance = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
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
            "COMPLETED_JOB_RETENTION_HOURS=0",
            "OCR_WORKERS",
            "OCR_TESSERACT_THREAD_LIMIT=1",
            "run_fresh_codex_job.py",
            "codex exec --ephemeral",
            "translation_manifest.json",
            "worker_contract_passed",
            "24KB",
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

    def test_agent_guidance_prevents_combined_skill_and_recursive_job_output(self) -> None:
        for phrase in (
            "read each",
            "`SKILL.md` in a separate tool call",
            "at or below 16 KB",
            "continue until EOF",
            "Never recursively list a job directory",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.agents_guidance)

        minutes_skill = self.primary_docs["codex/skills/minutes/SKILL.md"]
        self.assertIn("in separate\n  tool calls", minutes_skill)
        self.assertIn("continue to EOF before preprocessing", minutes_skill)

    def test_plan_records_current_local_quality_and_resource_profile(self) -> None:
        for phrase in (
            "CONTENT_AUDIT_MODE=strict",
            "OFFICIAL_SOURCE_VERIFICATION=auto",
            "OCR_WORKERS=3",
            "OCR_FRAME_INTERVAL_SECONDS=5",
            "OCR_FFMPEG_THREADS=2",
            "OCR_PRESTART_COOLDOWN_SECONDS=20",
            "OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=0",
            "OCR_TESSERACT_NICE=0",
            "OCR_MAX_SNAPSHOT_GAP_SECONDS=120",
            "CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=false",
            "COMPLETED_JOB_RETENTION_HOURS=0",
            "local_audio_diarization=disabled_by_policy",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.plan)

    def test_docs_select_voice_by_document_type_and_hide_production_logs(self) -> None:
        for relative_path in ("README.md", "INSTALL_USAGE.md", "PLAN.md"):
            content = self.primary_docs[relative_path]
            with self.subTest(path=relative_path):
                self.assertIn("~하기로 함", content)
                self.assertIn("STT/OCR/Snapshot", content)
                self.assertNotIn("원문 STT/OCR·개인", content)

        skill = self.primary_docs["codex/skills/minutes/SKILL.md"]
        self.assertIn("meeting_minutes", skill)
        self.assertIn("content_adaptive", skill)
        self.assertIn("reader deliverables, not production logs", skill)
        self.assertIn("production logs out of reader deliverables", self.skill_agent_metadata)

    def test_docs_require_traceable_video_and_docx_evidence(self) -> None:
        for relative_path, content in self.primary_docs.items():
            with self.subTest(path=relative_path):
                self.assertIn("evidence_coverage.json", content)
                self.assertIn("docx_qa.json", content)
                self.assertIn(
                    "~/minutes/output/YYYY-MM-DD_내용-기반-제목/",
                    content,
                )
                self.assertNotIn("~/minutes/output/YYYY-MM-DD/", content)
                self.assertNotIn(
                    "YYYY-MM-DD_내용-기반-제목.docx\n  docx_qa.json",
                    content,
                )

        for content in self.primary_docs.values():
            self.assertNotIn("OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80", content)
            self.assertNotIn("OCR_TESSERACT_NICE=10", content)
            self.assertNotIn("CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=true", content)

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

    def test_fresh_codex_handoff_keeps_full_evidence_out_of_parent_context(self) -> None:
        launcher = (REPO_ROOT / "scripts/run_fresh_codex_job.py").read_text(
            encoding="utf-8"
        )
        skill = self.primary_docs["codex/skills/minutes/SKILL.md"]
        security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")

        self.assertIn('"--ephemeral"', launcher)
        self.assertIn('"parent_conversation_inherited": False', launcher)
        self.assertIn('"raw_evidence_embedded_in_handoff": False', launcher)
        self.assertIn('"mode": "preloaded_compact"', launcher)
        self.assertIn("TOOL_OUTPUT_BUDGET_EXIT_CODE", launcher)
        self.assertIn("FORBIDDEN_WORKER_INSTRUCTION_MARKERS", launcher)
        self.assertIn("content_generation_checkpoint.json", launcher)
        self.assertIn("apply_content_repair_patch.py", launcher)
        self.assertIn("--force-content-rebuild", launcher)
        self.assertIn("must not launch", skill)
        self.assertIn("content_freeze.json", skill)
        self.assertIn("translation_manifest.json", skill)
        self.assertIn("translation defaults to `low`", skill)
        self.assertIn("Raw evidence is available only", skill)
        self.assertIn("delivery worker must", skill)
        self.assertIn("evidence_chunks.json", skill)
        self.assertIn("must not invoke a skill or open `SKILL.md`", skill)
        self.assertIn("worker_contract_passed=true", skill)
        self.assertIn("content_generation_checkpoint.json", skill)
        self.assertIn("apply_content_repair_patch.py", skill)
        self.assertIn("Codex LLM provider에 노출될 수 있다", security)
        self.assertIn("file-change diff", security)
        self.assertIn("24KB", security)

    def test_strict_fresh_jobs_require_ultra_derived_quality_artifacts(self) -> None:
        skill = self.primary_docs["codex/skills/minutes/SKILL.md"]
        quality_reference = (
            REPO_ROOT / "codex/skills/minutes/references/quality-loop.md"
        ).read_text(encoding="utf-8")

        self.assertIn("references/quality-loop.md", skill)
        self.assertIn("evidence_ledger.json", skill)
        self.assertIn("document_blueprint.json", skill)
        self.assertIn("content_quality_review.json", skill)
        self.assertIn("overcompression", quality_reference)
        self.assertIn("content_freeze.json", quality_reference)
        self.assertIn("schema_version=3", quality_reference)
        self.assertIn("model-judged", quality_reference)
        self.assertIn("--blocking-defect-code", quality_reference)
        self.assertIn("review_cycles", quality_reference)
        self.assertIn("required_item_checks", quality_reference)
        self.assertIn("LOW_INFORMATION_DENSITY", quality_reference)
        self.assertIn("3-5", quality_reference)
        self.assertIn("target_section_ids", quality_reference)
        self.assertIn("translation-only turn", quality_reference)
        self.assertIn("do not summarize, fact-check, add, omit, restructure", quality_reference)

    def test_docs_do_not_restore_expensive_direct_target_synthesis_or_retention(self) -> None:
        for relative_path, content in self.primary_docs.items():
            with self.subTest(path=relative_path):
                self.assertNotIn("COMPLETED_JOB_RETENTION_HOURS=24", content)
                self.assertNotIn("목표 언어의 최종 문서를 직접 작성", content)
                self.assertNotIn("direct Korean synthesis", content)

    def test_docx_delivery_is_content_frozen_and_bounded(self) -> None:
        skill = self.primary_docs["codex/skills/minutes/SKILL.md"]
        quality_reference = (
            REPO_ROOT / "codex/skills/minutes/references/quality-loop.md"
        ).read_text(encoding="utf-8")

        self.assertIn("finalize_docx.py prepare", skill)
        self.assertIn("finalize_docx.py approve", skill)
        self.assertIn("source-frozen", quality_reference)
        self.assertIn("validated final Markdown", quality_reference)
        self.assertIn("every latest page PNG at 100%", quality_reference)
        self.assertIn("warnings alone", quality_reference)
        self.assertIn("copy and fill the retained Word", quality_reference)
        self.assertIn("NATURAL_FINAL_PAGE_WHITESPACE", quality_reference)
        self.assertIn("no maximum word, character", quality_reference)
        self.assertNotIn("SHORT_FINAL_PAGE", quality_reference)
        self.assertIn("blocking defect", quality_reference)

    def test_primary_docs_describe_template_filling_without_content_cap(self) -> None:
        for relative_path, content in self.primary_docs.items():
            with self.subTest(path=relative_path):
                self.assertIn("minutes-word-template.docx", content)
                self.assertNotIn("standard_business_brief", content)
                self.assertNotIn("SHORT_FINAL_PAGE", content)

    def test_primary_docs_require_the_two_reader_visible_trust_sections(self) -> None:
        for relative_path, content in self.primary_docs.items():
            with self.subTest(path=relative_path):
                self.assertIn("추가 검증이 필요한 항목", content)
                self.assertIn("외부 근거 확인", content)

    def test_production_jobs_use_artifact_gates_not_repository_regression(self) -> None:
        skill = self.primary_docs["codex/skills/minutes/SKILL.md"]

        self.assertIn("Per-media production verification", skill)
        self.assertIn("Repository change verification", skill)
        self.assertIn("Do not run repository-wide", skill)
        self.assertIn("unittest discover", skill)
        self.assertIn("Do not inspect validator implementations or tests", skill)
        self.assertIn("worker_runtime_summary.json", skill)
        self.assertIn("reasoning effort `high`", skill)
        self.assertIn("context_efficiency", skill)
        self.assertIn("preloaded phase", skill)
        self.assertIn("zero forbidden instruction reads", skill)

    def test_community1_is_governance_gated_and_offline_only(self) -> None:
        security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")

        self.assertIn("Community-1 gated 모델 거버넌스", security)
        self.assertIn("scripts.community1_governance", security)
        self.assertIn("HF_HUB_OFFLINE=1", security)
        self.assertIn("HUGGING_FACE_HUB_TOKEN", security)
        self.assertIn("자동 다운로드하지 않는다", security)


if __name__ == "__main__":
    unittest.main()
