---
name: minutes
description: "Use when processing a local video or audio file with the minutes repo: local ffmpeg and MLX Whisper STT, optional video OCR and selected snapshots, evidence-only speaker identification without local audio diarization, content-driven Codex document generation, archival, and artifact verification."
---

# minutes

Use this skill to turn a local video or audio file into a content-driven document with this
repository. Do not assume the input is a meeting.

## Core Contract

- Default input is `~/remind`; default work/output root is `~/minutes`.
- Supported extensions are `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`,
  `.flac`, and `.ogg`.
- Preserve the detected source language in STT and OCR. `OUTPUT_LANGUAGE=auto` preserves
  it, `ko` requests direct Korean synthesis, and `en` requests direct English synthesis.
  Never translate a completed intermediate document and summarize it again.
- Derive the title, document type, and sections from the actual content. Do not hardcode
  `회의록`, `Meeting Minutes`, `영상 요약`, or meeting-only sections. Do not impose a hard
  word, token, page, bullet, or section-count limit.
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
- Final output goes under `~/minutes/output/<recording-date>/<content-title>/`. Prefer a
  valid date in the original filename; otherwise use media mtime.
- Keep only the renamed media, final `.md`, final `.docx`, and meaningful `snapshots/` in
  the final folder. Keep transcript, OCR, speaker evidence report, audit artifacts, status,
  and diagnostics in `~/minutes/jobs/<job_id>/` for `COMPLETED_JOB_RETENTION_HOURS`
  (default 24), then purge only a completed job whose final artifacts verify.
- For a direct child of `~/remind`, move the source through the job into final output after
  successful local analysis; leave no duplicate in the inbox. Preserve external originals
  and move only their job copy.
- Do not send media, transcript, OCR, or snapshots outside the machine except to the LLM
  provider configured by the user. Official-source web searches may contain only minimal
  public product/version/policy queries, never raw evidence, participant/customer names,
  internal identifiers, or secrets.
- Remove reproducible `audio.wav` immediately after STT when cleanup is enabled. Remove raw
  OCR frames after successful OCR, but retain selected Snapshots through document review.
- Use `OCR_WORKERS` for bounded frame-level parallelism and keep
  `OCR_TESSERACT_THREAD_LIMIT=1`. Apply parallel results in timestamp order so visual/text
  dedupe and Snapshot numbering remain deterministic. Respect the configured worker value;
  do not derive or change it from a machine-specific CPU benchmark.
- With `CONTENT_AUDIT_MODE=strict`, create `content_inventory.json` before drafting and
  `content_audit.json` after drafting. Preserve dates, versions, quantities, ranges, units,
  conditions, exceptions, negation, limitations, Q&A, and source conflicts. Do not archive
  until the audit passes.
- If evidence exceeds one context, read it sequentially by timestamp into one cumulative
  inventory. Do not create lossy section summaries and summarize them again.
- Keep the preprocessing conversation out of document synthesis. Outside a fresh worker,
  run local preprocessing only, do not open the full transcript, OCR, or Snapshots, and hand
  the prepared job to `./scripts/run_fresh_codex_job.py`. The launcher starts a new
  `codex exec --ephemeral` session with only job paths, prepared policy values, and short
  per-job overrides; it never embeds raw evidence in the handoff prompt.
- A fresh worker, identified by its prompt and `MINUTES_FRESH_CONTEXT=1`, must not launch
  another worker. It reads the complete `codex_minutes_input.md` directly from disk and
  completes inventory, drafting, audit, archive, and verification. Do not silently fall back
  to the long parent conversation if isolated execution fails.
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

Inside the fresh worker, read the generated
`~/minutes/jobs/<job_id>/codex_minutes_input.md` completely and only the selected Snapshots
necessary for evidence resolution. In strict mode, write `content_inventory.json`,
`official_sources.json`, `minutes.md`, and `content_audit.json` in that order, then archive:

```bash
python scripts/archive_job.py "<job-directory>"
```

The H1 becomes the display title, output directory, and renamed media stem.

`fresh_codex_handoff.json` records the full evidence file sizes and SHA-256 hashes, Snapshot
count, the small prompt hash, `parent_conversation_inherited=false`, and
`raw_evidence_embedded_in_handoff=false`. These are integrity and boundary checks; they do
not replace the strict content inventory and post-draft audit.

When the parent itself is running inside the macOS Codex seatbelt, launch
`./scripts/run_fresh_codex_job.py` with initial `sandbox_permissions=require_escalated`.
Use only the exact launcher prefix for reusable approval. The launcher validates that the job
is a direct child of configured `~/minutes/jobs`, then starts the worker with its own
`workspace-write` sandbox limited to the repository and configured minutes root.

## Expected Output Layout

```text
~/minutes/output/YYYY-MM-DD/
  내용-기반-제목/
    YYYY-MM-DD_내용-기반-제목.mov 또는 YYYY-MM-DD_내용-기반-제목.m4a
    YYYY-MM-DD_내용-기반-제목.md
    YYYY-MM-DD_내용-기반-제목.docx
    snapshots/
      snapshot_0001_00-00-00.jpg
```

## DOCX Requirements

Generate DOCX when `DOCX_ENABLED=true` with `scripts/docx_report.py`. Also use the bundled
`documents` skill and follow its render-and-inspect contract.

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
row non-splitting, and footer page numbers.

## Verification

Before completion:

```bash
python -m py_compile scripts/*.py
python -m unittest discover -s tests -v
```

Render every DOCX page and inspect the cover, TOC, tables, and final page. Verify TOC links,
bookmarks, and table geometry. Verify final-folder contents, source move semantics, strict
audit coverage, and retained job evidence. Specifically verify `speech_activity.json` is
validation-only, `local_audio_diarization=disabled_by_policy`, and no diarization stage appears in
metrics for either English or Korean input and whether or not screen evidence exists.
For Codex mode, also verify `fresh_codex_handoff.json` reports an ephemeral session, no parent
conversation inheritance, no raw evidence embedded in the handoff, matching input/Snapshot
hashes, and `state=completed`. A zero Codex exit code without completed `status.json` and real
archived source/Markdown files is a failure.

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
