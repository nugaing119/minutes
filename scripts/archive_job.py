from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings
from scripts.utils import now_local, safe_filename, unique_path, write_json


def archive_job(job_dir: Path, title: str | None = None) -> Path:
    settings = load_settings()
    job_dir = job_dir.expanduser().resolve()
    if not job_dir.exists():
        raise FileNotFoundError(job_dir)

    source = find_source(job_dir)
    minutes_path = job_dir / "minutes.md"
    if not minutes_path.exists():
        raise FileNotFoundError(minutes_path)

    saved_date = now_local().strftime("%Y-%m-%d")
    safe_title = safe_filename(title or source.stem, max_length=80) or "회의록"
    output_dir = make_meeting_output_dir(settings.output_dir, saved_date, safe_title)

    final_stem = f"{saved_date}_{output_dir.name}"
    video_out = copy_required(source, output_dir / f"{final_stem}{source.suffix.lower()}")

    files = {
        "minutes": copy_required(minutes_path, output_dir / f"{final_stem}.md"),
        "transcript_txt": copy_required(
            job_dir / "transcript.txt",
            output_dir / f"{final_stem}.transcript.txt",
        ),
        "transcript_json": copy_required(
            job_dir / "transcript.json",
            output_dir / f"{final_stem}.transcript.json",
        ),
        "transcript_srt": copy_required(
            job_dir / "transcript.srt",
            output_dir / f"{final_stem}.transcript.srt",
        ),
    }
    if settings.docx_enabled:
        from scripts.docx_report import generate_docx_report

        docx_path = generate_docx_report(
            files["minutes"],
            output_dir / f"{final_stem}.docx",
            meeting_title=output_dir.name,
            saved_date=saved_date,
        )
        files["docx"] = docx_path

    for name, src_name, suffix in (
        ("screen_text_txt", "screen_text.txt", ".screen_text.txt"),
        ("screen_text_json", "screen_text.json", ".screen_text.json"),
    ):
        src = job_dir / src_name
        if src.exists():
            dst = output_dir / f"{final_stem}{suffix}"
            shutil.copy2(src, dst)
            files[name] = dst

    snapshots_out = copy_snapshots(job_dir, output_dir)
    if snapshots_out is not None:
        files["snapshots"] = snapshots_out
    cleaned_job_ocr_images = False
    if settings.cleanup_job_ocr_images_after_archive:
        cleaned_job_ocr_images = cleanup_job_ocr_images(job_dir)

    status = {
        "status": "completed",
        "step": "completed",
        "job_id": job_dir.name,
        "source": str(source),
        "completed_at": now_local().isoformat(),
        "output_dir": str(output_dir),
        "date_output_dir": str(output_dir.parent),
        "base_name": final_stem,
        "cleaned_job_ocr_images": cleaned_job_ocr_images,
        "files": {"video": str(video_out), **{k: str(v) for k, v in files.items()}},
    }
    write_json(job_dir / "status.json", status)
    print(f"saved: {output_dir}")
    return output_dir


def find_source(job_dir: Path) -> Path:
    matches = sorted(job_dir.glob("source.*"))
    if not matches:
        raise FileNotFoundError(job_dir / "source.*")
    return matches[0]


def copy_required(src: Path, dst: Path) -> Path:
    if not src.exists():
        raise FileNotFoundError(src)
    shutil.copy2(src, dst)
    return dst


def make_meeting_output_dir(output_root: Path, saved_date: str, meeting_folder_name: str) -> Path:
    date_dir = output_root / saved_date
    date_dir.mkdir(parents=True, exist_ok=True)
    meeting_dir = unique_path(date_dir / meeting_folder_name)
    meeting_dir.mkdir(parents=True, exist_ok=True)
    return meeting_dir


def copy_snapshots(job_dir: Path, output_dir: Path) -> Path | None:
    snapshots_dir = job_dir / "snapshots"
    if not snapshots_dir.exists():
        return None
    snapshot_files = sorted(snapshots_dir.glob("snapshot_*.jpg"))
    if not snapshot_files:
        return None
    snapshots_out = output_dir / "snapshots"
    if snapshots_out.exists():
        shutil.rmtree(snapshots_out)
    snapshots_out.mkdir(parents=True, exist_ok=True)
    for snapshot in snapshot_files:
        shutil.copy2(snapshot, snapshots_out / snapshot.name)
    return snapshots_out


def cleanup_job_ocr_images(job_dir: Path) -> bool:
    removed = False
    for dirname in ("frames", "snapshots"):
        path = job_dir / dirname
        if path.exists():
            shutil.rmtree(path)
            removed = True
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive a completed Codex minutes job.")
    parser.add_argument("job_dir", help="Path to a job directory")
    parser.add_argument("--title", help="Meeting title for the output file name")
    args = parser.parse_args()
    try:
        archive_job(Path(args.job_dir), title=args.title)
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
