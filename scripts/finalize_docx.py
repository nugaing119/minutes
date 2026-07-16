from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.archive_job import extract_recording_date, find_source
from scripts.content_freeze import validate_content_freeze
from scripts.docx_qa import create_docx_qa, file_record, render_record, sha256_file
from scripts.docx_report import generate_docx_report, resolve_word_template_path
from scripts.translation import resolve_final_markdown
from scripts.utils import now_local, read_json, write_json


MANIFEST_NAME = "docx_finalize_manifest.json"
VISUAL_REVIEW_NAME = "visual_review.json"
MAX_NORMAL_RENDER_ATTEMPTS = 2
MAX_RENDER_ATTEMPTS = 3
MAX_RENDERER_REPAIR_ATTEMPTS = 1
BLOCKING_DEFECT_CODES = {
    "BLANK_INTERIOR_PAGE",
    "BROKEN_TOC_OR_BOOKMARK",
    "CLIPPED_OR_OVERLAPPING_TEXT",
    "MISSING_CONTENT",
    "MISSING_GLYPH",
    "INCORRECT_LIST_NUMBERING",
    "ADJACENT_LARGE_IMAGES",
    "EXCESSIVE_LAYOUT_GAP",
    "IMAGE_PLACEMENT_DRIFT",
    "ORPHAN_HEADING_OR_SPLIT_ROW",
    "UNREADABLE_TABLE",
}
NONBLOCKING_WARNING_CODES = {
    "INTENTIONAL_SECTION_WHITESPACE",
    "MILD_READABLE_WRAP",
    "NATURAL_FINAL_PAGE_WHITESPACE",
    "TOC_PAGE_WHITESPACE",
}
FINAL_PAGE_WHITESPACE_MIN_PAGE_COUNT = 2
FINAL_PAGE_WHITESPACE_MIN_FILL_RATIO = 0.30
FINAL_PAGE_WHITESPACE_MIN_ACTIVE_ROW_RATIO = 0.20


def _bounded_output(value: str, limit: int = 2_000) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _clean_render_dir(render_dir: Path) -> None:
    if render_dir.exists():
        shutil.rmtree(render_dir)
    render_dir.mkdir(parents=True, exist_ok=False)


def _renderer_fingerprint(template_path: Path | None = None) -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__).with_name("docx_report.py"),
        Path(__file__).with_name("render_docx_checked.py"),
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    resolved_template = resolve_word_template_path(template_path)
    digest.update(resolved_template.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(resolved_template.read_bytes())
    digest.update(b"\0")
    return digest.hexdigest()


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    diagonal_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= diagonal_distance:
        return left
    if above_distance <= diagonal_distance:
        return above
    return upper_left


def _decode_png_rows(path: Path) -> tuple[int, int, int, list[bytes], bytes | None]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    offset = 8
    header: tuple[int, int, int, int] | None = None
    compressed = bytearray()
    palette: bytes | None = None
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            raise ValueError("truncated PNG chunk")
        payload = data[payload_start:payload_end]
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            if bit_depth != 8 or compression != 0 or filtering != 0 or interlace != 0:
                raise ValueError("unsupported PNG encoding")
            header = (width, height, bit_depth, color_type)
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        offset = payload_end + 4
    if header is None or not compressed:
        raise ValueError("PNG is missing image data")
    width, height, _bit_depth, color_type = header
    channels_by_color_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_by_color_type.get(color_type)
    if channels is None:
        raise ValueError("unsupported PNG color type")
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG scanline length does not match IHDR")
    rows: list[bytes] = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        encoded = raw[cursor : cursor + stride]
        cursor += stride
        decoded = bytearray(stride)
        for index, value in enumerate(encoded):
            left = decoded[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth_predictor(left, above, upper_left)
            else:
                raise ValueError("unsupported PNG row filter")
            decoded[index] = (value + predictor) & 0xFF
        rows.append(bytes(decoded))
        previous = decoded
    return width, height, color_type, rows, palette


def _pixel_has_ink(
    row: bytes,
    x: int,
    *,
    color_type: int,
    palette: bytes | None,
) -> bool:
    if color_type == 0:
        red = green = blue = row[x]
        alpha = 255
    elif color_type == 2:
        offset = x * 3
        red, green, blue = row[offset : offset + 3]
        alpha = 255
    elif color_type == 3:
        palette_index = row[x] * 3
        if palette is None or palette_index + 3 > len(palette):
            return False
        red, green, blue = palette[palette_index : palette_index + 3]
        alpha = 255
    elif color_type == 4:
        offset = x * 2
        red = green = blue = row[offset]
        alpha = row[offset + 1]
    else:
        offset = x * 4
        red, green, blue, alpha = row[offset : offset + 4]
    return alpha >= 32 and min(red, green, blue) < 242


def _page_content_fill(path: Path) -> dict[str, Any]:
    width, height, color_type, rows, palette = _decode_png_rows(path)
    x_start = max(0, int(width * 0.04))
    x_end = min(width, max(x_start + 1, int(width * 0.96)))
    y_start = max(0, int(height * 0.08))
    y_end = min(height, max(y_start + 1, int(height * 0.89)))
    minimum_ink_pixels = max(2, int((x_end - x_start) * 0.002))
    active_rows: list[int] = []
    for y in range(y_start, y_end):
        ink_pixels = 0
        row = rows[y]
        for x in range(x_start, x_end):
            if _pixel_has_ink(row, x, color_type=color_type, palette=palette):
                ink_pixels += 1
                if ink_pixels >= minimum_ink_pixels:
                    active_rows.append(y)
                    break
    usable_height = y_end - y_start
    content_end_ratio = (
        round((active_rows[-1] - y_start + 1) / usable_height, 4)
        if active_rows
        else 0.0
    )
    active_row_ratio = round(len(active_rows) / usable_height, 4)
    return {
        "width": width,
        "height": height,
        "active_row_count": len(active_rows),
        "active_row_ratio": active_row_ratio,
        "content_end_ratio": content_end_ratio,
    }


def render_layout_summary(render_dir: Path) -> dict[str, Any]:
    pages = sorted(
        render_dir.glob("page-*.png"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    result: dict[str, Any] = {
        "status": "unavailable",
        "page_count": len(pages),
        "final_page_whitespace_min_fill_ratio": FINAL_PAGE_WHITESPACE_MIN_FILL_RATIO,
        "final_page_whitespace_min_active_row_ratio": (
            FINAL_PAGE_WHITESPACE_MIN_ACTIVE_ROW_RATIO
        ),
        "last_page": None,
        "blocking_defects": [],
        "warnings": [],
    }
    if not pages:
        return result
    last_page_number = len(pages)
    try:
        fill = _page_content_fill(pages[-1])
    except (OSError, ValueError, zlib.error) as exc:
        result["analysis_error"] = _bounded_output(str(exc), limit=240)
        return result
    result["status"] = "analyzed"
    result["last_page"] = {"page": last_page_number, **fill}
    if (
        len(pages) >= FINAL_PAGE_WHITESPACE_MIN_PAGE_COUNT
        and (
            fill["content_end_ratio"] < FINAL_PAGE_WHITESPACE_MIN_FILL_RATIO
            or fill["active_row_ratio"] < FINAL_PAGE_WHITESPACE_MIN_ACTIVE_ROW_RATIO
        )
    ):
        result["warnings"] = [
            {
                "code": "NATURAL_FINAL_PAGE_WHITESPACE",
                "page": last_page_number,
                "content_end_ratio": fill["content_end_ratio"],
                "minimum_ratio": FINAL_PAGE_WHITESPACE_MIN_FILL_RATIO,
                "active_row_ratio": fill["active_row_ratio"],
                "minimum_active_row_ratio": FINAL_PAGE_WHITESPACE_MIN_ACTIVE_ROW_RATIO,
            }
        ]
    return result


def _next_attempt(
    previous: dict[str, Any],
    *,
    final_markdown_sha256: str,
    blocking_defect_code: str | None,
    renderer_fingerprint: str,
) -> tuple[int, list[dict[str, Any]], bool]:
    history = previous.get("history", [])
    previous_markdown_sha256 = previous.get(
        "final_markdown_sha256",
        previous.get("content_sha256"),
    )
    if (
        not isinstance(history, list)
        or previous_markdown_sha256 != final_markdown_sha256
    ):
        history = []
    attempt = len(history) + 1
    renderer_repair = False
    if attempt > MAX_RENDER_ATTEMPTS:
        prior_renderer = previous.get("renderer_fingerprint")
        renderer_repair_count = sum(
            record.get("renderer_repair") is True
            for record in history
            if isinstance(record, dict)
        )
        renderer_repair = (
            blocking_defect_code in BLOCKING_DEFECT_CODES
            and prior_renderer != renderer_fingerprint
            and renderer_repair_count < MAX_RENDERER_REPAIR_ATTEMPTS
        )
        if not renderer_repair:
            raise ValueError(
                f"DOCX render attempts must not exceed {MAX_RENDER_ATTEMPTS}; "
                "one additional blocking-defect repair is allowed only after the renderer changes"
            )
    if attempt > MAX_NORMAL_RENDER_ATTEMPTS:
        if blocking_defect_code not in BLOCKING_DEFECT_CODES:
            raise ValueError(
                "a third DOCX render, or a renderer-repair render, requires an explicit blocking defect code"
            )
    elif blocking_defect_code is not None:
        raise ValueError("blocking defect code is only accepted for a third render")
    return attempt, list(history), renderer_repair


def prepare_docx(
    job_dir: Path,
    *,
    reuse_final: bool = False,
    blocking_defect_code: str | None = None,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    freeze = validate_content_freeze(job_dir)
    content_sha256 = str(freeze["content_sha256"])
    markdown_path = resolve_final_markdown(job_dir)
    final_markdown_sha256 = sha256_file(markdown_path)
    word_template_path = resolve_word_template_path()
    manifest_path = job_dir / MANIFEST_NAME
    previous = read_json(manifest_path)
    renderer_fingerprint = _renderer_fingerprint(word_template_path)
    attempt, history, renderer_repair = _next_attempt(
        previous,
        final_markdown_sha256=final_markdown_sha256,
        blocking_defect_code=blocking_defect_code,
        renderer_fingerprint=renderer_fingerprint,
    )

    draft_path = job_dir / "minutes.draft.docx"
    final_path = job_dir / "minutes.final.docx"
    render_dir = job_dir / "docx_render"
    qa_path = job_dir / "docx_qa.json"
    if reuse_final:
        for path in (draft_path, final_path):
            if not path.is_file():
                raise FileNotFoundError(path)
    else:
        saved_date = extract_recording_date(job_dir, find_source(job_dir))
        generate_docx_report(
            markdown_path,
            draft_path,
            saved_date=saved_date,
            template_path=word_template_path,
        )
        shutil.copy2(draft_path, final_path)

    _clean_render_dir(render_dir)
    command = [
        sys.executable,
        str(Path(__file__).with_name("render_docx_checked.py")),
        str(final_path),
        "--output_dir",
        str(render_dir),
        "--emit_pdf",
    ]
    completed = runner(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "DOCX render failed: "
            + _bounded_output((completed.stderr or completed.stdout or "unknown error"))
        )

    qa = create_docx_qa(
        markdown_path,
        draft_path,
        final_path,
        render_dir=render_dir,
        visual_status="not_run",
        output_path=qa_path,
    )
    rendered = qa["render"]
    if qa["status"] != "structural_only":
        raise ValueError("DOCX structural QA failed: " + "; ".join(qa["issues"]))
    render_layout = render_layout_summary(render_dir)
    if render_layout.get("status") != "analyzed":
        raise ValueError(
            "DOCX layout occupancy analysis failed: "
            + str(render_layout.get("analysis_error", "no analyzable rendered page"))
        )
    attempt_record = {
        "attempt": attempt,
        "prepared_at": now_local().isoformat(),
        "reuse_final": reuse_final,
        "blocking_defect_code": blocking_defect_code,
        "renderer_fingerprint": renderer_fingerprint,
        "renderer_repair": renderer_repair,
        "final_docx_sha256": qa["final_docx"]["sha256"],
        "render_manifest_sha256": rendered["manifest_sha256"],
        "page_count": rendered["page_count"],
        "blocking_layout_defects": render_layout["blocking_defects"],
        "layout_warnings": render_layout["warnings"],
    }
    history.append(attempt_record)
    manifest = {
        "schema_version": 1,
        "status": "awaiting_visual_review",
        "content_sha256": content_sha256,
        "final_markdown_sha256": final_markdown_sha256,
        "attempt": attempt,
        "normal_attempt_limit": MAX_NORMAL_RENDER_ATTEMPTS,
        "absolute_attempt_limit": MAX_RENDER_ATTEMPTS,
        "renderer_repair_limit": MAX_RENDERER_REPAIR_ATTEMPTS,
        "renderer_fingerprint": renderer_fingerprint,
        "word_template": file_record(word_template_path),
        "draft_docx": file_record(draft_path),
        "final_docx": file_record(final_path),
        "render": render_record(render_dir, visual_status="not_run"),
        "render_layout": render_layout,
        "history": history,
    }
    write_json(manifest_path, manifest)
    return manifest


def _validate_warning_records(warnings: Any, *, page_count: int) -> list[str]:
    issues: list[str] = []
    if not isinstance(warnings, list):
        return ["warnings must be a list"]
    for index, warning in enumerate(warnings):
        if not isinstance(warning, dict):
            issues.append(f"warnings[{index}] must be an object")
            continue
        if warning.get("code") not in NONBLOCKING_WARNING_CODES:
            issues.append(f"warnings[{index}] uses an unsupported warning code")
        page = warning.get("page")
        if not isinstance(page, int) or page < 1 or page > page_count:
            issues.append(f"warnings[{index}].page must be an integer")
    return issues


def approve_docx(
    job_dir: Path,
    *,
    review_path: Path | None = None,
) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    freeze = validate_content_freeze(job_dir)
    manifest_path = job_dir / MANIFEST_NAME
    manifest = read_json(manifest_path)
    if manifest.get("status") != "awaiting_visual_review":
        raise ValueError("DOCX must be prepared before visual approval")
    final_path = job_dir / "minutes.final.docx"
    draft_path = job_dir / "minutes.draft.docx"
    markdown_path = resolve_final_markdown(job_dir)
    final_markdown_sha256 = sha256_file(markdown_path)
    render_dir = job_dir / "docx_render"
    current_render = render_record(render_dir, visual_status="passed")
    current_final_sha256 = sha256_file(final_path)
    if manifest.get("content_sha256") != freeze.get("content_sha256"):
        raise ValueError("DOCX manifest content hash does not match the content freeze")
    if manifest.get("final_markdown_sha256") != final_markdown_sha256:
        raise ValueError("DOCX manifest final Markdown hash does not match")
    if manifest.get("final_docx", {}).get("sha256") != current_final_sha256:
        raise ValueError("final DOCX changed after the latest render")
    if manifest.get("render", {}).get("manifest_sha256") != current_render.get(
        "manifest_sha256"
    ):
        raise ValueError("rendered pages changed after the latest prepare step")

    review_path = (review_path or job_dir / VISUAL_REVIEW_NAME).expanduser().resolve()
    review = read_json(review_path)
    issues: list[str] = []
    render_layout = render_layout_summary(render_dir)
    if render_layout.get("status") != "analyzed":
        issues.append("deterministic render layout occupancy analysis is unavailable")
    for defect in render_layout.get("blocking_defects", []):
        if isinstance(defect, dict):
            issues.append(
                "deterministic render blocking defect "
                f"{defect.get('code')} on page {defect.get('page')}"
            )
    if review.get("schema_version") != 1:
        issues.append("visual review schema_version must be 1")
    if review.get("status") != "passed":
        issues.append("visual review status must be passed")
    expected_pages = list(range(1, int(current_render["page_count"]) + 1))
    if review.get("inspected_pages") != expected_pages:
        issues.append("visual review must list every latest rendered page")
    blockers = review.get("blocking_defects")
    if not isinstance(blockers, list) or blockers:
        issues.append("blocking_defects must be an empty list before approval")
    issues.extend(
        _validate_warning_records(
            review.get("warnings"),
            page_count=int(current_render["page_count"]),
        )
    )
    if issues:
        raise ValueError("visual review failed: " + "; ".join(issues))

    review["bindings"] = {
        "content_sha256": freeze["content_sha256"],
        "final_markdown_sha256": final_markdown_sha256,
        "final_docx_sha256": current_final_sha256,
        "render_manifest_sha256": current_render["manifest_sha256"],
    }
    write_json(review_path, review)
    qa = create_docx_qa(
        markdown_path,
        draft_path,
        final_path,
        render_dir=render_dir,
        visual_status="passed",
        output_path=job_dir / "docx_qa.json",
        visual_review=review,
    )
    if qa["status"] != "passed":
        raise ValueError("DOCX approval QA failed: " + "; ".join(qa["issues"]))
    manifest.update(
        {
            "status": "passed",
            "approved_at": now_local().isoformat(),
            "visual_review": file_record(review_path),
            "docx_qa": file_record(job_dir / "docx_qa.json"),
            "render_layout": render_layout,
        }
    )
    write_json(manifest_path, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Prepare, render, and approve a content-frozen DOCX."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("job_dir", type=Path)
    prepare.add_argument("--reuse-final", action="store_true")
    prepare.add_argument(
        "--blocking-defect-code",
        choices=sorted(BLOCKING_DEFECT_CODES),
    )
    approve = subparsers.add_parser("approve")
    approve.add_argument("job_dir", type=Path)
    approve.add_argument("--review", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = (
            prepare_docx(
                args.job_dir,
                reuse_final=args.reuse_final,
                blocking_defect_code=args.blocking_defect_code,
            )
            if args.command == "prepare"
            else approve_docx(args.job_dir, review_path=args.review)
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {_bounded_output(str(exc))}", file=sys.stderr)
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "status": result["status"],
                "attempt": result.get("attempt"),
                "content_sha256": result["content_sha256"],
                "page_count": result.get("render", {}).get("page_count"),
                "blocking_defects": result.get("render_layout", {}).get(
                    "blocking_defects",
                    [],
                ),
                "warnings": result.get("render_layout", {}).get("warnings", []),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
