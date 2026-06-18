from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor, Twips


BLUE_DARK = "1F3864"
BLUE = "2E75B6"
BLUE_LIGHT = "D5E8F0"
GRAY_LIGHT = "F2F2F2"
ORANGE_LIGHT = "FFF3E0"
BORDER = "CCCCCC"
WHITE = "FFFFFF"
BODY_FONT = "Arial"


@dataclass
class MarkdownTable:
    headers: list[str]
    rows: list[list[str]]


@dataclass
class MarkdownBlock:
    kind: str
    text: str = ""
    level: int = 0
    items: list[str] | None = None
    table: MarkdownTable | None = None


def generate_docx_report(
    markdown_path: Path,
    output_path: Path,
    meeting_title: str,
    saved_date: str,
) -> Path:
    markdown = markdown_path.read_text(encoding="utf-8")
    blocks = parse_markdown(markdown)
    heading_anchors = build_heading_anchors(blocks)

    doc = Document()
    configure_document(doc)
    configure_styles(doc)
    add_header_footer(doc)
    add_cover(doc, meeting_title, saved_date)
    add_static_toc(doc, blocks, heading_anchors)
    add_body(doc, blocks, heading_anchors)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path


def parse_markdown(markdown: str) -> list[MarkdownBlock]:
    lines = markdown.splitlines()
    blocks: list[MarkdownBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            index += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            blocks.append(
                MarkdownBlock(
                    kind="heading",
                    level=len(heading.group(1)),
                    text=heading.group(2).strip(),
                )
            )
            index += 1
            continue

        if is_table_start(lines, index):
            table, index = parse_table(lines, index)
            blocks.append(MarkdownBlock(kind="table", table=table))
            continue

        if line.lstrip().startswith("- "):
            items = []
            while index < len(lines) and lines[index].lstrip().startswith("- "):
                items.append(lines[index].lstrip()[2:].strip())
                index += 1
            blocks.append(MarkdownBlock(kind="bullets", items=items))
            continue

        paragraph_lines = [line.strip()]
        index += 1
        while index < len(lines):
            next_line = lines[index].rstrip()
            if not next_line.strip():
                break
            if re.match(r"^(#{1,6})\s+(.+)$", next_line):
                break
            if next_line.lstrip().startswith("- ") or is_table_start(lines, index):
                break
            paragraph_lines.append(next_line.strip())
            index += 1
        blocks.append(MarkdownBlock(kind="paragraph", text=" ".join(paragraph_lines)))
    return blocks


def is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return lines[index].strip().startswith("|") and re.match(
        r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$",
        lines[index + 1].strip(),
    )


def parse_table(lines: list[str], index: int) -> tuple[MarkdownTable, int]:
    headers = split_table_row(lines[index])
    index += 2
    rows = []
    while index < len(lines) and lines[index].strip().startswith("|"):
        rows.append(split_table_row(lines[index]))
        index += 1
    width = len(headers)
    rows = [row + [""] * (width - len(row)) for row in rows]
    return MarkdownTable(headers=headers, rows=[row[:width] for row in rows]), index


def split_table_row(line: str) -> list[str]:
    clean = line.strip().strip("|")
    return [cell.replace("\\|", "|").strip() for cell in clean.split("|")]


def build_heading_anchors(blocks: list[MarkdownBlock]) -> dict[int, str]:
    anchors = {}
    count = 1
    for index, block in enumerate(blocks):
        if block.kind != "heading" or block.level > 3:
            continue
        if block.level == 1 and block.text == "회의록":
            continue
        anchors[index] = f"meeting_section_{count:04d}"
        count += 1
    return anchors


def configure_document(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.69)
    section.bottom_margin = Inches(0.69)
    section.left_margin = Inches(0.69)
    section.right_margin = Inches(0.69)
    section.header_distance = Inches(0.49)
    section.footer_distance = Inches(0.49)


def configure_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.font.size = Pt(9.5)
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.08

    bullet = doc.styles["List Bullet"]
    bullet.font.name = BODY_FONT
    bullet._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    bullet.font.size = Pt(9.5)
    bullet.paragraph_format.left_indent = Inches(0.32)
    bullet.paragraph_format.first_line_indent = Inches(-0.18)
    bullet.paragraph_format.space_after = Pt(4)
    bullet.paragraph_format.line_spacing = 1.08

    for style_name, size, color, before, after in (
        ("Heading 1", 16, BLUE_DARK, 20, 10),
        ("Heading 2", 13, BLUE, 15, 7),
        ("Heading 3", 11, BLUE_DARK, 10, 5),
    ):
        style = doc.styles[style_name]
        style.font.name = BODY_FONT
        style._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)


def add_header_footer(doc: Document) -> None:
    section = doc.sections[0]
    header = section.header
    header.is_linked_to_previous = False
    clear_paragraphs(header.paragraphs)

    footer = section.footer
    footer.is_linked_to_previous = False
    clear_paragraphs(footer.paragraphs)
    footer_paragraph = footer.paragraphs[0]
    footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_paragraph.add_run("- ")
    add_field(footer_paragraph, "PAGE")
    footer_paragraph.add_run(" -")
    for run in footer_paragraph.runs:
        run.font.name = BODY_FONT
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor.from_string("999999")


def clear_paragraphs(paragraphs) -> None:
    for paragraph in paragraphs:
        for run in paragraph.runs:
            run._element.getparent().remove(run._element)


def add_cover(doc: Document, meeting_title: str, saved_date: str) -> None:
    for _ in range(5):
        doc.add_paragraph("")

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(meeting_title)
    run.font.name = BODY_FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    run.font.size = Pt(28)
    run.font.bold = True
    run.font.color.rgb = RGBColor.from_string(BLUE_DARK)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_run = subtitle.add_run("회의록")
    subtitle_run.font.name = BODY_FONT
    subtitle_run.font.size = Pt(17)
    subtitle_run.font.bold = True
    subtitle_run.font.color.rgb = RGBColor.from_string(BLUE)

    line = doc.add_paragraph()
    add_bottom_border(line, BLUE, "8")

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_run = meta.add_run(f"작성일 : {saved_date}")
    meta_run.font.name = BODY_FONT
    meta_run.font.size = Pt(9.5)
    meta_run.font.color.rgb = RGBColor.from_string("666666")

    doc.add_page_break()


def add_static_toc(
    doc: Document,
    blocks: list[MarkdownBlock],
    heading_anchors: dict[int, str],
) -> None:
    heading = doc.add_paragraph("목차", style="Heading 1")
    add_bottom_border(heading, BLUE, "6")
    for index, block in enumerate(blocks):
        if block.kind != "heading" or block.level > 3:
            continue
        text = block.text
        if block.level == 1 and text == "회의록":
            continue
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.left_indent = Inches(0.22 * max(block.level - 1, 0))
        add_internal_hyperlink(
            paragraph,
            text,
            heading_anchors[index],
            BLUE_DARK if block.level == 2 else "333333",
        )
    doc.add_page_break()


def add_body(
    doc: Document,
    blocks: list[MarkdownBlock],
    heading_anchors: dict[int, str],
) -> None:
    for index, block in enumerate(blocks):
        if block.kind == "heading":
            if block.level == 1 and block.text == "회의록":
                continue
            style = "Heading 1" if block.level <= 2 else "Heading 2" if block.level == 3 else "Heading 3"
            paragraph = doc.add_paragraph(block.text, style=style)
            add_bookmark(paragraph, heading_anchors[index], index + 1)
            if style == "Heading 1":
                add_bottom_border(paragraph, BLUE, "6")
        elif block.kind == "paragraph":
            add_paragraph(doc, block.text)
        elif block.kind == "bullets":
            for item in block.items or []:
                add_bullet(doc, item)
        elif block.kind == "table" and block.table is not None:
            add_table(doc, block.table)


def add_paragraph(doc: Document, text: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.name = BODY_FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    run.font.size = Pt(9.5)


def add_bullet(doc: Document, text: str) -> None:
    paragraph = doc.add_paragraph(style="List Bullet")
    body = paragraph.add_run(text)
    body.font.name = BODY_FONT
    body._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    body.font.size = Pt(9.5)


def add_internal_hyperlink(paragraph, text: str, anchor: str, color: str) -> None:
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), anchor)
    hyperlink.set(qn("w:history"), "1")

    run = OxmlElement("w:r")
    run_properties = OxmlElement("w:rPr")

    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), BODY_FONT)
    fonts.set(qn("w:hAnsi"), BODY_FONT)
    fonts.set(qn("w:eastAsia"), BODY_FONT)
    run_properties.append(fonts)

    size = OxmlElement("w:sz")
    size.set(qn("w:val"), "19")
    run_properties.append(size)

    color_element = OxmlElement("w:color")
    color_element.set(qn("w:val"), color)
    run_properties.append(color_element)

    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    run_properties.append(underline)

    text_element = OxmlElement("w:t")
    text_element.text = text

    run.append(run_properties)
    run.append(text_element)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def add_bookmark(paragraph, name: str, bookmark_id: int) -> None:
    bookmark_start = OxmlElement("w:bookmarkStart")
    bookmark_start.set(qn("w:id"), str(bookmark_id))
    bookmark_start.set(qn("w:name"), name)

    bookmark_end = OxmlElement("w:bookmarkEnd")
    bookmark_end.set(qn("w:id"), str(bookmark_id))

    insert_position = 1 if paragraph._p.pPr is not None else 0
    paragraph._p.insert(insert_position, bookmark_start)
    paragraph._p.append(bookmark_end)


def add_table(doc: Document, table: MarkdownTable) -> None:
    width = max(len(table.headers), 1)
    word_table = doc.add_table(rows=len(table.rows) + 1, cols=width)
    widths = column_widths(width)
    set_table_width(word_table, 9360, widths)
    set_table_borders(word_table)
    set_table_cell_margins(word_table, 80, 120)
    for idx, cell in enumerate(word_table.rows[0].cells):
        set_cell_width(cell, widths[idx])
        set_cell_fill(cell, BLUE_DARK)
        set_cell_text(cell, table.headers[idx], bold=True, color=WHITE, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_repeat_table_header(word_table.rows[0])
    set_row_cant_split(word_table.rows[0])

    for row_index, row_values in enumerate(table.rows, start=1):
        cells = word_table.rows[row_index].cells
        for idx, cell in enumerate(cells):
            set_cell_width(cell, widths[idx])
            if idx == 0:
                set_cell_fill(cell, BLUE_LIGHT)
            elif row_index % 2 == 0:
                set_cell_fill(cell, GRAY_LIGHT)
            set_cell_text(cell, row_values[idx] if idx < len(row_values) else "")
        set_row_cant_split(word_table.rows[row_index])
    doc.add_paragraph("")


def column_widths(count: int) -> list[int]:
    if count == 1:
        return [9360]
    if count == 2:
        return [3000, 6360]
    if count == 3:
        return [2600, 3380, 3380]
    if count == 4:
        return [2300, 3200, 1800, 2060]
    total = 9360
    first = 800
    rest = int((total - first) / (count - 1))
    return [first] + [rest] * (count - 1)


def set_cell_text(
    cell,
    text: str,
    bold: bool = False,
    color: str = "000000",
    align: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.LEFT,
) -> None:
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    paragraph = cell.paragraphs[0]
    clear_paragraphs([paragraph])
    paragraph.alignment = align
    paragraph.paragraph_format.space_before = Pt(1)
    paragraph.paragraph_format.space_after = Pt(1)
    run = paragraph.add_run(text)
    run.font.name = BODY_FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    run.font.size = Pt(8.5)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def set_table_width(table, width: int, widths: list[int]) -> None:
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(width))

    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:type"), "dxa")
    tbl_ind.set(qn("w:w"), "120")

    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")

    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for col_width in widths:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(col_width))
        grid.append(grid_col)

    for idx, col_width in enumerate(widths):
        table.columns[idx].width = Twips(col_width)


def set_cell_width(cell, width: int) -> None:
    cell.width = Twips(width)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:type"), "dxa")
    tc_w.set(qn("w:w"), str(width))
    set_cell_margins(cell, 80, 120)


def set_cell_fill(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), BORDER)


def set_table_cell_margins(table, vertical: int, horizontal: int) -> None:
    tbl_pr = table._tbl.tblPr
    margins = tbl_pr.find(qn("w:tblCellMar"))
    if margins is None:
        margins = OxmlElement("w:tblCellMar")
        tbl_pr.append(margins)
    for side, margin in (
        ("top", vertical),
        ("bottom", vertical),
        ("start", horizontal),
        ("end", horizontal),
    ):
        element = margins.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            margins.append(element)
        element.set(qn("w:w"), str(margin))
        element.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = tr_pr.find(qn("w:tblHeader"))
    if header is None:
        header = OxmlElement("w:tblHeader")
        tr_pr.append(header)
    header.set(qn("w:val"), "true")


def set_row_cant_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = tr_pr.find(qn("w:cantSplit"))
    if cant_split is None:
        cant_split = OxmlElement("w:cantSplit")
        tr_pr.append(cant_split)


def set_cell_margins(cell, vertical: int, horizontal: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = tc_pr.find(qn("w:tcMar"))
    if margins is None:
        margins = OxmlElement("w:tcMar")
        tc_pr.append(margins)
    for side, margin in (
        ("top", vertical),
        ("bottom", vertical),
        ("start", horizontal),
        ("end", horizontal),
    ):
        element = margins.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            margins.append(element)
        element.set(qn("w:w"), str(margin))
        element.set(qn("w:type"), "dxa")


def add_bottom_border(paragraph, color: str, size: str) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    border = p_pr.find(qn("w:pBdr"))
    if border is None:
        border = OxmlElement("w:pBdr")
        p_pr.append(border)
    bottom = border.find(qn("w:bottom"))
    if bottom is None:
        bottom = OxmlElement("w:bottom")
        border.append(bottom)
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)


def add_field(paragraph, field: str) -> None:
    run = paragraph.add_run()
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = field
    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char_begin)
    run._r.append(instr_text)
    run._r.append(fld_char_end)
