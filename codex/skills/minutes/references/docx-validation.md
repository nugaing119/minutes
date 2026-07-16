# DOCX Validation Reference

Use this reference when modifying or validating `scripts/docx_report.py`.

The generator starts from `../assets/minutes-word-template.docx`; it does not reconstruct page
geometry, styles, numbering, or the footer for each job. See `word-template.md` for the retained
components and slot contract. `docx_finalize_manifest.json` records the exact template SHA-256.

## Internal TOC Links

A clickable static TOC requires both sides:

- TOC item: `w:hyperlink` with a `w:anchor` value.
- Body heading: matching `w:bookmarkStart` / `w:bookmarkEnd` using that anchor.

Validation script pattern:

```python
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

path = Path("/path/to/file.docx")
ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
attr = lambda name: f"{{{ns['w']}}}{name}"
with ZipFile(path) as archive:
    root = ET.fromstring(archive.read("word/document.xml"))
links = root.findall(".//w:hyperlink", ns)
bookmarks = root.findall(".//w:bookmarkStart", ns)
anchors = [link.get(attr("anchor")) for link in links]
bookmark_names = [bookmark.get(attr("name")) for bookmark in bookmarks]
missing = [anchor for anchor in anchors if anchor not in bookmark_names]
print(len(links), len(bookmarks), missing)
```

Expected: non-zero link/bookmark counts and `missing == []`.

When the combined H2/H3 navigation list is dense, keep all body headings and bookmarks but
collapse the visible TOC to top-level content headings. This preserves content-derived depth
without creating a mostly blank spillover TOC page.

## Checklist Rendering

Markdown task items must render as real checkbox glyphs: `- [ ]` becomes `☐` and `- [x]`
becomes `☑`. A shipped DOCX must not expose literal `[ ]` or `[x]` markers.

## Table Geometry

For each table:

- `w:tblW` is DXA and equals total table width.
- `w:tblInd` is DXA and normally `120`.
- `w:tblGrid/w:gridCol` values sum to `tblW`.
- Every row cell `w:tcW` list equals the grid values.

Use the current Documents skill `scripts/table_geometry.py` helper.

## Render QA

For a content-frozen media job, use `scripts/finalize_docx.py`, which validates the freeze and
combines generation, render-directory cleanup, full rendering, and structural QA:

```bash
python scripts/finalize_docx.py prepare /path/to/job
```

After inspecting every latest page at 100%, write `visual_review.json` and run:

```bash
python scripts/finalize_docx.py approve /path/to/job
```

Use `scripts/render_docx_checked.py` directly only when debugging the renderer. It delegates to the newest installed Documents skill renderer and supplies the optional `pdf2image` dependency through an isolated `uv` runtime when needed:

```bash
python scripts/render_docx_checked.py \
  /path/to/file.docx \
  --output_dir /private/tmp/minutes-docx-render \
  --emit_pdf
```

On macOS Codex, check `CODEX_SANDBOX` before launching. When it is `seatbelt`, do not try the command in the ordinary sandbox: run it with `sandbox_permissions=require_escalated` on the first attempt. Sandboxed LibreOffice can abort and show a persistent macOS crash dialog. The wrapper exits with code 78 before starting `soffice` if this unsafe path is detected.

Outside the Codex macOS sandbox, if the bundled renderer itself is unavailable, use LibreOffice and Poppler directly:

```bash
mkdir -p /private/tmp/minutes-docx-render /private/tmp/minutes-lo-profile
env HOME=/private/tmp/minutes-lo-profile TMPDIR=/private/tmp \
  soffice --headless \
  -env:UserInstallation=file:///private/tmp/minutes-lo-profile \
  --convert-to pdf --outdir /private/tmp/minutes-docx-render /path/to/file.docx
pdftoppm -png /private/tmp/minutes-docx-render/file.pdf \
  /private/tmp/minutes-docx-render/page
```

Inspect every rendered page with `view_image`, including the cover, TOC, all tables, and final
page. Reject clipping/overlap, missing glyph or content, a blank interior page, broken navigation,
an unreadable table, literal task-list markers, an orphan heading/split row, excessive layout gaps,
adjacent large images, or image placement drift. `finalize_docx.py` still measures the last page's
content depth and active ink-row coverage, but a naturally short final page is recorded as
`NATURAL_FINAL_PAGE_WHITESPACE`, not a defect. Do not add filler, remove complete content, or reflow
the document merely to change that measurement. Treat it together with whitespace on one TOC page,
intentional section whitespace, and mild readable wrapping as nonblocking warnings. Warnings alone
do not authorize content changes or another render.
