from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings
from scripts.media_types import (
    SUPPORTED_EXTENSIONS,
    is_video_extension,
    supported_extensions_text,
)
from scripts.summarize import generate_minutes, render_markdown
from scripts.utils import (
    file_fingerprint,
    make_job_id,
    now_local,
    read_json,
    safe_filename,
    write_json,
)


def process_file(media_path: Path) -> Path:
    settings = load_settings()
    source = media_path.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension: {source.suffix}. "
            f"Supported: {supported_extensions_text()}",
        )
    has_video = is_video_extension(source.suffix)

    settings.recordings_inbox.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = file_fingerprint(source)
    index_path = settings.jobs_dir / "index.json"
    index = read_json(index_path, {"processed_files": []})
    for item in index.get("processed_files", []):
        if item.get("fingerprint") == fingerprint:
            output_dir = Path(item["output_dir"]).expanduser()
            print(f"already processed: {output_dir}")
            return output_dir

    job_id = make_job_id(source)
    job_dir = settings.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    status_path = job_dir / "status.json"
    logs_path = job_dir / "logs.txt"
    current_step = "starting"

    def status(step: str, state: str = "running", **extra: object) -> None:
        nonlocal current_step
        if state == "running":
            current_step = step
        payload = {
            "status": state,
            "step": step,
            "source": str(source),
            "job_id": job_id,
            "updated_at": now_local().isoformat(),
            **extra,
        }
        write_json(status_path, payload)

    try:
        status("copy_source")
        job_source = job_dir / f"source{source.suffix.lower()}"
        if not job_source.exists():
            shutil.copy2(source, job_source)

        from scripts.transcribe import extract_audio, transcribe_audio

        status("extract_audio")
        audio_path = job_dir / "audio.wav"
        extract_audio(job_source, audio_path, settings)

        status("transcribe")
        transcript_path = job_dir / "transcript.json"
        transcribe_audio(audio_path, transcript_path, settings)
        transcript_text_path = job_dir / "transcript.txt"
        transcript_text = transcript_text_path.read_text(encoding="utf-8")

        status("ocr")
        screen_text = ""
        screen_text_json_path = job_dir / "screen_text.json"
        screen_text_txt_path = job_dir / "screen_text.txt"
        if has_video:
            try:
                from scripts.ocr import run_ocr

                run_ocr(job_source, job_dir, settings)
                if screen_text_txt_path.exists():
                    screen_text = screen_text_txt_path.read_text(encoding="utf-8")
            except Exception as ocr_error:
                write_json(
                    screen_text_json_path,
                    {
                        "enabled": settings.ocr_enabled,
                        "status": "failed",
                        "error": str(ocr_error),
                        "frames": [],
                    },
                )
                screen_text_txt_path.write_text("", encoding="utf-8")
                screen_text = ""
        else:
            write_json(
                screen_text_json_path,
                {
                    "enabled": settings.ocr_enabled,
                    "status": "skipped",
                    "reason": "audio input has no video frames",
                    "frames": [],
                },
            )
            screen_text_txt_path.write_text("", encoding="utf-8")

        if settings.llm_provider == "codex":
            status("awaiting_codex", state="awaiting_codex")
            codex_input_path = job_dir / "codex_minutes_input.md"
            codex_input_path.write_text(
                build_codex_minutes_input(transcript_text, screen_text),
                encoding="utf-8",
            )
            print(f"prepared for Codex: {codex_input_path}")
            return job_dir

        status("summarize")
        minutes = generate_minutes(transcript_text, settings, screen_text=screen_text)
        raw_minutes_path = job_dir / "minutes.raw.json"
        write_json(raw_minutes_path, minutes)

        status("render_markdown")
        markdown = render_markdown(minutes)
        minutes_path = job_dir / "minutes.md"
        minutes_path.write_text(markdown, encoding="utf-8")

        status("archive")
        saved_date = now_local().strftime("%Y-%m-%d")
        title = safe_filename(minutes.get("meeting_title", ""), max_length=80)
        if not title:
            title = safe_filename(source.stem, max_length=80) or "회의록"
        from scripts.archive_job import (
            cleanup_job_ocr_images,
            copy_required,
            copy_snapshots,
            make_meeting_output_dir,
        )

        output_dir = make_meeting_output_dir(settings.output_dir, saved_date, title)
        final_stem = f"{saved_date}_{output_dir.name}"
        source_out = copy_required(source, output_dir / f"{final_stem}{source.suffix.lower()}")
        final_md = output_dir / f"{final_stem}.md"
        final_txt = output_dir / f"{final_stem}.transcript.txt"
        final_json = output_dir / f"{final_stem}.transcript.json"
        final_srt = output_dir / f"{final_stem}.transcript.srt"
        final_screen_json = output_dir / f"{final_stem}.screen_text.json"
        final_screen_txt = output_dir / f"{final_stem}.screen_text.txt"

        shutil.copy2(minutes_path, final_md)
        if settings.docx_enabled:
            from scripts.docx_report import generate_docx_report

            final_docx = generate_docx_report(
                final_md,
                output_dir / f"{final_stem}.docx",
                meeting_title=output_dir.name,
                saved_date=saved_date,
            )
        else:
            final_docx = None
        shutil.copy2(transcript_text_path, final_txt)
        shutil.copy2(transcript_path, final_json)
        shutil.copy2(job_dir / "transcript.srt", final_srt)
        if screen_text_json_path.exists():
            shutil.copy2(screen_text_json_path, final_screen_json)
        if screen_text_txt_path.exists():
            shutil.copy2(screen_text_txt_path, final_screen_txt)

        snapshots_out = copy_snapshots(job_dir, output_dir)
        cleaned_job_ocr_images = False
        if settings.cleanup_job_ocr_images_after_archive:
            cleaned_job_ocr_images = cleanup_job_ocr_images(job_dir)

        completed_at = now_local().isoformat()
        files = {
            "source": str(source_out),
            "minutes": str(final_md),
            "transcript_txt": str(final_txt),
            "transcript_json": str(final_json),
            "transcript_srt": str(final_srt),
            "screen_text_json": str(final_screen_json),
            "screen_text_txt": str(final_screen_txt),
        }
        if final_docx is not None:
            files["docx"] = str(final_docx)
        if snapshots_out is not None:
            files["snapshots"] = str(snapshots_out)
        if has_video:
            files["video"] = str(source_out)
        else:
            files["audio"] = str(source_out)

        status(
            "completed",
            state="completed",
            completed_at=completed_at,
            output_dir=str(output_dir),
            date_output_dir=str(output_dir.parent),
            base_name=final_stem,
            cleaned_job_ocr_images=cleaned_job_ocr_images,
            files=files,
        )

        index.setdefault("processed_files", []).append(
            {
                "fingerprint": fingerprint,
                "source": str(source),
                "job_id": job_id,
                "completed_at": completed_at,
                "output_dir": str(output_dir),
                "base_name": final_stem,
            }
        )
        write_json(index_path, index)
        print(f"saved: {output_dir}")
        return output_dir
    except Exception as exc:
        logs_path.write_text(traceback.format_exc(), encoding="utf-8")
        status(
            current_step,
            state="failed",
            error=str(exc),
            retryable=True,
            logs=str(logs_path),
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Process one meeting recording.")
    parser.add_argument(
        "media_path",
        help=f"Path to a supported media file: {supported_extensions_text()}",
    )
    args = parser.parse_args()
    try:
        process_file(Path(args.media_path))
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc


def build_codex_minutes_input(transcript_text: str, screen_text: str) -> str:
    parts = [
        "# Codex 회의록 작성 입력",
        "",
        "아래 자료를 바탕으로 한국어 회의록 Markdown을 작성하세요.",
        "전사 오류는 문맥상 자연스럽게 보정하되 없는 내용을 만들지 마세요.",
        "영어는 제품명, API 이름, 명령어, 고유명사처럼 필요한 경우에만 유지하세요.",
        "",
        "## 출력 형식",
        "",
        "# 회의록",
        "",
        "## 1. 회의 요약",
        "## 2. 주요 결정사항",
        "## 3. 액션 아이템",
        "## 4. 논의 상세",
        "## 5. 확인 필요 사항",
        "",
        "## 음성 전사",
        "",
        transcript_text.strip(),
    ]
    if screen_text.strip():
        parts.extend(["", "## 화면 공유 OCR 텍스트", "", screen_text.strip()])
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    main()
