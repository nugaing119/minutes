from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence

from docx import Document

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.docx_report import (
    WORD_TEMPLATE_ID,
    add_header_footer,
    configure_document,
    configure_styles,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_minutes_word_template(
    reference_path: Path,
    output_path: Path,
    *,
    metadata_path: Path | None = None,
) -> Path:
    reference_path = reference_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if not reference_path.is_file():
        raise FileNotFoundError(reference_path)

    reference = Document(reference_path)
    section = reference.sections[0]
    reference_geometry = {
        "page_width_in": round(section.page_width.inches, 3),
        "page_height_in": round(section.page_height.inches, 3),
        "top_margin_in": round(section.top_margin.inches, 3),
        "right_margin_in": round(section.right_margin.inches, 3),
        "bottom_margin_in": round(section.bottom_margin.inches, 3),
        "left_margin_in": round(section.left_margin.inches, 3),
    }

    template = Document()
    configure_document(template)
    configure_styles(template)
    add_header_footer(template)
    body = template._element.body
    for child in list(body):
        if child.tag.endswith("}p"):
            body.remove(child)

    properties = template.core_properties
    properties.title = "Minutes retained Word template"
    properties.subject = WORD_TEMPLATE_ID
    properties.author = ""
    properties.last_modified_by = ""
    properties.comments = (
        "Clean layout template distilled from the approved reference; "
        "contains no meeting content."
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    template.save(output_path)

    if metadata_path is not None:
        metadata_path = metadata_path.expanduser().resolve()
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "schema_version": 1,
            "template_id": WORD_TEMPLATE_ID,
            "reference": {
                "name": reference_path.name,
                "sha256": sha256_file(reference_path),
                "section_count": len(reference.sections),
                "geometry": reference_geometry,
            },
            "template": {
                "name": output_path.name,
                "sha256": sha256_file(output_path),
                "editable_slots": [
                    "cover_title",
                    "cover_document_type",
                    "cover_recording_date",
                    "static_toc",
                    "document_body",
                ],
                "retained_components": [
                    "page_geometry",
                    "style_system",
                    "numbering",
                    "footer_page_number",
                    "table_palette",
                ],
                "contains_reference_content": False,
            },
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return output_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Distill a clean reusable minutes DOCX template from an approved reference."
    )
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = build_minutes_word_template(
        args.reference,
        args.output,
        metadata_path=args.metadata,
    )
    print(result)


if __name__ == "__main__":
    main()
