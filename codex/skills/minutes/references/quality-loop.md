# Ultra-derived content quality loop

Use this workflow for every fresh-context strict Codex job. It captures the useful part of
iterative Ultra work: evidence bookkeeping, adversarial review, bounded revision, and a
verifiable stop condition. Do not copy reference-document word or page counts as targets.

## Contents

1. Build the evidence ledger
2. Challenge the inventory
3. Lock the document blueprint
4. Choose form factors
5. Run the adversarial review
6. Freeze, optionally translate once, and finish with DOCX render QA

## 1. Build the evidence ledger while reading once

Read every entry in `evidence_chunks.json` once in manifest order. Build
`evidence_ledger.json` with `schema_version=1`, `status=completed`, `chunk_count`, and one
`chunks` record per manifest chunk. Each record must contain:

- `index` and the manifest chunk `source_sha256`;
- `classification`: `material`, `mixed`, `repetition`, or `technical_noise`;
- a concise `rationale`;
- `material_topics` as a list;
- `inventory_item_ids` as a list.

Every `material` or `mixed` chunk must name its topics and map them to inventory items.
`repetition` and `technical_noise` chunks must explain why they add no inventory item. Map
every required inventory item from at least one ledger chunk.

Do not turn chunks into prose summaries and summarize them again. The ledger is an
accounting surface, not the document source. Preserve concrete dates, values, versions,
conditions, exceptions, risks, questions, answers, corrections, decisions, and follow-ups in
the inventory.

The launcher preloads this maintenance contract into the compact content-worker prompt. A fresh
worker must not reopen this file or another SKILL/reference file. Keep a read-once checklist for
evidence chunks only. Manifest line ranges refer to the unsplit source; read every part in full
once, and treat a repeated part path as a contract violation. Do not reopen already-read chunks,
generated artifacts, validator source, or unit tests merely to gain confidence. Run the
deterministic gate and inspect only bounded error details or targeted artifact excerpts when it
fails. Repository-wide regression testing belongs to skill/code changes, not a production media
job.

## 2. Challenge the inventory before drafting

Review the completed ledger and inventory as an adversarial critic. Check for:

- a long or information-dense time range collapsed into one broad item;
- a question whose answer, limitation, or disagreement is missing;
- examples, demonstrations, and operational conditions treated as filler;
- OCR-only UI state, tables, labels, or values omitted from a screen-driven explanation;
- uncertainty, negation, qualification, or source conflict flattened into a conclusion;
- repeated themes that contain new values, conditions, or commitments.

Repair the inventory before writing `minutes.md`.

## 3. Lock a reader-facing document blueprint before drafting

Create `document_blueprint.json` after the inventory and before `minutes.md`. This is the
low-freedom authoring contract that keeps weaker models from drifting into a chronology dump,
an over-fragmented outline, or a citation-heavy audit report. Use `schema_version=1`,
`status=completed`, and these top-level fields:

- `document_archetype`: one of `technical_session_analysis`, `product_demo_analysis`,
  `technical_decision_record`, `strategy_session_analysis`, or
  `general_recording_analysis`;
- `document_type` and a concise `reader_goal`;
- `front_matter`, with exact `key`, `label`, and `value` records for `source`,
  `recording_datetime`, `duration`, `source_language`, `output_language`,
  `evidence_basis`, and `external_evidence_policy`;
- ordered `sections`, each with a unique `id`, exact H2 `heading`, `role`, `form_factor`,
  `applicability`, and `primary_inventory_item_ids`. Use `rationale` when applicability is
  `not_applicable`.

Allowed roles are `executive_synthesis`, `session_context`, `speaker_map`, `topic_analysis`,
`operational_actions`, `open_questions`, `evidence_appendix`, and `external_evidence`. Allowed
form factors are `prose`, `grouped_bullets`, `table`, `checklist`, `timeline`,
`definition_list`, `mixed`, and `source_list`.

Roles constrain document function, not subject matter. Never preseed domain topics, product
names, or feature headings in the skill or template. Derive every topic heading from the current
recording and inventory; use the structural gate only to keep closely related findings together.

The Markdown must implement the blueprint exactly:

- put `문서 유형: ...` or `Document type: ...` immediately after the H1 and render every
  front-matter record as `- label: value` before the first H2;
- include exactly one executive synthesis with at least three grouped bullets;
- assign every required inventory item to exactly one primary section;
- use no more than six `topic_analysis` H2 groups; place related detail under H3 instead of
  promoting each narrow feature or question to a new H2;
- when speaker-role evidence exists, use one `speaker_map` section and a table or definition
  list rather than burying role attribution in prose;
- when decisions, policies, follow-ups, availability, IAM, retention, or roadmap evidence
  exists, use one actionable checklist/table with at least three entries;
- always include one open-questions section. Mark it `not_applicable` only when the inventory
  truly contains no unresolved item and repeat the concrete rationale in the section;
- when official sources were consulted, keep `external_evidence` as the final H2.

The useful reference-quality pattern is cover and document type → readable evidence metadata →
executive synthesis → optional speaker/topic map → a few deep topic groups → operational
checklist/timeline → unresolved verification → external evidence. This is a functional pattern,
not a fixed title list, word count, or page target.

Keep audit traceability in `content_inventory.json`, `content_audit.json`, and the evidence
appendices. In the reader-facing body, raw `STT:`, `OCR:`, and `Snapshot:` strings are exceptions
for genuinely contested claims, not paragraph suffixes. The deterministic allowance is the
larger of two references or two per recorded source conflict; evidence appendices are excluded.

## 4. Choose the document archetype and form factors from the recording

Match form to content. A product demo normally needs feature flow, observed UI behavior,
activation and permission requirements, operational constraints, Q&A, limitations, and
follow-ups. A technical decision record needs alternatives, trade-offs, decisions, owners,
open risks, and actions. A strategy session needs narrative, roadmap, objections, and field
guidance.

Use tables only for comparable rows. Use timelines, checklists, decision grids, or grouped
bullets when they improve retrieval. Keep the executive summary concise without compressing
the substantive body. Integrate timestamp evidence naturally; avoid repeating raw
`근거: STT... OCR... Snapshot...` lines after every paragraph when compact citations or a
dedicated evidence structure reads better.

## 5. Run an adversarial draft review

After writing `minutes.md`, compare it against the ledger and inventory. Review these failure
classes: `overcompression`, `inventory_granularity`, `question_answer_retention`,
`conditions_exceptions_risks`, `document_archetype_fit`, `evidence_citation_usability`,
`visual_evidence_plan`, and `reader_usability`.

Create `content_quality_review.json` with:

- `schema_version=3`, `status=passed`;
- one or two sequential `review_cycles`, each with `cycle`, `status` (`passed` or
  `revised`), `findings`, and `changes`;
- `final_checks`, containing only the eight model-judged failure classes above, each with
  `status=passed` and one concise concrete `finding`.

Do not calculate hashes, reviewed chunk indexes, inventory counts, or Markdown signals. After
the review passes, run `python scripts/content_freeze.py <job-directory>`. It fills those
deterministic bindings, reruns the complete content gate, and writes `content_freeze.json`.
If a check fails, make one targeted revision and repeat the review. A third cycle is allowed only
for a named blocking defect and must set `blocking_defect_code`; otherwise fail rather than chase
diminishing-return edits. A short document can pass only when the ledger shows that the recording
itself is low-information; brevity is not evidence of quality.

For screen-driven content, explicitly decide whether selected snapshots should be embedded,
referenced, or omitted. Base the choice on reader value and legibility, not snapshot count.
Before final-folder verification, identify the substantive-content boundary from the cumulative
ledger and remove only unreferenced archived tail images that are lock screens, unrelated
application/browser activity, or private desktop material. Preserve every reader reference and
verify that each retained Snapshot reference resolves; full STT/OCR coverage, not raw image count,
is the completeness criterion.

## 6. Freeze, optionally translate once, and finish with DOCX render QA

After `content_freeze.json` verifies, compare the explicit output language with the detected source
language. If they differ, run one isolated low-reasoning translation-only turn. Give it only frozen
`minutes.md`; it must use no tools and return only `minutes.translated.md`. Do not provide STT, OCR,
Snapshots, evidence chunks, ledger, inventory, audit text, or quality-review prose. Translation is
not another analysis or review: do not summarize, fact-check, add, omit, restructure, or run a
second model critique. Bind the target to the source freeze with `translation_manifest.json` and
deterministically verify heading/list/table structure, protected references, URLs, code, timecodes,
and source numeric values. Skip the phase when source and target languages already match.

Start a separate fresh delivery session only after the freeze and optional translation manifest
verify. It may read only the validated final Markdown, blueprint, manifests, DOCX instructions, and
latest page renders. Both the source-frozen and final Markdown are immutable; visual QA may edit
only DOCX layout. Route a genuine semantic problem back as an explicit blocker instead of silently
rewriting content.

Run `python scripts/finalize_docx.py prepare <job-directory>` to generate, clean, render, and
structurally audit in one bounded command. Inspect every latest page PNG at 100%. Revise for
blocking defects only: clipping/overlap, missing glyph or content, blank interior page, broken
TOC/bookmark, unreadable table, or orphan heading/split row. Accept a short final page, whitespace
on one TOC page, intentional section whitespace, and mild readable wrapping as warnings when
readability is intact.

The first correction uses `prepare --reuse-final`. A third render requires an explicit supported
`--blocking-defect-code`; warnings alone never authorize it. Write `visual_review.json` with every
inspected page, no blocking defects, and only supported warning codes, then run
`python scripts/finalize_docx.py approve <job-directory>`. The approval command binds the latest
DOCX and render hashes into `docx_qa.json`; archive only after it passes.
One additional renderer-repair render is allowed only after the renderer fingerprint changes and
with a supported blocking defect; the same renderer cannot exceed the normal three attempts.
