---
name: minutes
description: "Use when processing local video or audio recordings into Korean meeting minutes with the minutes repo: run ffmpeg, mlx-whisper STT, optional video OCR, Codex fallback minutes generation, archive Markdown and DOCX outputs under ~/minutes/output, and verify generated artifacts."
---

# minutes

Use this skill when the user wants to process a local video or audio file into Korean meeting minutes using this repository.

## Core Contract

- Default input folder: `~/remind`.
- Default work/output root: `~/minutes`.
- Supported input extensions: `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`, `.ogg`.
- Final minutes must be Korean. Keep English only for product names, API names, commands, and unavoidable proper nouns.
- Final outputs go under `~/minutes/output/YYYY-MM-DD/<meeting-title>/`.
- Keep raw job artifacts under `~/minutes/jobs/<job_id>/`.
- Do not send source media, transcript, or OCR content outside the machine except to the configured LLM provider requested by the user.

## Standard Commands

Activate the repository venv before running scripts:

```bash
source .venv/bin/activate
```

Process a specific recording with configured OpenAI or OCI provider:

```bash
python scripts/process_file.py "/Users/<user>/remind/<recording>.mov"
python scripts/process_file.py "/Users/<user>/remind/<recording>.m4a"
```

Run Codex mode when the user wants Codex to write the final Korean minutes:

```bash
LLM_PROVIDER=codex python scripts/process_file.py "/Users/<user>/remind/<recording>.mov"
```

Then read `~/minutes/jobs/<job_id>/codex_minutes_input.md`, write Korean `minutes.md` in the same job folder, and archive:

```bash
python scripts/archive_job.py ~/minutes/jobs/<job_id> --title "회의 제목"
```

## Expected User Invocation

When invoked as a Codex skill, accept prompts like:

```text
$minutes Codex 모드로 "/Users/jun/remind/2026-06-18 회의.mov" 내용 정리해줘
$minutes Codex 모드로 "/Users/jun/remind/2026-06-18 회의.m4a" 내용 정리해줘
$minutes "/Users/jun/Desktop/customer-call.mov" 회의록 만들어줘
$minutes "/Users/jun/remind/demo.mp4" CPU와 소요시간도 측정해줘
```

For Codex mode, run the scripts, read the generated `codex_minutes_input.md`, create `minutes.md` in Korean, and archive the job.

## Expected Output Layout

```text
~/minutes/output/YYYY-MM-DD/
  회의-주제/
    YYYY-MM-DD_회의-주제.mov 또는 YYYY-MM-DD_회의-주제.m4a
    YYYY-MM-DD_회의-주제.md
    YYYY-MM-DD_회의-주제.docx
    YYYY-MM-DD_회의-주제.transcript.txt
    YYYY-MM-DD_회의-주제.transcript.json
    YYYY-MM-DD_회의-주제.transcript.srt
    YYYY-MM-DD_회의-주제.screen_text.txt
    YYYY-MM-DD_회의-주제.screen_text.json
    snapshots/
      snapshot_0001_00-00-00.jpg
```

## Verification

Before claiming completion:

```bash
python -m py_compile scripts/*.py
```

For generated output, verify that the final folder contains the source media copy, `.md`, `.docx`, transcript files, OCR text files when available, and `snapshots/` when video OCR produced meaningful images.
