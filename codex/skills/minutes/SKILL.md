---
name: minutes
description: "Use when processing local video or audio with the minutes repo: ffmpeg and MLX Whisper STT, video OCR and selected snapshots, evidence-only speaker identification, source-language content freeze, optional one-pass translation, Word delivery, archival, and artifact verification."
---

# minutes

Use this skill to turn a local video or audio file into a content-driven document with this
repository. Do not assume the input is a meeting.

## Core Contract

- Default input is `~/remind`; default work/output root is `~/minutes`.
- Supported extensions are `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`,
  `.flac`, and `.ogg`.
- Preserve the detected source language in STT and OCR. `OUTPUT_LANGUAGE=auto` keeps the final
  document in that language. When an explicit `ko` or `en` target differs from the detected
  language, author and freeze `minutes.md` in the source language, then translate that completed
  Markdown exactly once in an isolated low-reasoning turn. Translation must not reread evidence,
  summarize, reanalyze, fact-check, restructure, or trigger another model review.
- Derive the title, document type, and sections from the actual content. Do not hardcode
  `회의록`, `Meeting Minutes`, `영상 요약`, or meeting-only sections. Do not impose fixed
  word, token, page, or bullet targets. Use the quality-loop blueprint gate to reject avoidable
  top-level fragmentation while preserving content-driven H3 depth.
- Use `SPEAKER_ATTRIBUTION_MODE=evidence` and
  `SPEAKER_ATTRIBUTION_REQUIRED=false`. `audio` and `hybrid` are forbidden in the automatic
  workflow.
- Keep `SPEECH_ACTIVITY_VALIDATION_ENABLED=true` unless the user explicitly disables the
  optional validation pass.
- Keep audio extraction and MLX Whisper STT. A single-thread Silero ONNX pass may validate
  speech presence, but it must never assign speakers, alter the transcript automatically,
  or become speaker-identity evidence. Never run or retry ECAPA/pyannote diarization, even
  when screen evidence is absent or weak. Never force a speaker count.
- For speaker identity, use only timestamped STT, timestamped OCR, and the few selected
  Snapshots needed to resolve a conflict or transition. Do not assume a video service,
  color, border, or fixed layout. A participant list, screen-sharer label, or a name merely
  visible on screen is not enough to identify the current speaker.
- With no usable screen evidence, accept only explicit STT evidence such as a self-
  introduction, direct naming followed by a response, or a clear handoff. If evidence is
  weak or conflicting, use `화자 미상`/`unknown speaker`; do not invent a name or speaker
  count. Never omit the underlying content merely because speaker identity is unresolved.
- Verify `speaker_attribution_report.json` records
  `local_audio_diarization=disabled_by_policy`, and verify `process_metrics.json` contains
  `validate_speech_activity` but no `diarize` or `attribute_speakers` stage.
- Final output goes under `~/minutes/output/<recording-date>_<content-title>/`, so every
  folder is content-identifiable at the output root. Prefer a valid date in the original
  filename; otherwise use media mtime.
- Keep only the renamed media, final `.md`, final `.docx`, and meaningful `snapshots/` in
  the final folder. Keep `docx_qa.json`, transcript, OCR, speaker evidence report, audit
  artifacts, status, and diagnostics in `~/minutes/jobs/<job_id>/` only through parent-side
  performance/quality evaluation. `COMPLETED_JOB_RETENTION_HOURS=0` is the default; the next
  cleanup purges a completed job only after final-artifact verification. Set a positive retention
  value only when an intentional rework window is required.
- Before final-folder verification, derive the end of substantive content from the cumulative
  evidence. Remove only unreferenced archived Snapshots after that boundary when they show a
  lock screen, unrelated application/browser activity, or a private desktop. Preserve every
  reader-referenced Snapshot and every image needed to support substantive content, then verify
  that all final Markdown Snapshot references resolve. Snapshot count is not the coverage gate;
  the complete timestamped STT/OCR ledger and selected-image hashes are.
- For a direct child of `~/remind`, move the source through the job into final output after
  successful local analysis; leave no duplicate in the inbox. Preserve external originals
  and move only their job copy.
- Do not send media, transcript, OCR, or snapshots outside the machine except to the LLM
  provider configured by the user. Official-source web searches may contain only minimal
  public product/version/policy queries, never raw evidence, participant/customer names,
  internal identifiers, or secrets.
- Remove reproducible `audio.wav` immediately after STT when cleanup is enabled. Retain raw
  OCR frames, their SHA-256 manifest, and selected Snapshots through content audit, DOCX render
  review, archive verification, and the completed-job retention window; only the verified
  completed-job purge removes them.
- Use `OCR_WORKERS` for bounded frame-level parallelism and keep
  `OCR_TESSERACT_THREAD_LIMIT=1`. Apply parallel results in timestamp order so visual/text
  dedupe and Snapshot numbering remain deterministic. Respect the configured worker value;
  do not derive or change it from a machine-specific CPU benchmark.
- For video, read the bounded `evidence_coverage_summary.json` before inventory authoring. Do not
  open or print the full `evidence_coverage.json`; deterministic validators read that raw ledger
  internally. Require complete raw-frame accounting, valid raw/Snapshot hashes, and a maximum selected-Snapshot gap no greater than
  `OCR_MAX_SNAPSHOT_GAP_SECONDS` (default 120). Review `visual_only`, `speaker_ui_change`, and
  `forced_coverage` frames when material. Use resolvable refs in the exact forms
  `STT:HH:MM:SS-HH:MM:SS`, `OCR:HH:MM:SS`, and
  `Snapshot:snapshot-NNNN@HH:MM:SS`. A required item supported only by visual evidence must
  include a Snapshot ref.
- With `CONTENT_AUDIT_MODE=strict`, create `content_inventory.json` before drafting and
  `content_audit.json` after drafting. Preserve dates, versions, quantities, ranges, units,
  conditions, exceptions, negation, limitations, Q&A, and source conflicts. Do not archive
  until the audit passes.
- `references/quality-loop.md` is the maintenance source for strict-job quality rules. The
  launcher compiles its required evidence-ledger, reader-facing blueprint, and adversarial review
  rules into the compact content prompt; a fresh worker must not reopen that reference. Create
  hash-bound `evidence_ledger.json`, `document_blueprint.json`, and
  `content_quality_review.json`; the strict archive gate rejects a missing chunk, an unmapped
  required inventory item, an over-fragmented or citation-noisy reader document, a failed final
  check, or stale hashes.
- If evidence exceeds one context, read it sequentially by timestamp into one cumulative
  inventory. Do not create lossy section summaries and summarize them again.
- Keep the preprocessing conversation out of document synthesis. Outside a fresh worker,
  run local preprocessing only, do not open the full transcript, OCR, or Snapshots, and hand
  the prepared job to `./scripts/run_fresh_codex_job.py`. The launcher starts an isolated
  `codex exec --ephemeral` content phase and an isolated delivery phase. It inserts one isolated translation-only phase between
  them only when an explicit target language differs from the detected source. Content and delivery
  default to reasoning effort `high`; translation defaults to `low`. Raw evidence is available only
  to the content worker and is never embedded in a handoff prompt. Use an explicit CLI override only
  for a controlled comparison.
- A fresh content worker, identified by its prompt and `MINUTES_FRESH_CONTEXT=1`, must not launch
  another worker. It reads every byte-bounded, non-overlapping part in `evidence_chunks.json`
  exactly once in manifest order. Manifest line ranges are coordinates in the unsplit source, so
  read each part as a whole and never reuse its path in another command. The launcher terminates a
  duplicate part read. The compact prompt carries exact ledger/inventory/blueprint/audit/review
  fields and enums; write large artifacts in one multi-file patch and run the absolute repository
  `.venv/bin/python` freeze command without validator-source inspection. Then complete inventory, drafting, audit, compact quality
  review, and `content_freeze.json` in the source language when translation is required. It must
  not create a DOCX or archive. The optional translation turn receives only frozen `minutes.md`,
  writes `minutes.translated.md` as its final response without tools, and is accepted only after
  `translation_manifest.json` binds the source freeze and target hash while verifying Markdown
  structure and protected literals. The delivery worker must not read STT, OCR, evidence chunks,
  ledger, inventory, or audit prose; it verifies the freeze and optional translation manifest,
  performs DOCX-only finalization, archives, and verifies. Valid content and translation artifacts
  are reused after delivery failure so evidence and translation are not repeated.
- Treat a fresh worker as a production media job, not repository development. Its preloaded phase
  prompt is the complete operational contract. It must not invoke a skill or open `SKILL.md`,
  `quality-loop.md`, `docx-validation.md`, or another instruction file. Do not inspect validator
  implementations or tests unless a validator fails with an error that the phase contract cannot resolve.
  Do not run repository-wide compilation, test, lint, or git-review commands during a media job;
  deterministic artifact gates and launcher post-verification are the acceptance path.
- Never concatenate files in a worker command. Target at most 16KB of model-visible output per
  tool item. `run_fresh_codex_job.py` counts command output and file-change diffs; it terminates the
  current phase on the first item over 20KB or any worker attempt to read the full instruction
  files above. It also terminates a duplicate evidence-part read. The 20KB limit is fail-closed,
  not truncation followed by continuation. Target at most 50 content and 25 delivery tool calls as
  a cost objective without skipping evidence or all-page QA. `fresh_codex_handoff.json` must report
  `worker_contract_passed=true`, zero oversized tool outputs, zero forbidden instruction reads,
  and zero duplicate evidence chunk reads.
- Read `worker_runtime_summary.json` for preprocessing, resource, speaker-policy, and speech-
  validation facts. Do not print the full `status.json`, `process_metrics.json`,
  `speaker_attribution_report.json`, or `speech_activity.json`; deterministic validators may read
  those raw files without returning their large arrays to the model.
- The recording remains the source of truth for what was said. With
  `OFFICIAL_SOURCE_VERIFICATION=auto`, inspect local audio context, timestamped STT, OCR,
  and relevant Snapshots first. Use current official documents only to clarify remaining
  ambiguity or disclose a conflict; never rewrite a clear recorded statement.
- When external sources are used, append the final `## 외부 근거 확인` or
  `## External Evidence Check`, separating supporting transcription/OCR evidence from
  evidence that conflicts with the video. Include timestamp, purpose, finding, checked
  date, and official links. No H2 section may follow it.

## Standard Commands

Activate the repository environment and process a media file:

```bash
source .venv/bin/activate
python scripts/process_file.py "~/remind/<video>.mov"
python scripts/process_file.py "~/remind/<audio>.m4a"
```

For Codex-authored output:

```bash
LLM_PROVIDER=codex python scripts/process_file.py "~/remind/<video>.mov"
./scripts/run_fresh_codex_job.py "<prepared-job-directory>"
```

The parent session stops reading evidence after `process_file.py` prints the prepared job.
If the user supplied a short instruction that is not already represented by
`OUTPUT_LANGUAGE` or the job policy, pass only that instruction to the isolated worker:

```bash
./scripts/run_fresh_codex_job.py \
  "<prepared-job-directory>" \
  --request "Report total wall time and validation results"
```

Inside the content worker, read `evidence_coverage_summary.json`, then every byte-bounded path in
`~/minutes/jobs/<job_id>/evidence_chunks.json` exactly once in order, one file per tool call, and only the selected
Snapshots necessary for evidence resolution. In strict mode, write `content_inventory.json`,
`evidence_ledger.json`, `document_blueprint.json`, `official_sources.json`, `minutes.md`,
`content_audit.json`, and `content_quality_review.json` in the quality-loop order. The blueprint
must make the cover document type, evidence metadata, functional H2 roles, primary inventory
placement, form factors, operational utility, and reader-facing evidence placement explicit.
Write only the eight model-judged schema-v3 checks, then run:

```bash
python scripts/content_freeze.py "<job-directory>"
```

The command fills hashes, reviewed chunk indexes, and document signals deterministically and
reruns the complete content gate. When translation is required, the launcher then starts one
low-reasoning, tool-free translation turn from frozen `minutes.md` and validates it with:

```bash
python scripts/translation.py "<job-directory>" --verify
```

This produces `minutes.translated.md` plus hash-bound `translation_manifest.json`. It performs no
second content review and never reads STT, OCR, inventory, audit, ledger, or Snapshots. If source
and target languages already match, this phase is skipped. The launcher then starts delivery.

For `DOCX_ENABLED=true`, the delivery worker receives a compact preloaded contract instead of
reading the full minutes and Documents skill files. `finalize_docx.py` applies the
`standard_business_brief` preset and delegates rendering to the newest bundled Documents skill
`render_docx.py`. The launcher disables Documents plugin injection inside fresh workers only; it
does not remove the installed renderer used by the deterministic script. Generate a deterministic job-local draft, render into a clean directory, and run
structural QA with one command:

```bash
python scripts/finalize_docx.py prepare "<job-directory>"
```

Inspect every latest page PNG at 100%. The source-frozen Markdown and validated final Markdown must
not change for pagination. Revise
only blocking layout defects and rerender once with `prepare --reuse-final`; warnings alone do not
authorize another render. A third render requires an explicit supported `--blocking-defect-code`.
The three-render production limit remains. One extra renderer-repair render is allowed only when
the renderer fingerprint changed and a supported blocking defect is named; it cannot loop on the
same renderer.
After all pages pass, write compact `visual_review.json` and bind it deterministically:

```bash
python scripts/finalize_docx.py approve "<job-directory>"
python scripts/archive_job.py "<job-directory>"
```

The structural gate rejects unsupported Markdown leakage, internal Codex citation tokens, body
fake lists, preset drift, broken TOC bookmarks, and inconsistent table DXA geometry. Numbered stage
labels inside table cells are not body-list failures. The archive step copies
`minutes.final.docx`; it must not regenerate or overwrite it.

The H1 becomes the display title, output directory, and renamed media stem.

`fresh_codex_handoff.json` records individual core evidence file hashes plus bounded aggregate
counts, byte totals, and combined manifest hashes for Snapshot/raw-frame directories. It also
records separate content/translation/delivery prompt hashes, phase elapsed time, tokens, tool calls,
`parent_conversation_inherited=false`, and `raw_evidence_embedded_in_handoff=false`.
`context_efficiency` records aggregate uncached input, cache ratio, input/output ratio, and bounded
command-output pressure. Compare phase records on the next run; they do not replace strict content
inventory and post-draft audit.
`cached_input_tokens` is cumulative API prompt-cache accounting, not local disk-cache growth. A
high cached ratio together with excessive tool calls means repeated growing context; clearing a
local model cache does not solve it and can increase uncached cost.

When the parent itself is running inside the macOS Codex seatbelt, launch
`./scripts/run_fresh_codex_job.py` with initial `sandbox_permissions=require_escalated`.
Use only the exact launcher prefix for reusable approval. The launcher validates that the job
is a direct child of configured `~/minutes/jobs`, then starts the worker with its own
`workspace-write` sandbox limited to the repository and configured minutes root.

## Expected Output Layout

```text
~/minutes/output/YYYY-MM-DD_내용-기반-제목/
  YYYY-MM-DD_내용-기반-제목.mov 또는 YYYY-MM-DD_내용-기반-제목.m4a
  YYYY-MM-DD_내용-기반-제목.md
  YYYY-MM-DD_내용-기반-제목.docx
  snapshots/
    snapshot_0001_00-00-00.jpg
```

## DOCX Requirements

Generate the job-local DOCX through `scripts/finalize_docx.py` when `DOCX_ENABLED=true`, use the
bundled Documents renderer, and apply the compact visual shipping contract. A Codex or strict job without a valid
content freeze, hash-matching `minutes.final.docx`, latest rendered pages, complete
`visual_review.json`, and passed `docx_qa.json` must not archive.

On macOS Codex, never probe LibreOffice from `CODEX_SANDBOX=seatbelt`; it can abort and
leave repeated crash dialogs. Run the project guard with initial escalation:

```bash
python scripts/render_docx_checked.py \
  "/absolute/path/to/final.docx" \
  --output_dir /private/tmp/minutes-docx-render \
  --emit_pdf
```

The DOCX needs a content-derived cover, static linked TOC with matching bookmarks,
language-appropriate styling, explicit table geometry, repeating table headers, practical
row non-splitting, and footer page numbers. When H2/H3 navigation is dense, keep every body
heading but collapse the visible TOC to top-level content headings so it does not create a mostly
blank spill page. Render Markdown task items as `☐`/`☑`, never literal `[ ]`/`[x]` text.

## Verification

### Per-media production verification

For a normal fresh-context media job, run only the configured content audit, content-quality
review, DOCX render/QA, archive gate, and final-folder checks described above. Do not run
repository-wide `py_compile`, `unittest discover`, `pytest`, lint, static analysis, or git review.
Do not inspect validator implementations or tests unless a validator actually fails and its
bounded error cannot be resolved from this skill. Keep full command output in job-local logs when
available and return only exit status, hashes, counts, and bounded failure details to the model.

Render every DOCX page and inspect the cover, TOC, tables, and final page. Verify TOC links,
bookmarks, table geometry, final-folder contents, source move semantics, strict audit coverage,
`content_freeze.json`, an optional `translation_manifest.json`, `docx_qa.json` hashes/status, and
retained job evidence. Specifically verify
`speech_activity.json` is validation-only, `local_audio_diarization=disabled_by_policy`, and no
diarization stage appears in metrics. For Codex mode, verify `fresh_codex_handoff.json` reports
isolated content and delivery phases, plus translation only when required; no parent conversation
inheritance; no raw evidence outside content; matching input/Snapshot/final-Markdown hashes; and
`worker_contract.mode=preloaded_compact`; zero forbidden instruction reads and outputs over 20KB;
and `state=completed`. A zero Codex exit code without completed
`status.json` and real archived artifacts is a failure.

### Repository change verification

Only after modifying the repository code, this skill, or tests, run the full regression checks:

```bash
python -m py_compile scripts/*.py
python -m unittest discover -s tests -v
```

When comparing a reprocessed document with an earlier result, assess speaker-name evidence,
unsupported attribution, retained factual content, omissions, section structure, wall time,
stage time, and disk growth. A conservative unknown speaker is better than an unsupported
name, but unresolved identity must never cause content loss.

## Common Fixes

- `audio`/`hybrid` mode rejected: use `SPEAKER_ATTRIBUTION_MODE=evidence`; basic audio
  extraction and STT still run.
- Whisper cache is shared by model content, not created per video. With
  `HF_HUB_OFFLINE=1`, a missing model fails instead of downloading.
- Slow Apple Silicon transcription: keep `WHISPER_DEVICE=gpu` for MLX Metal or select a
  smaller Whisper model.
- `soffice` crashes in the macOS sandbox: use `scripts/render_docx_checked.py` through an
  initially escalated command.
- `failed to initialize in-process app-server client`: the fresh launcher was started inside
  the parent seatbelt. Re-run the exact `./scripts/run_fresh_codex_job.py` command with initial
  escalation; do not remove the child worker's `workspace-write` sandbox.

## References

Read `references/docx-validation.md` when changing DOCX generation or testing DOCX output.
Read `references/quality-loop.md` when maintaining the compact content contract. Fresh media
workers receive the compiled rules in their prompt and must not reopen either reference.
