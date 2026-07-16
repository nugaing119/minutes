# Retained Word Template

Use this reference when maintaining the minutes Word template or its slot-filling generator.

## Authority

- Template: `../assets/minutes-word-template.docx`
- Metadata: `../assets/minutes-word-template.json`
- Template ID: `heatwave-reference-v1`
- Approved reference SHA-256:
  `316ca5d2c58df5f53162c8bab7f363f36366cb6e366c94e9f748113f93bbf2fe`

The template is a clean reusable layout asset. It contains no meeting, product, speaker, or topic
content from the approved reference. Topic selection and section headings always come from the
current recording, inventory, and blueprint.

## Retained Components

- Letter portrait page geometry with 0.69-inch margins;
- Arial body and heading styles;
- list and heading numbering definitions;
- dark-navy table header, light-blue first column, and alternating light-gray rows;
- centered footer page-number field;
- theme and package relationships required by Word.

## Editable Slots

`scripts/docx_report.py` loads a fresh copy and clears only the body container. It then fills:

1. cover title;
2. cover document type;
3. recording date;
4. static linked TOC;
5. document body, tables, checklists, links, and selected images.

Do not let a model recreate the style system or page geometry. The Markdown has no document or
section length maximum; slot content expands to as many pages as evidence-backed completeness
requires.

## Generation and Verification

`scripts/finalize_docx.py prepare` records the exact template SHA-256 in
`docx_finalize_manifest.json`, generates the draft and final DOCX, and performs one complete render.
The full render remains required because pagination, table flow, and image placement depend on the
filled content. Rerender only after a real blocking layout correction.

A naturally sparse final page is `NATURAL_FINAL_PAGE_WHITESPACE`, a nonblocking warning. Never add
filler, remove complete content, or reflow solely to change final-page occupancy.

## Rebuilding the Asset

Only rebuild after an approved template/reference change:

```bash
python scripts/build_minutes_word_template.py \
  /path/to/approved-reference.docx \
  codex/skills/minutes/assets/minutes-word-template.docx \
  --metadata codex/skills/minutes/assets/minutes-word-template.json
```

Then run the DOCX report/QA/finalizer tests, render a representative dense document, inspect every
page, and run the skill validator. A compatible custom template may be selected with
`MINUTES_WORD_TEMPLATE`, but it must satisfy the same structural/style QA contract.
