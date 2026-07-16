# Minutes Repository Guidance

## Skill loading

- For media-document work that uses both the `minutes` and `documents` skills, read each
  `SKILL.md` in a separate tool call. Never batch, concatenate, or parallelize the two skill
  reads into one model-visible result.
- Keep every model-visible read at or below 16 KB. If a skill file may exceed that size, read
  deterministic, non-overlapping line ranges in separate calls and continue until EOF before
  starting preprocessing.
- Do not combine a skill read with repository diagnostics, directory listings, or another file
  read in the same tool result.

## Bounded job inspection

- Never recursively list a job directory, `frames/`, `snapshots/`, or rendered pages in
  model-visible output. Use exact contract paths, bounded globs, or a shallow listing limited to
  the files needed for the current check.
- Do not print or concatenate raw evidence ledgers, full transcripts, full OCR data, large JSON,
  or large diffs. Read the bounded summaries and chunks defined by the workflow contract.

## Thermal profile

- Preserve the configured Whisper model for content quality. Do not lower it solely to reduce
  heat unless the user explicitly accepts the accuracy tradeoff.
- Respect the configured OCR worker, FFmpeg thread, and pre-OCR cooldown values. Do not increase
  concurrency automatically from CPU-count heuristics or a one-off benchmark.
