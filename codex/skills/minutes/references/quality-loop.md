# Ultra-derived content quality loop

Use this workflow for every fresh-context strict Codex job. It captures the useful part of
iterative Ultra work: evidence bookkeeping, adversarial review, bounded revision, and a
verifiable stop condition. Do not copy reference-document word or page counts as targets.
There is no maximum word, character, token, bullet, section, or page count for the reader document;
all density checks below are minimum completeness gates.

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

- `document_archetype`: one of `meeting_minutes`, `technical_session_analysis`, `product_demo_analysis`,
  `technical_decision_record`, `strategy_session_analysis`, or
  `general_recording_analysis`;
- `document_type`, a concise `reader_goal`, and `writing_style`, which is
  `meeting_minutes_objective` for actual meetings and `content_adaptive` otherwise;
- `front_matter`, with exact `key`, `label`, and `value` records for
  `recording_datetime` and `duration`, plus only reader-useful fields such as purpose or
  participants when evidence supports them. Do not add source/output language, evidence-basis,
  external-policy, model, skill, token, preprocessing, rendering, QA, hash, or internal-path fields;
- ordered `sections`, each with a unique `id`, exact H2 `heading`, `role`, `form_factor`,
  `applicability`, and `primary_inventory_item_ids`. Use `rationale` when applicability is
  `not_applicable`.
- `visual_evidence_plan`, with `status`, `rationale`, and `items`. Each item uses
  `snapshot_path`, `section_id`, `purpose`, and `reader_value`.

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
- for `meeting_minutes`, summarize rather than transcribe dialogue. Center the document on agenda
  and context, key discussion, decisions and agreements, action owners and deadlines, follow-ups,
  unresolved items, and risks. Korean minutes must use concise objective report prose with
  consistent endings such as `~함`, `~하기로 함`, `~예정임`, and `~필요함`;
- for every non-meeting archetype, use `content_adaptive` and choose a professional voice suited
  to the actual document type. Do not force meeting-minutes endings onto analyses, demos, guides,
  briefings, or training notes;
- include exactly one executive synthesis with at least three grouped bullets;
- assign every required inventory item to exactly one primary section;
- use no more than six `topic_analysis` H2 groups; place related detail under H3 instead of
  promoting each narrow feature or question to a new H2;
- when speaker-role evidence exists, use one `speaker_map` section and a table or definition
  list rather than burying role attribution in prose;
- when decisions, policies, follow-ups, availability, IAM, retention, or roadmap evidence
  exists, use one actionable checklist/table with at least three entries;
- always include one `open_questions` section using exactly
  `추가 검증이 필요한 항목` or `Items Requiring Further Verification`. Mark it
  `not_applicable` only when the inventory truly contains no unresolved item and repeat the
  concrete rationale in the section;
- when official verification is enabled, always include one `external_evidence` section using
  exactly `외부 근거 확인` or `External Evidence Check`, even when no claim required an
  external lookup. Keep `open_questions` and `external_evidence` as the final two H2s in that
  order. A `not_applicable` external section must display the exact sidecar reason, checked date,
  recording-first rule, and privacy non-transmission statement. A completed external section
  adds the checked official links and separates recording support from video conflicts.
  Record `checked_at` as the actual timezone-aware check time, never a planned or future time;
  the freeze clock corrects only a same-day future skew and rejects other future dates.

Use `visual_evidence_plan.status=embedded` only for 3-5 distinct core Snapshots that materially
improve comprehension or verification. Keep their plan order identical to Markdown order, place no
more than two full-width images in one H2, never place full-width images adjacently without
substantive reader content between them, and keep substantive content after the last image. Use
`limited` only when fewer than three selected Snapshots exist. Use `not_applicable` with a concrete
rationale when no Snapshot adds reader value; image count is never a substitute for evidence
coverage.

The useful reference-quality pattern is cover and document type → useful reader metadata →
executive synthesis → optional speaker/topic map → a few deep topic groups → operational
checklist/timeline → unresolved verification → external evidence. This is a functional pattern,
not a fixed title list, word count, or page target.

In `auto` mode, mark unresolved publicly verifiable product support, version, release/EOL,
policy, security, and API claims as `official_verification=required` after local STT/OCR/Snapshot
cross-checking. A presenter explanation or estimate remains a qualifier to preserve, not a reason
to skip the check. Internal decisions and POC measurements remain local verification items when
no authoritative public source applies. Numbering stays dynamic; only the final two functional
headings and their order are fixed.

Keep audit traceability in `content_inventory.json`, `content_audit.json`, and other internal
sidecars. Never expose raw `STT:`, `OCR:`, or `Snapshot:` strings in the reader document, including
appendices. Do not mention internal artifact filenames, job paths, hashes, model/tool/token usage,
preprocessing stages, render attempts, or QA mechanics. Use natural reader-facing captions for
embedded images and describe privacy as recording-content non-transmission, not as an STT/OCR
pipeline report.

## 4. Choose the document archetype and form factors from the recording

Match form to content. A product demo normally needs feature flow, observed UI behavior,
activation and permission requirements, operational constraints, Q&A, limitations, and
follow-ups. A technical decision record needs alternatives, trade-offs, decisions, owners,
open risks, and actions. A strategy session needs narrative, roadmap, objections, and field
guidance.

Use tables only for comparable rows. Use timelines, checklists, decision grids, or grouped
bullets when they improve retrieval. Keep the executive summary concise without compressing
the substantive body. Keep timestamp traceability in internal sidecars; never render raw
`근거: STT... OCR... Snapshot...` lines in the reader document.

## 5. Run an adversarial draft review

After writing `minutes.md`, compare it against the ledger and inventory. Review these failure
classes: `overcompression`, `inventory_granularity`, `question_answer_retention`,
`conditions_exceptions_risks`, `document_archetype_fit`, `evidence_citation_usability`,
`visual_evidence_plan`, and `reader_usability`.

`document_archetype_fit` includes voice fit: actual meetings use objective minutes and Korean
report endings, while non-meeting documents use a type-appropriate professional voice.

Create `content_quality_review.json` with:

- `schema_version=3`, `status=passed`;
- one or two sequential `review_cycles`, each with `cycle`, `status` (`passed` or
  `revised`), `findings`, and `changes`;
- `final_checks`, containing only the eight model-judged failure classes above, each with
  `status=passed` and one concise concrete `finding`.
- `required_item_checks`, containing every required inventory item exactly once in its primary
  blueprint section. Each object uses `item_id`, `section_id`, and `dimensions` (not `checks`,
  `inventory_item_id`, or `primary_section_id`). `dimensions` contains exactly `core_facts`,
  `conditions_exceptions`, `risks_limitations`, `impact`, and `actions_decisions`.
  `core_facts` must be `covered`; each other dimension must be either `covered` with a short
  verbatim section reference or `not_applicable` with a concrete rationale. A covered reference
  must be an exact substring of the assigned primary H2, including Markdown markers where used;
  text that appears only in a separate action or question H2 does not satisfy the check.

Do not calculate hashes, reviewed chunk indexes, inventory counts, or Markdown signals. After
the review passes, run `python scripts/content_freeze.py <job-directory>`. It fills those
deterministic bindings, reruns the complete content gate, and writes `content_freeze.json`.
If the pre-freeze adversarial check fails, revise only the failed sections once while authoring the
artifacts. Cycle 1 is `revised` and owns non-empty
`findings`, `changes`, and `target_section_ids`; cycle 2 is `passed` with empty findings and
changes. A third content cycle is forbidden.
Run the freeze exactly once in the evidence-reading content turn. After a successful content-model
turn, the launcher writes a prompt/hash-bound `content_generation_checkpoint.json`. If the freeze
fails, stop that worker without rereading evidence, editing artifacts, or retrying the freeze. The
launcher starts at most one isolated repair turn from the checkpoint. It receives the bounded
validator error, may inspect only the named content-sidecar entries and implicated H2 excerpts, and
must never read media, transcript, OCR, evidence chunks, Snapshots, validator source, tests, or web
results. It writes only bounded `content_repair_patch.json`, applies it through
`scripts/apply_content_repair_patch.py`, and runs the freeze once. The helper validates every update
before writing, accepts only model-owned audit/review/blueprint/official-source fields plus exact
single-occurrence Markdown replacements, and emits counts instead of a large diff. Never use a raw
parent-side `codex exec` repair.

If that single repair fails, retain the checkpoint and stop. Do not automatically repeat the
multi-million-token content turn. An operator must use `--force-content-rebuild` to explicitly
discard generated content and authorize a new evidence read.

The deterministic signals emit `LOW_INFORMATION_DENSITY` for a long recording whose reader body is
unusually sparse. Treat it as an omission warning, not a word-count target: revisit only the named
sections against the ledger, add evidence-backed specificity, and never add filler. The first
warning writes validator-owned `content_density_baseline.json`; its target selection excludes
evidence/external appendices and ranks substantive sections by information per required item. The
single repair must use exactly those section IDs and increase the information characters of every
target before the final pass. The baseline requires a minimum gain that closes 80% of the warning
deficit, with that mandatory gain bounded to 400-1,800 information characters and at least 120 per
target. The 1,800 value caps only what the validator requires in one repair; it never caps actual
section or document length. Preserve every exact substring already
used by `content_audit.json` or `required_item_checks`; prefer additive paragraphs/tables, or update
all affected references in the same patch when a cited sentence must change. A short
document can pass after that bounded review only when the ledger shows that the recording itself is
low-information; brevity alone is not evidence of quality.

Exact-reference repairs use the same compact content-repair patch surface; never direct-patch the
large finalized audit/review JSON. Do not ask the repair worker to edit `review_cycles` for a density
warning. After the target and total
gains pass, `content_freeze.py` records the deterministic revised/pass cycles and measured section
changes itself.

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

For every fresh Codex phase, a 10-minute JSON-event silence emits a heartbeat and a 15-minute
silence terminates the phase with exit code 80. Treat this as a model/CLI stream stall, not as an
STT/OCR CPU failure; retained phase logs make a clean retry diagnosable.

Run `python scripts/finalize_docx.py prepare <job-directory>` to copy and fill the retained Word
template, clean the render directory, render, and structurally audit in one bounded command. Inspect
every latest page PNG at 100%. The first full render remains mandatory because Word pagination,
tables, and images can change when the template slots are filled. Treat clipping/overlap, missing
glyph or content, blank interior page, broken TOC/bookmark, unreadable table, orphan heading/split
row, excessive interior layout gaps, adjacent large images, and image placement drift as blocking
defects. `NATURAL_FINAL_PAGE_WHITESPACE`, single-page TOC whitespace, intentional section whitespace,
and mild readable wrapping may remain as warnings.

Last-page occupancy is diagnostic only. Never add filler, delete complete content, or reflow a
document merely to change final-page whitespace. A warning alone cannot trigger another render.

The first correction uses `prepare --reuse-final`. A third render requires an explicit supported
`--blocking-defect-code`; warnings alone never authorize it. Write `visual_review.json` with every
inspected page, no blocking defects, and only supported warning codes, then run
`python scripts/finalize_docx.py approve <job-directory>`. The approval command binds the latest
DOCX and render hashes into `docx_qa.json`; archive only after it passes.
One additional renderer-repair render is allowed only after the renderer fingerprint changes and
with a supported blocking defect; the same renderer cannot exceed the normal three attempts.
