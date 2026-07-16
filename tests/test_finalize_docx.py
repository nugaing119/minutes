from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document

from scripts.finalize_docx import (
    BLOCKING_DEFECT_CODES,
    NONBLOCKING_WARNING_CODES,
    approve_docx,
    prepare_docx,
    render_layout_summary,
)


def write_png_header(path: Path, width: int = 1275, height: int = 1650) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


def write_rgb_png(
    path: Path,
    *,
    width: int = 120,
    height: int = 200,
    ink_end: int = 160,
    ink_ranges: tuple[tuple[int, int], ...] | None = None,
) -> None:
    ranges = ink_ranges or ((20, ink_end),)
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            black = 16 <= x < width - 16 and any(
                start <= y < end for start, end in ranges
            )
            row.extend((0, 0, 0) if black else (255, 255, 255))
        rows.append(bytes(row))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


class FinalizeDocxTests(unittest.TestCase):
    def _job(self, root: Path) -> Path:
        job = root / "job"
        job.mkdir()
        (job / "source.mov").write_bytes(b"video")
        (job / "source_metadata.json").write_text(
            json.dumps({"original_name": "2026-07-15 demo.mov"}),
            encoding="utf-8",
        )
        (job / "minutes.md").write_text(
            "# 기술 검토\n\n"
            "문서 유형: 기술 브리프\n\n"
            "## 결론\n\n"
            "본문을 변경하지 않고 Word 레이아웃을 검증한다.\n",
            encoding="utf-8",
        )
        return job

    @staticmethod
    def _renderer(command: list[str], **_kwargs: object) -> SimpleNamespace:
        output_dir = Path(command[command.index("--output_dir") + 1])
        write_rgb_png(output_dir / "page-1.png", ink_end=170)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    @staticmethod
    def _invalid_png_renderer(
        command: list[str], **_kwargs: object
    ) -> SimpleNamespace:
        output_dir = Path(command[command.index("--output_dir") + 1])
        write_png_header(output_dir / "page-1.png")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    @staticmethod
    def _short_final_page_renderer(
        command: list[str], **_kwargs: object
    ) -> SimpleNamespace:
        output_dir = Path(command[command.index("--output_dir") + 1])
        write_rgb_png(output_dir / "page-1.png", ink_end=170)
        write_rgb_png(output_dir / "page-2.png", ink_end=48)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_prepare_and_approve_preserve_frozen_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            original_markdown = (job / "minutes.md").read_bytes()
            freeze = {"status": "frozen", "content_sha256": "a" * 64}
            with patch(
                "scripts.finalize_docx.validate_content_freeze",
                return_value=freeze,
            ):
                prepared = prepare_docx(job, runner=self._renderer)
                (job / "visual_review.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "status": "passed",
                            "inspected_pages": [1],
                            "blocking_defects": [],
                            "warnings": [],
                        }
                    ),
                    encoding="utf-8",
                )
                approved = approve_docx(job)
                final_markdown = (job / "minutes.md").read_bytes()
                qa_exists = (job / "docx_qa.json").is_file()

        self.assertEqual(prepared["status"], "awaiting_visual_review")
        self.assertEqual(approved["status"], "passed")
        self.assertEqual(final_markdown, original_markdown)
        self.assertTrue(qa_exists)
        self.assertTrue(Path(prepared["word_template"]["path"]).is_file())

    def test_natural_final_page_whitespace_is_a_nonblocking_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            freeze = {"status": "frozen", "content_sha256": "a" * 64}
            with patch(
                "scripts.finalize_docx.validate_content_freeze",
                return_value=freeze,
            ):
                prepared = prepare_docx(
                    job,
                    runner=self._short_final_page_renderer,
                )
                (job / "visual_review.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "status": "passed",
                            "inspected_pages": [1, 2],
                            "blocking_defects": [],
                            "warnings": [],
                        }
                    ),
                    encoding="utf-8",
                )
                approved = approve_docx(job)

        self.assertEqual(approved["status"], "passed")
        self.assertNotIn("SHORT_FINAL_PAGE", BLOCKING_DEFECT_CODES)
        self.assertIn("NATURAL_FINAL_PAGE_WHITESPACE", NONBLOCKING_WARNING_CODES)
        self.assertEqual(prepared["render_layout"]["blocking_defects"], [])
        self.assertEqual(
            prepared["render_layout"]["warnings"][0]["code"],
            "NATURAL_FINAL_PAGE_WHITESPACE",
        )

    def test_sparse_final_page_is_measured_without_forcing_reflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            render_dir = Path(temp_dir)
            write_rgb_png(render_dir / "page-1.png", ink_end=170)
            write_rgb_png(
                render_dir / "page-2.png",
                ink_ranges=((20, 46), (70, 74)),
            )

            summary = render_layout_summary(render_dir)

        self.assertGreater(summary["last_page"]["content_end_ratio"], 0.30)
        self.assertLess(summary["last_page"]["active_row_ratio"], 0.20)
        self.assertEqual(summary["blocking_defects"], [])
        self.assertEqual(
            summary["warnings"][0]["code"],
            "NATURAL_FINAL_PAGE_WHITESPACE",
        )

    def test_prepare_fails_closed_when_layout_analysis_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            freeze = {"status": "frozen", "content_sha256": "a" * 64}
            with patch(
                "scripts.finalize_docx.validate_content_freeze",
                return_value=freeze,
            ):
                with self.assertRaisesRegex(ValueError, "layout occupancy analysis"):
                    prepare_docx(job, runner=self._invalid_png_renderer)

    def test_prepare_uses_validated_translated_markdown_for_word(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            translated = job / "minutes.translated.md"
            translated.write_text(
                "# 번역된 기술 검토\n\n"
                "문서 유형: 기술 브리프\n\n"
                "## 결론\n\n"
                "영어 완성본을 한 번 번역한 최종 문서다.\n",
                encoding="utf-8",
            )
            freeze = {"status": "frozen", "content_sha256": "c" * 64}
            with (
                patch(
                    "scripts.finalize_docx.validate_content_freeze",
                    return_value=freeze,
                ),
                patch(
                    "scripts.finalize_docx.resolve_final_markdown",
                    return_value=translated,
                ),
            ):
                prepare_docx(job, runner=self._renderer)
            document_text = "\n".join(
                paragraph.text
                for paragraph in Document(job / "minutes.draft.docx").paragraphs
            )

        self.assertIn("번역된 기술 검토", document_text)
        self.assertNotIn("본문을 변경하지 않고", document_text)

    def test_third_render_requires_explicit_blocking_defect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            freeze = {"status": "frozen", "content_sha256": "b" * 64}
            with patch(
                "scripts.finalize_docx.validate_content_freeze",
                return_value=freeze,
            ):
                prepare_docx(job, runner=self._renderer)
                prepare_docx(job, reuse_final=True, runner=self._renderer)
                with self.assertRaisesRegex(ValueError, "third DOCX render"):
                    prepare_docx(job, reuse_final=True, runner=self._renderer)
                third = prepare_docx(
                    job,
                    reuse_final=True,
                    blocking_defect_code="UNREADABLE_TABLE",
                    runner=self._renderer,
                )

        self.assertEqual(third["attempt"], 3)
        self.assertEqual(len(third["history"]), 3)

    def test_one_renderer_change_repair_is_allowed_after_attempt_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = self._job(Path(temp_dir))
            freeze = {"status": "frozen", "content_sha256": "b" * 64}
            with (
                patch(
                    "scripts.finalize_docx.validate_content_freeze",
                    return_value=freeze,
                ),
                patch(
                    "scripts.finalize_docx._renderer_fingerprint",
                    side_effect=["a" * 64, "a" * 64, "a" * 64, "b" * 64, "b" * 64],
                ),
            ):
                prepare_docx(job, runner=self._renderer)
                prepare_docx(job, reuse_final=True, runner=self._renderer)
                prepare_docx(
                    job,
                    reuse_final=True,
                    blocking_defect_code="UNREADABLE_TABLE",
                    runner=self._renderer,
                )
                repair = prepare_docx(
                    job,
                    reuse_final=True,
                    blocking_defect_code="INCORRECT_LIST_NUMBERING",
                    runner=self._renderer,
                )
                with self.assertRaisesRegex(ValueError, "renderer changes"):
                    prepare_docx(
                        job,
                        reuse_final=True,
                        blocking_defect_code="INCORRECT_LIST_NUMBERING",
                        runner=self._renderer,
                    )

        self.assertEqual(repair["attempt"], 4)
        self.assertTrue(repair["history"][-1]["renderer_repair"])


if __name__ == "__main__":
    unittest.main()
