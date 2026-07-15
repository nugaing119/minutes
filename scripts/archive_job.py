from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings
from scripts.content_audit import validate_content_artifacts
from scripts.media_types import is_video_extension
from scripts.translation import resolve_final_markdown
from scripts.utils import now_local, read_json, safe_filename, unique_path, write_json


def archive_job(
    job_dir: Path,
    title: str | None = None,
    *,
    settings: object | None = None,
) -> Path:
    settings = settings or load_settings()
    job_dir = job_dir.expanduser().resolve()
    if not job_dir.exists():
        raise FileNotFoundError(job_dir)

    source = find_source(job_dir)
    source_minutes_path = job_dir / "minutes.md"
    if not source_minutes_path.exists():
        raise FileNotFoundError(source_minutes_path)
    content_audit = validate_content_artifacts(
        job_dir,
        audit_mode=getattr(settings, "content_audit_mode", "off"),
        official_source_verification=getattr(
            settings,
            "official_source_verification",
            "off",
        ),
    )
    if content_audit["issues"]:
        print("warning: content audit issues: " + "; ".join(content_audit["issues"]))
    if (
        getattr(settings, "content_audit_mode", "off") == "strict"
        and (job_dir / "evidence_chunks.json").is_file()
    ):
        from scripts.content_freeze import validate_content_freeze

        validate_content_freeze(job_dir, revalidate_content=False)
    minutes_path = resolve_final_markdown(job_dir)

    metadata = read_json(job_dir / "source_metadata.json")
    saved_date = extract_recording_date(job_dir, source)
    markdown_title = extract_markdown_title(minutes_path)
    original_name = str(metadata.get("original_name", "")).strip()
    fallback_title = Path(original_name).stem if original_name else source.stem
    display_title, safe_title = resolve_document_titles(
        title or markdown_title,
        fallback_title,
    )
    document_type = extract_document_type(minutes_path)
    output_root = Path(settings.output_dir).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    output_dir = unique_path(output_root / f"{saved_date}_{safe_title}")
    staging_dir = unique_path(
        output_root / f".{output_dir.name}.{job_dir.name}.partial"
    )
    staging_dir.mkdir(parents=True, exist_ok=False)
    final_stem = output_dir.name
    staged_source = staging_dir / f"{final_stem}{source.suffix.lower()}"
    docx_qa_path: Path | None = None
    source_moved = False
    try:
        files = {
            "minutes": copy_required(
                minutes_path,
                staging_dir / f"{final_stem}.md",
            ),
        }
        if settings.docx_enabled:
            final_docx, docx_qa_path = prepare_docx_for_archive(
                job_dir,
                minutes_path,
                settings=settings,
                document_title=display_title,
                document_type=document_type,
                saved_date=saved_date,
            )
            files["docx"] = copy_required(
                final_docx,
                staging_dir / f"{final_stem}.docx",
            )

        snapshots_out = copy_snapshots(job_dir, staging_dir)
        if snapshots_out is not None:
            files["snapshots"] = snapshots_out

        move_required(source, staged_source)
        source_moved = True
        staging_dir.rename(output_dir)
    except Exception:
        if source_moved and staged_source.exists() and not source.exists():
            move_required(staged_source, source)
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise

    source_out = output_dir / staged_source.name
    files = {
        name: output_dir / path.relative_to(staging_dir)
        for name, path in files.items()
    }
    # Raw frames and selected snapshots remain available for evidence review until
    # the verified completed-job retention cleanup removes the whole job directory.
    cleaned_job_ocr_images = False
    retained_job_ocr_images = any(
        (job_dir / dirname).exists() for dirname in ("frames", "snapshots")
    )

    files_payload = {
        "source": str(source_out),
        **{name: str(path) for name, path in files.items()},
    }
    if docx_qa_path is not None:
        files_payload["docx_qa"] = str(docx_qa_path)
    if is_video_extension(source.suffix):
        files_payload["video"] = str(source_out)
    else:
        files_payload["audio"] = str(source_out)

    job_media_cleanup = read_early_media_cleanup(job_dir)
    previous_status = read_json(job_dir / "status.json")
    completed_at = now_local().isoformat()
    process_metrics = dict(previous_status.get("process_metrics", {}))
    try:
        process_metrics.update(
            finalize_process_metrics(
                job_dir,
                output_dir=output_dir,
                completed_at=completed_at,
            )
        )
    except OSError as exc:
        print(f"warning: process metrics finalization failed: {exc}")
    status = {
        "status": "completed",
        "step": "completed",
        "job_id": job_dir.name,
        "source": str(metadata.get("original_path") or previous_status.get("source") or source),
        "recording_date": saved_date,
        "completed_at": completed_at,
        "output_dir": str(output_dir),
        "output_root": str(output_root),
        "base_name": final_stem,
        "cleaned_job_ocr_images": cleaned_job_ocr_images,
        "retained_job_ocr_images": retained_job_ocr_images,
        "ocr_image_retention": "completed_job_retention",
        "cleaned_job_media": job_media_cleanup["cleaned"],
        "job_media_cleanup": job_media_cleanup,
        "reclaimed_bytes": job_media_cleanup["reclaimed_bytes"],
        "speaker_attribution": previous_status.get("speaker_attribution", {}),
        "codex_handoff": previous_status.get("codex_handoff", {}),
        "content_audit": content_audit,
        "resource_policy": previous_status.get("resource_policy", {}),
        "process_metrics": process_metrics,
        "files": files_payload,
    }
    write_json(job_dir / "status.json", status)
    if getattr(settings, "cleanup_job_media_after_archive", False):
        job_media_cleanup = merge_cleanup_results(
            job_media_cleanup,
            attempt_cleanup_job_media(job_dir, source_out),
        )
        status.update(
            {
                "cleaned_job_media": job_media_cleanup["cleaned"],
                "job_media_cleanup": job_media_cleanup,
                "reclaimed_bytes": job_media_cleanup["reclaimed_bytes"],
            }
        )
        try:
            write_json(job_dir / "status.json", status)
        except OSError as exc:
            print(f"warning: archived media cleaned but status update failed: {exc}")
    update_processed_index(
        job_dir,
        metadata,
        output_dir=output_dir,
        final_stem=final_stem,
        completed_at=str(status["completed_at"]),
    )
    try:
        from scripts.cleanup_completed_jobs import cleanup_completed_jobs

        cleanup = cleanup_completed_jobs(
            Path(getattr(settings, "jobs_dir", job_dir.parent)),
            apply=True,
            retention_hours=getattr(settings, "completed_job_retention_hours", 0),
            excluded_jobs={job_dir},
        )
        if cleanup["purged_jobs"]:
            print(
                "expired jobs purged: "
                f"{cleanup['purged_jobs']} "
                f"({cleanup['reclaimed_bytes']} bytes)"
            )
    except OSError as exc:
        print(f"warning: expired job cleanup failed: {exc}")
    print(f"saved: {output_dir}")
    return output_dir


def resolve_document_titles(
    requested_title: str | None,
    fallback_title: str,
) -> tuple[str, str]:
    display_title = (
        (requested_title or "").strip()
        or fallback_title.strip()
        or "콘텐츠 분석"
    )
    folder_title = safe_filename(display_title, max_length=80) or "콘텐츠-분석"
    return display_title, folder_title


def prepare_docx_for_archive(
    job_dir: Path,
    markdown_path: Path,
    *,
    settings: object,
    document_title: str,
    document_type: str,
    saved_date: str,
) -> tuple[Path, Path]:
    from scripts.docx_qa import create_docx_qa, validate_docx_qa
    from scripts.docx_report import generate_docx_report

    draft_path = job_dir / "minutes.draft.docx"
    final_path = job_dir / "minutes.final.docx"
    qa_path = job_dir / "docx_qa.json"
    render_dir = job_dir / "docx_render"
    require_visual = (
        getattr(settings, "llm_provider", "") == "codex"
        or getattr(settings, "content_audit_mode", "off") == "strict"
    )

    if require_visual:
        for required in (draft_path, final_path, qa_path):
            if not required.is_file():
                raise FileNotFoundError(
                    "Codex/strict DOCX archive requires Documents finalization: "
                    f"missing {required.name}"
                )
        validate_docx_qa(
            markdown_path,
            final_path,
            qa_path,
            require_visual=True,
            require_visual_review=(job_dir / "content_freeze.json").is_file(),
        )
        return final_path, qa_path

    if not draft_path.is_file():
        generate_docx_report(
            markdown_path,
            draft_path,
            document_title=document_title,
            document_type=document_type,
            saved_date=saved_date,
        )
    if not final_path.is_file():
        shutil.copy2(draft_path, final_path)
    if not qa_path.is_file():
        render_dir.mkdir(parents=True, exist_ok=True)
        create_docx_qa(
            markdown_path,
            draft_path,
            final_path,
            render_dir=render_dir,
            visual_status="not_run",
            output_path=qa_path,
            renderer="not_run_non_codex_fallback",
        )
    validate_docx_qa(
        markdown_path,
        final_path,
        qa_path,
        require_visual=False,
    )
    return final_path, qa_path


def resolve_meeting_titles(
    requested_title: str | None,
    fallback_title: str,
) -> tuple[str, str]:
    """Compatibility alias for callers using the previous public name."""
    return resolve_document_titles(requested_title, fallback_title)


def extract_markdown_title(markdown_path: Path) -> str:
    for line in markdown_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            title = match.group(1).strip()
            if title not in {"회의록", "Meeting Minutes"}:
                return title
    return ""


def extract_document_type(markdown_path: Path) -> str:
    for line in markdown_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(?:문서 유형|Document type)\s*:\s*(.+)$", line.strip(), re.I)
        if match:
            return match.group(1).strip()
    return ""


def extract_recording_date(job_dir: Path, source: Path) -> str:
    metadata = read_json(job_dir / "source_metadata.json")
    candidates = [
        str(metadata.get("recording_date", "")),
        str(metadata.get("original_name", "")),
        str(metadata.get("original_path", "")),
        job_dir.name,
    ]
    for candidate in candidates:
        match = re.search(
            r"(?<!\d)(20\d{2})[-_. ]?(\d{2})[-_. ]?(\d{2})(?!\d)",
            candidate,
        )
        if not match:
            continue
        value = "-".join(match.groups())
        try:
            date.fromisoformat(value)
        except ValueError:
            continue
        return value
    return datetime.fromtimestamp(source.stat().st_mtime).astimezone().strftime("%Y-%m-%d")


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


def move_required(src: Path, dst: Path) -> Path:
    if not src.exists():
        raise FileNotFoundError(src)
    if src.is_symlink() or not src.is_file():
        raise ValueError(f"Refusing to move unexpected source path: {src}")
    if dst.exists():
        raise FileExistsError(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(src), str(dst)))


def update_processed_index(
    job_dir: Path,
    metadata: dict,
    *,
    output_dir: Path,
    final_stem: str,
    completed_at: str,
) -> None:
    fingerprint = str(metadata.get("fingerprint", "")).strip()
    if not fingerprint:
        return
    index_path = job_dir.parent / "index.json"
    index = read_json(index_path, {"processed_files": []})
    processed = [
        item
        for item in index.get("processed_files", [])
        if item.get("fingerprint") != fingerprint
    ]
    processed.append(
        {
            "fingerprint": fingerprint,
            "source": str(metadata.get("original_path", "")),
            "job_id": job_dir.name,
            "speaker_attribution_mode": metadata.get(
                "speaker_attribution_mode",
                "off",
            ),
            "speaker_attribution_profile": metadata.get(
                "speaker_attribution_profile",
                "off",
            ),
            "completed_at": completed_at,
            "output_dir": str(output_dir),
            "base_name": final_stem,
        }
    )
    index["processed_files"] = processed
    write_json(index_path, index)


def make_meeting_output_dir(output_root: Path, saved_date: str, meeting_folder_name: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    meeting_dir = unique_path(output_root / f"{saved_date}_{meeting_folder_name}")
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


def cleanup_job_media(job_dir: Path, archived_source: Path) -> dict[str, object]:
    """Remove redundant job media only after verifying the archived source copy."""
    candidates, reclaimed_bytes = verify_job_media_cleanup(job_dir, archived_source)
    removed_files = [path.name for path in candidates]
    for path in candidates:
        path.unlink()
    return {
        "cleaned": bool(removed_files),
        "removed_files": removed_files,
        "reclaimed_bytes": reclaimed_bytes,
    }


def verify_job_media_cleanup(
    job_dir: Path,
    archived_source: Path,
) -> tuple[list[Path], int]:
    """Return removable job media after verifying the final archived media."""
    if not archived_source.is_file():
        raise FileNotFoundError(archived_source)
    if archived_source.is_symlink():
        raise ValueError(f"Refusing unexpected archived media path: {archived_source}")

    sources = sorted(job_dir.glob("source.*"))
    if len(sources) > 1:
        raise ValueError(f"Multiple job source files found: {job_dir}")
    candidates: list[Path] = []
    if sources:
        source = sources[0]
        source_size = source.stat().st_size
        archived_size = archived_source.stat().st_size
        if source_size != archived_size:
            raise ValueError(
                "Archived source size does not match job source: "
                f"{archived_size} != {source_size}"
            )
        if source.is_symlink() or not filecmp.cmp(
            source,
            archived_source,
            shallow=False,
        ):
            raise ValueError("Archived source content does not match job source")
        candidates.append(source)

    extracted_audio = job_dir / "audio.wav"
    if extracted_audio.exists() and extracted_audio not in candidates:
        candidates.append(extracted_audio)
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Refusing to clean unexpected job media path: {path}")

    reclaimed_bytes = sum(path.stat().st_size for path in candidates)
    return candidates, reclaimed_bytes


def attempt_cleanup_job_media(
    job_dir: Path,
    archived_source: Path,
) -> dict[str, object]:
    """Keep successful archives successful even if optional cleanup is blocked."""
    try:
        return cleanup_job_media(job_dir, archived_source)
    except Exception as exc:
        return {
            "cleaned": False,
            "removed_files": [],
            "reclaimed_bytes": 0,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


def read_early_media_cleanup(job_dir: Path) -> dict[str, object]:
    metrics = read_json(job_dir / "process_metrics.json")
    cleanup = metrics.get("intermediate_cleanup", {}).get("audio_wav", {})
    return {
        "cleaned": bool(cleanup.get("cleaned", False)),
        "removed_files": list(cleanup.get("removed_files", [])),
        "reclaimed_bytes": int(cleanup.get("reclaimed_bytes", 0)),
    }


def finalize_process_metrics(
    job_dir: Path,
    *,
    output_dir: Path,
    completed_at: str,
) -> dict[str, object]:
    """Mark preprocessing metrics complete once final deliverables are archived."""
    metrics_path = job_dir / "process_metrics.json"
    if not metrics_path.exists():
        return {}
    metrics = read_json(metrics_path)
    if not metrics:
        return {}
    metrics.update(
        {
            "state": "completed",
            "updated_at": completed_at,
            "completed_at": completed_at,
            "output_dir": str(output_dir),
            "preprocessing_elapsed_seconds": metrics.get("elapsed_seconds", 0),
            "elapsed_scope": "local_preprocessing_only",
        }
    )
    write_json(metrics_path, metrics)
    return {
        "path": str(metrics_path),
        "state": "completed",
        "completed_stage_count": len(metrics.get("stages", [])),
        "preprocessing_elapsed_seconds": metrics.get("elapsed_seconds", 0),
    }


def merge_cleanup_results(
    first: dict[str, object],
    second: dict[str, object],
) -> dict[str, object]:
    removed_files = list(
        dict.fromkeys(
            [
                *list(first.get("removed_files", [])),
                *list(second.get("removed_files", [])),
            ]
        )
    )
    merged = {
        "cleaned": bool(first.get("cleaned") or second.get("cleaned")),
        "removed_files": removed_files,
        "reclaimed_bytes": int(first.get("reclaimed_bytes", 0))
        + int(second.get("reclaimed_bytes", 0)),
    }
    error = second.get("error") or first.get("error")
    if error:
        merged["error"] = error
        merged["error_type"] = second.get("error_type") or first.get("error_type")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive a completed media-analysis job.")
    parser.add_argument("job_dir", help="Path to a job directory")
    parser.add_argument("--title", help="Optional document title override")
    args = parser.parse_args()
    try:
        archive_job(Path(args.job_dir), title=args.title)
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
