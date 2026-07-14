from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn

from scripts.docx_report import generate_docx_report, wrap_cover_title


class DocxReportTests(unittest.TestCase):
    def test_long_cover_title_wraps_at_a_semantic_word_boundary(self) -> None:
        title = "OCI Console AI와 MCP 기반 클라우드 운영 자동화 검토"

        wrapped = wrap_cover_title(title)

        self.assertEqual(
            wrapped,
            "OCI Console AI와 MCP 기반\n클라우드 운영 자동화 검토",
        )
        self.assertNotIn("운\n영", wrapped)

    def test_dynamic_title_and_document_type_replace_hardcoded_minutes_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = root / "analysis.md"
            output = root / "analysis.docx"
            markdown.write_text(
                "# MySQL HeatWave 동시성 아키텍처\n\n"
                "문서 유형: 기술 세션 분석\n\n"
                "## 화자별 구성\n\n"
                "- 화자 1: 제품 개요\n"
                "- 화자 2: AutoML 및 GenAI\n\n"
                "## DASK worker 제약\n\n"
                "| 작업 | 제한 |\n"
                "|---|---|\n"
                "| GenAI | 노드당 worker 1개 |\n",
                encoding="utf-8",
            )

            generate_docx_report(
                markdown,
                output,
                document_title="MySQL HeatWave 동시성 아키텍처",
                document_type="기술 세션 분석",
                saved_date="2026-02-12",
            )

            document = Document(output)
            paragraph_text = [paragraph.text for paragraph in document.paragraphs]
            self.assertEqual(paragraph_text.count("MySQL HeatWave 동시성 아키텍처"), 1)
            self.assertIn("기술 세션 분석", paragraph_text)
            self.assertNotIn("회의록", paragraph_text)

            namespace = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            }
            attribute = lambda name: f"{{{namespace['w']}}}{name}"
            with ZipFile(output) as archive:
                root_xml = ET.fromstring(archive.read("word/document.xml"))
            anchors = [
                link.get(attribute("anchor"))
                for link in root_xml.findall(".//w:hyperlink", namespace)
            ]
            bookmarks = {
                item.get(attribute("name"))
                for item in root_xml.findall(".//w:bookmarkStart", namespace)
            }
            self.assertTrue(anchors)
            self.assertTrue(all(anchor in bookmarks for anchor in anchors))

    def test_content_headings_use_matching_multilevel_numbers_in_toc_and_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = root / "analysis.md"
            output = root / "analysis.docx"
            markdown.write_text(
                "# 기술 세션 분석\n\n"
                "문서 유형: 기술 발표\n\n"
                "## 지원 종료 정책\n\n"
                "### MySQL 8.0 일정\n\n"
                "본문\n\n"
                "## 동시성 아키텍처\n\n"
                "### DASK worker\n\n"
                "본문\n",
                encoding="utf-8",
            )

            generate_docx_report(
                markdown,
                output,
                document_title="기술 세션 분석",
                document_type="기술 발표",
                saved_date="2026-02-12",
            )

            namespace = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            }
            with ZipFile(output) as archive:
                root_xml = ET.fromstring(archive.read("word/document.xml"))
            toc_texts = [
                "".join(link.itertext())
                for link in root_xml.findall(".//w:hyperlink", namespace)
            ]
            self.assertEqual(
                toc_texts,
                [
                    "1. 지원 종료 정책",
                    "1.1 MySQL 8.0 일정",
                    "2. 동시성 아키텍처",
                    "2.1 DASK worker",
                ],
            )

            document = Document(output)
            numbered_headings = [
                paragraph
                for paragraph in document.paragraphs
                if paragraph.style.name in {"Heading 1", "Heading 2"}
                and paragraph.text != "목차"
            ]
            self.assertEqual(
                [paragraph.text for paragraph in numbered_headings],
                [
                    "지원 종료 정책",
                    "MySQL 8.0 일정",
                    "동시성 아키텍처",
                    "DASK worker",
                ],
            )
            self.assertEqual(
                [
                    int(
                        paragraph._p.pPr.numPr.ilvl.get(qn("w:val"))
                    )
                    for paragraph in numbered_headings
                ],
                [0, 1, 0, 1],
            )

    def test_inline_code_and_official_links_render_without_markdown_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = root / "analysis.md"
            output = root / "analysis.docx"
            markdown.write_text(
                "# 기술 세션 분석\n\n"
                "문서 유형: 기술 발표\n\n"
                "## 외부 근거 확인\n\n"
                "- `CALL sys.ML_RAG()` 예시는 "
                "[Oracle 공식 문서](https://docs.example.com/heatwave)에서 확인했다.\n",
                encoding="utf-8",
            )

            generate_docx_report(
                markdown,
                output,
                document_title="기술 세션 분석",
                document_type="기술 발표",
                saved_date="2026-07-13",
            )

            namespace = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
            }
            with ZipFile(output) as archive:
                document_xml = ET.fromstring(archive.read("word/document.xml"))
                relationships_xml = ET.fromstring(
                    archive.read("word/_rels/document.xml.rels")
                )

            document_text = "".join(document_xml.itertext())
            self.assertIn("CALL sys.ML_RAG()", document_text)
            self.assertIn("Oracle 공식 문서", document_text)
            self.assertNotIn("[Oracle 공식 문서]", document_text)
            self.assertNotIn("https://docs.example.com/heatwave", document_text)

            external_links = document_xml.findall(".//w:hyperlink[@r:id]", namespace)
            self.assertEqual(len(external_links), 1)
            targets = {
                relationship.get("Target")
                for relationship in relationships_xml.findall(
                    "pr:Relationship", namespace
                )
                if relationship.get("Type", "").endswith("/hyperlink")
            }
            self.assertEqual(targets, {"https://docs.example.com/heatwave"})


if __name__ == "__main__":
    unittest.main()
