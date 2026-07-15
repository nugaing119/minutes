from __future__ import annotations

import tempfile
import unittest
import json
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.archive_job import (
    archive_job,
    cleanup_job_media,
    extract_recording_date,
    resolve_document_titles,
)
from scripts.docx_qa import create_docx_qa
from scripts.docx_report import generate_docx_report


class ArchiveSpeakerArtifactsTests(unittest.TestCase):
    def test_archive_ships_validated_translated_markdown_as_the_final_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            job_dir.mkdir()
            (job_dir / "source.mov").write_bytes(b"source")
            (job_dir / "minutes.md").write_text(
                "# English report\n\nDocument type: briefing\n",
                encoding="utf-8",
            )
            translated = job_dir / "minutes.translated.md"
            translated.write_text(
                "# 한국어 보고서\n\n문서 유형: 브리핑\n",
                encoding="utf-8",
            )
            (job_dir / "source_metadata.json").write_text(
                json.dumps({"original_name": "2026-07-15 video.mov"}),
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                output_dir=root / "output",
                jobs_dir=root,
                docx_enabled=False,
                cleanup_job_media_after_archive=False,
                completed_job_retention_hours=0,
                content_audit_mode="off",
                official_source_verification="off",
            )

            with patch(
                "scripts.archive_job.resolve_final_markdown",
                return_value=translated,
            ):
                output_dir = archive_job(job_dir, settings=settings)

            archived_markdown = next(output_dir.glob("*.md"))
            self.assertIn(
                "한국어 보고서",
                archived_markdown.read_text(encoding="utf-8"),
            )
            self.assertEqual(output_dir.name, "2026-07-15_한국어-보고서")

    def test_codex_docx_archive_requires_documents_finalization_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            job_dir.mkdir()
            source = job_dir / "source.mov"
            source.write_bytes(b"source")
            (job_dir / "minutes.md").write_text("# 분석\n", encoding="utf-8")
            (job_dir / "source_metadata.json").write_text(
                json.dumps({"original_name": "2026-07-15 video.mov"}),
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                output_dir=root / "output",
                docx_enabled=True,
                llm_provider="codex",
                content_audit_mode="off",
                official_source_verification="off",
                cleanup_job_ocr_images_after_archive=False,
                cleanup_job_media_after_archive=False,
            )

            with self.assertRaisesRegex(
                FileNotFoundError,
                "Documents finalization",
            ):
                archive_job(job_dir, settings=settings)

            self.assertTrue(source.exists())

    def test_codex_archive_copies_prevalidated_final_docx_without_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            job_dir.mkdir()
            (job_dir / "source.mov").write_bytes(b"source")
            markdown = job_dir / "minutes.md"
            markdown.write_text(
                "# 기술 분석\n\n문서 유형: 기술 브리프\n\n## 결론\n\n본문\n",
                encoding="utf-8",
            )
            (job_dir / "source_metadata.json").write_text(
                json.dumps({"original_name": "2026-07-15 video.mov"}),
                encoding="utf-8",
            )
            draft = job_dir / "minutes.draft.docx"
            final = job_dir / "minutes.final.docx"
            render_dir = job_dir / "docx_render"
            render_dir.mkdir()
            generate_docx_report(markdown, draft, saved_date="2026-07-15")
            shutil.copy2(draft, final)
            (render_dir / "page-1.png").write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + struct.pack(">I", 13)
                + b"IHDR"
                + struct.pack(">II", 1275, 1650)
            )
            create_docx_qa(
                markdown,
                draft,
                final,
                render_dir=render_dir,
                visual_status="passed",
                output_path=job_dir / "docx_qa.json",
            )
            settings = SimpleNamespace(
                output_dir=root / "output",
                docx_enabled=True,
                llm_provider="codex",
                content_audit_mode="off",
                official_source_verification="off",
                cleanup_job_ocr_images_after_archive=False,
                cleanup_job_media_after_archive=False,
            )

            with patch(
                "scripts.docx_report.generate_docx_report",
                side_effect=AssertionError("archive must not regenerate DOCX"),
            ):
                output_dir = archive_job(job_dir, settings=settings)

            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertTrue(next(output_dir.glob("*.docx")).is_file())
            self.assertFalse((output_dir / "docx_qa.json").exists())
            self.assertEqual(
                status["files"]["docx_qa"],
                str(job_dir.resolve() / "docx_qa.json"),
            )
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                [
                    "2026-07-15_기술-분석.docx",
                    "2026-07-15_기술-분석.md",
                    "2026-07-15_기술-분석.mov",
                ],
            )

    def test_strict_content_audit_blocks_archive_before_media_moves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            job_dir.mkdir()
            source = job_dir / "source.mov"
            source.write_bytes(b"source")
            (job_dir / "minutes.md").write_text("# 분석 문서\n", encoding="utf-8")
            (job_dir / "source_metadata.json").write_text(
                json.dumps({"original_name": "2026-02-12 video.mov"}),
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                output_dir=root / "output",
                docx_enabled=False,
                cleanup_job_ocr_images_after_archive=True,
                cleanup_job_media_after_archive=True,
                content_audit_mode="strict",
                official_source_verification="off",
            )

            with self.assertRaisesRegex(ValueError, "missing content_inventory.json"):
                archive_job(job_dir, settings=settings)

            self.assertTrue(source.exists())
            self.assertFalse((root / "output").exists())

    def test_strict_fresh_job_requires_content_freeze_before_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            job_dir.mkdir()
            (job_dir / "source.mov").write_bytes(b"source")
            (job_dir / "minutes.md").write_text("# 분석 문서\n", encoding="utf-8")
            (job_dir / "evidence_chunks.json").write_text("{}", encoding="utf-8")
            (job_dir / "source_metadata.json").write_text(
                json.dumps({"original_name": "2026-02-12 video.mov"}),
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                output_dir=root / "output",
                jobs_dir=root,
                docx_enabled=False,
                cleanup_job_ocr_images_after_archive=False,
                cleanup_job_media_after_archive=False,
                completed_job_retention_hours=24,
                content_audit_mode="strict",
                official_source_verification="off",
            )
            passed_audit = {"status": "passed", "issues": []}

            with (
                patch(
                    "scripts.archive_job.validate_content_artifacts",
                    return_value=passed_audit,
                ),
                patch(
                    "scripts.content_freeze.validate_content_freeze",
                    side_effect=ValueError("content freeze validation failed"),
                ) as freeze_validator,
            ):
                with self.assertRaisesRegex(ValueError, "content freeze"):
                    archive_job(job_dir, settings=settings)

            freeze_validator.assert_called_once_with(
                job_dir.resolve(),
                revalidate_content=False,
            )
            self.assertTrue((job_dir / "source.mov").exists())

    def test_cleanup_job_media_requires_verified_archive_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            job_dir.mkdir()
            source = job_dir / "source.mov"
            audio = job_dir / "audio.wav"
            archived = root / "archived.mov"
            source.write_bytes(b"source")
            audio.write_bytes(b"audio")
            archived.write_bytes(b"different")

            with self.assertRaisesRegex(ValueError, "size"):
                cleanup_job_media(job_dir, archived)

            self.assertTrue(source.exists())
            self.assertTrue(audio.exists())

            archived.write_bytes(b"xxxxxx")
            with self.assertRaisesRegex(ValueError, "content"):
                cleanup_job_media(job_dir, archived)

            self.assertTrue(source.exists())
            self.assertTrue(audio.exists())

    def test_completed_archive_moves_media_and_keeps_only_final_deliverables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            job_dir.mkdir()
            (job_dir / "source.mov").write_bytes(b"source")
            (job_dir / "audio.wav").write_bytes(b"audio")
            (job_dir / "minutes.md").write_text(
                "# MySQL HeatWave 운영 정책\n\n"
                "문서 유형: 기술 세션 분석\n\n"
                "## 지원 종료 일정\n\n- 8.0 지원 종료\n",
                encoding="utf-8",
            )
            (job_dir / "transcript.txt").write_text("전사\n", encoding="utf-8")
            (job_dir / "transcript.json").write_text("{}", encoding="utf-8")
            (job_dir / "transcript.srt").write_text("", encoding="utf-8")
            (job_dir / "diarization.json").write_text("{}", encoding="utf-8")
            (job_dir / "process_metrics.json").write_text(
                json.dumps(
                    {
                        "state": "awaiting_codex",
                        "elapsed_seconds": 12.5,
                        "stages": [{"step": "transcribe"}],
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "status.json").write_text(
                json.dumps(
                    {
                        "status": "awaiting_codex",
                        "codex_handoff": {
                            "docx_enabled": False,
                            "selected_snapshot_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            snapshots = job_dir / "snapshots"
            snapshots.mkdir()
            (snapshots / "snapshot_0001_00-00-00.jpg").write_bytes(b"image")
            frames = job_dir / "frames"
            frames.mkdir()
            (frames / "frame_000001.jpg").write_bytes(b"raw-image")
            (job_dir / "source_metadata.json").write_text(
                json.dumps(
                    {
                        "original_name": "2026-02-12 13-37-50.mov",
                        "recording_date": "2026-02-12",
                    }
                ),
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                output_dir=root / "output",
                docx_enabled=False,
                cleanup_job_ocr_images_after_archive=True,
                cleanup_job_media_after_archive=True,
            )

            with patch("scripts.archive_job.load_settings", return_value=settings):
                output_dir = archive_job(job_dir)

            archived_media = next(output_dir.glob("*.mov"))
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(archived_media.read_bytes(), b"source")
            self.assertEqual(output_dir.parent, settings.output_dir)
            self.assertEqual(
                output_dir.name,
                "2026-02-12_MySQL-HeatWave-운영-정책",
            )
            self.assertEqual(
                archived_media.name,
                "2026-02-12_MySQL-HeatWave-운영-정책.mov",
            )
            self.assertFalse((job_dir / "source.mov").exists())
            self.assertFalse((job_dir / "audio.wav").exists())
            self.assertTrue((job_dir / "minutes.md").exists())
            self.assertTrue((job_dir / "transcript.json").exists())
            self.assertTrue((job_dir / "frames" / "frame_000001.jpg").exists())
            self.assertTrue(
                (job_dir / "snapshots" / "snapshot_0001_00-00-00.jpg").exists()
            )
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                [
                    "2026-02-12_MySQL-HeatWave-운영-정책.md",
                    "2026-02-12_MySQL-HeatWave-운영-정책.mov",
                    "snapshots",
                ],
            )
            self.assertEqual(
                set(status["files"]),
                {"source", "minutes", "snapshots", "video"},
            )
            self.assertTrue(status["cleaned_job_media"])
            self.assertFalse(status["cleaned_job_ocr_images"])
            self.assertTrue(status["retained_job_ocr_images"])
            self.assertEqual(
                status["ocr_image_retention"],
                "completed_job_retention",
            )
            self.assertEqual(status["reclaimed_bytes"], 5)
            metrics = json.loads(
                (job_dir / "process_metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metrics["state"], "completed")
            self.assertEqual(metrics["output_dir"], str(output_dir))
            self.assertEqual(metrics["preprocessing_elapsed_seconds"], 12.5)
            self.assertEqual(metrics["elapsed_scope"], "local_preprocessing_only")
            self.assertEqual(status["process_metrics"]["state"], "completed")
            self.assertEqual(status["output_root"], str(settings.output_dir))
            self.assertNotIn("date_output_dir", status)
            self.assertEqual(
                status["codex_handoff"]["selected_snapshot_count"],
                1,
            )

    def test_document_title_preserves_spaces_while_folder_title_is_safe(self) -> None:
        display_title, folder_title = resolve_document_titles(
            "MySQL HeatWave Office Hours - AutoML 동시성",
            "fallback",
        )

        self.assertEqual(
            display_title,
            "MySQL HeatWave Office Hours - AutoML 동시성",
        )
        self.assertEqual(
            folder_title,
            "MySQL-HeatWave-Office-Hours-AutoML-동시성",
        )

    def test_recording_date_prefers_original_filename_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "20260713_job"
            job_dir.mkdir()
            source = job_dir / "source.mov"
            source.write_bytes(b"source")
            (job_dir / "source_metadata.json").write_text(
                json.dumps({"original_name": "2026-02-12 13-37-50.mov"}),
                encoding="utf-8",
            )

            self.assertEqual(extract_recording_date(job_dir, source), "2026-02-12")


if __name__ == "__main__":
    unittest.main()
