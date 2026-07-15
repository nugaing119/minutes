from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from scripts.config import Settings
from scripts.cpu_limit import run_limited
from scripts.utils import write_json


def run_ocr(
    video_path: Path,
    job_dir: Path,
    settings: Settings,
    *,
    detected_language: str | None = None,
) -> dict[str, Any]:
    screen_text_path = job_dir / "screen_text.json"
    screen_text_txt_path = job_dir / "screen_text.txt"

    if not settings.ocr_enabled:
        result = {"enabled": False, "status": "disabled", "frames": []}
        write_json(screen_text_path, result)
        write_json(
            job_dir / "evidence_coverage.json",
            unavailable_evidence_coverage("disabled", "OCR is disabled"),
        )
        screen_text_txt_path.write_text("", encoding="utf-8")
        return result

    if shutil.which("tesseract") is None:
        result = {
            "enabled": True,
            "status": "skipped",
            "reason": "tesseract command not found",
            "frames": [],
        }
        write_json(screen_text_path, result)
        write_json(
            job_dir / "evidence_coverage.json",
            unavailable_evidence_coverage("skipped", result["reason"]),
        )
        screen_text_txt_path.write_text("", encoding="utf-8")
        return result

    ocr_languages = resolve_ocr_languages(
        settings.ocr_languages,
        detected_language,
    )
    frames_dir = job_dir / "frames"
    snapshots_dir = job_dir / "snapshots"
    _, frame_extraction_metrics = measure_phase(
        lambda: extract_frames(
            video_path,
            frames_dir,
            settings.ocr_frame_interval_seconds,
            settings.ocr_ffmpeg_threads,
            settings.ocr_frame_extract_cpu_limit_percent,
            settings.ocr_frame_extract_cpu_limit_period_seconds,
            settings.ocr_frame_extract_cpu_limit_fallback_burst_cores,
        )
    )
    raw_frame_files = sorted(frames_dir.glob("frame_*.jpg"))
    raw_frame_bytes = sum(
        frame.stat().st_size for frame in raw_frame_files if frame.is_file()
    )
    reset_snapshots(snapshots_dir)
    workers = normalized_positive_int(getattr(settings, "ocr_workers", 1), 1)

    def signatures_for(
        frame_path: Path,
    ) -> tuple[bytes | None, bytes | None]:
        return visual_frame_signature_pair(
            frame_path,
            settings.ocr_visual_dedupe_ignore_bottom_ratio,
            settings.ocr_visual_dedupe_ignore_right_ratio,
            settings.ocr_ffmpeg_threads,
            settings.ocr_signature_cpu_limit_percent,
            settings.ocr_signature_cpu_limit_period_seconds,
            settings.ocr_signature_cpu_limit_fallback_burst_cores,
        )

    def text_for(frame_path: Path) -> str:
        return ocr_frame(
            frame_path,
            ocr_languages,
            settings.ocr_tesseract_thread_limit,
            settings.ocr_tesseract_nice,
            settings.ocr_tesseract_cpu_limit_percent,
            settings.ocr_tesseract_cpu_limit_period_seconds,
            settings.ocr_tesseract_cpu_limit_fallback_burst_cores,
        )

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="minutes-ocr",
    ) as executor:
        if settings.ocr_visual_dedupe_enabled:
            signature_pairs, signature_metrics = measure_phase(
                lambda: list(executor.map(signatures_for, raw_frame_files))
            )
        else:
            signature_pairs = [(None, None)] * len(raw_frame_files)
            signature_metrics = empty_phase_metrics()

        def choose_candidates() -> tuple[
            list[int],
            list[str],
            list[str],
            list[float],
            list[float],
            list[bool],
        ]:
            candidate_indexes: list[int] = []
            outcomes = [""] * len(raw_frame_files)
            candidate_reasons = [""] * len(raw_frame_files)
            content_deltas = [255.0] * len(raw_frame_files)
            full_deltas = [255.0] * len(raw_frame_files)
            localized_content_changes = [False] * len(raw_frame_files)
            previous_full_signature: bytes | None = None
            previous_content_signature: bytes | None = None
            last_forced_timestamp = 0
            max_gap = normalized_positive_int(
                getattr(settings, "ocr_max_snapshot_gap_seconds", 120),
                120,
            )
            change_threshold = max(
                settings.ocr_visual_dedupe_max_mean_delta,
                0.0,
            )
            for index, (full_signature, content_signature) in enumerate(
                signature_pairs
            ):
                timestamp_seconds = frame_index_to_seconds(
                    raw_frame_files[index],
                    settings.ocr_frame_interval_seconds,
                )
                if previous_content_signature is not None and content_signature is not None:
                    content_deltas[index] = mean_pixel_delta(
                        previous_content_signature,
                        content_signature,
                    )
                if previous_full_signature is not None and full_signature is not None:
                    full_deltas[index] = mean_pixel_delta(
                        previous_full_signature,
                        full_signature,
                    )
                localized_content_changes[index] = is_localized_visual_change(
                    previous_content_signature,
                    content_signature,
                    change_threshold,
                )
                localized_full_change = is_localized_visual_change(
                    previous_full_signature,
                    full_signature,
                    change_threshold,
                )

                reason = ""
                if index == 0:
                    reason = "first_frame"
                    last_forced_timestamp = timestamp_seconds
                elif timestamp_seconds - last_forced_timestamp >= max_gap:
                    reason = "forced_coverage"
                    last_forced_timestamp = timestamp_seconds
                elif not settings.ocr_visual_dedupe_enabled:
                    reason = "content_change"
                elif (
                    content_deltas[index] > change_threshold
                    or localized_content_changes[index]
                ):
                    reason = "content_change"
                elif full_deltas[index] > change_threshold or localized_full_change:
                    reason = "speaker_ui_change"
                else:
                    outcomes[index] = "visual_duplicate"

                if not reason:
                    continue
                candidate_indexes.append(index)
                candidate_reasons[index] = reason
                if full_signature is not None:
                    previous_full_signature = full_signature
                if content_signature is not None:
                    previous_content_signature = content_signature
            return (
                candidate_indexes,
                outcomes,
                candidate_reasons,
                content_deltas,
                full_deltas,
                localized_content_changes,
            )

        (
            (
                candidate_indexes,
                outcomes,
                candidate_reasons,
                content_deltas,
                full_deltas,
                localized_content_changes,
            ),
            candidate_metrics,
        ) = measure_phase(choose_candidates)
        candidate_paths = [raw_frame_files[index] for index in candidate_indexes]
        (candidate_texts, worker_metrics), tesseract_metrics = measure_phase(
            lambda: run_bounded_ocr_tasks(
                candidate_paths,
                text_for,
                executor=executor,
                workers=workers,
            )
        )

    def apply_ordered_results() -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        previous_text = ""
        snapshot_index = 0
        candidate_text_by_index = dict(zip(candidate_indexes, candidate_texts))
        for index, frame_path in enumerate(raw_frame_files):
            if outcomes[index] == "visual_duplicate":
                pause_after_frame(settings.ocr_frame_pause_seconds)
                continue
            text = normalize_ocr_text(candidate_text_by_index.get(index, ""))
            text_duplicate = is_near_duplicate(previous_text, text)
            candidate_reason = candidate_reasons[index]
            visual_only_threshold = max(
                getattr(settings, "ocr_visual_only_min_mean_delta", 12.0),
                0.0,
            )
            selected = False
            if candidate_reason == "forced_coverage":
                outcomes[index] = "forced_coverage"
                selected = True
            elif candidate_reason == "speaker_ui_change":
                outcomes[index] = "speaker_ui_change"
                selected = True
            elif candidate_reason == "first_frame" and not text:
                outcomes[index] = "visual_only"
                selected = True
            elif not text:
                if (
                    content_deltas[index] >= visual_only_threshold
                    or localized_content_changes[index]
                ):
                    outcomes[index] = "visual_only"
                    selected = True
                else:
                    outcomes[index] = "empty_ocr"
            elif text_duplicate:
                if (
                    content_deltas[index] >= visual_only_threshold
                    or localized_content_changes[index]
                ):
                    outcomes[index] = "visual_only"
                    selected = True
                else:
                    outcomes[index] = "text_duplicate"
            else:
                outcomes[index] = "selected"
                selected = True

            if not selected:
                outcomes[index] = "empty_ocr"
                if text:
                    outcomes[index] = "text_duplicate"
                pause_after_frame(settings.ocr_frame_pause_seconds)
                continue
            stored_text = text if text and not text_duplicate else ""
            if stored_text:
                previous_text = stored_text
            timestamp_seconds = frame_index_to_seconds(
                frame_path,
                settings.ocr_frame_interval_seconds,
            )
            snapshot_index += 1
            snapshot_path = copy_snapshot(
                frame_path,
                snapshots_dir,
                snapshot_index,
                timestamp_seconds,
            )
            frames.append(
                {
                    "evidence_id": f"snapshot-{snapshot_index:04d}",
                    "timestamp_seconds": timestamp_seconds,
                    "timestamp": seconds_to_timestamp(timestamp_seconds),
                    "source_frame": frame_path.name,
                    "snapshot": str(snapshot_path),
                    "selection_reason": outcomes[index],
                    "text": stored_text,
                }
            )
            pause_after_frame(settings.ocr_frame_pause_seconds)
        return frames

    frames, text_dedupe_metrics = measure_phase(apply_ordered_results)
    reason_counts: dict[str, int] = {}
    for outcome in outcomes:
        reason = outcome or "unclassified"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    parent_nice = observed_process_nice()
    configured_tesseract_nice = normalized_nice(settings.ocr_tesseract_nice)
    coverage = build_evidence_coverage(
        job_dir,
        raw_frame_files=raw_frame_files,
        signature_pairs=signature_pairs,
        content_deltas=content_deltas,
        full_deltas=full_deltas,
        localized_content_changes=localized_content_changes,
        outcomes=outcomes,
        selected_frames=frames,
        frame_interval_seconds=settings.ocr_frame_interval_seconds,
        max_snapshot_gap_seconds=getattr(
            settings,
            "ocr_max_snapshot_gap_seconds",
            120,
        ),
        visual_only_min_mean_delta=getattr(
            settings,
            "ocr_visual_only_min_mean_delta",
            12.0,
        ),
    )
    write_json(job_dir / "evidence_coverage.json", coverage)
    phase_metrics = {
        "frame_extraction": {
            **frame_extraction_metrics,
            "frame_count": len(raw_frame_files),
        },
        "signature": {
            **signature_metrics,
            "call_count": (
                len(raw_frame_files) if settings.ocr_visual_dedupe_enabled else 0
            ),
        },
        "visual_candidate_selection": {
            **candidate_metrics,
            "candidate_count": len(candidate_indexes),
            "skipped_visual_duplicate_count": reason_counts.get(
                "visual_duplicate",
                0,
            ),
        },
        "tesseract": {
            **tesseract_metrics,
            **worker_metrics,
        },
        "text_dedupe_and_snapshot": {
            **text_dedupe_metrics,
            "selected_count": len(frames),
            "empty_ocr_count": reason_counts.get("empty_ocr", 0),
            "text_duplicate_count": reason_counts.get("text_duplicate", 0),
        },
    }

    result = {
        "enabled": True,
        "status": "completed",
        "frame_interval_seconds": settings.ocr_frame_interval_seconds,
        "languages": ocr_languages,
        "detected_source_language": detected_language,
        "ffmpeg_threads": normalized_positive_int(settings.ocr_ffmpeg_threads, 1),
        "workers": workers,
        "tesseract_thread_limit": normalized_positive_int(
            settings.ocr_tesseract_thread_limit,
            1,
        ),
        "tesseract_nice": normalized_nice(settings.ocr_tesseract_nice),
        "frame_pause_seconds": max(settings.ocr_frame_pause_seconds, 0.0),
        "frame_extract_cpu_limit_percent": settings.ocr_frame_extract_cpu_limit_percent,
        "signature_cpu_limit_percent": settings.ocr_signature_cpu_limit_percent,
        "tesseract_cpu_limit_percent": settings.ocr_tesseract_cpu_limit_percent,
        "visual_dedupe_enabled": settings.ocr_visual_dedupe_enabled,
        "visual_dedupe_ignore_bottom_ratio": normalized_ratio(
            settings.ocr_visual_dedupe_ignore_bottom_ratio,
            0.18,
        ),
        "visual_dedupe_ignore_right_ratio": normalized_ratio(
            settings.ocr_visual_dedupe_ignore_right_ratio,
            0.20,
        ),
        "visual_dedupe_max_mean_delta": max(
            settings.ocr_visual_dedupe_max_mean_delta,
            0.0,
        ),
        "max_snapshot_gap_seconds": getattr(
            settings,
            "ocr_max_snapshot_gap_seconds",
            120,
        ),
        "observed_max_snapshot_gap_seconds": coverage[
            "max_selected_snapshot_gap_seconds"
        ],
        "visual_only_min_mean_delta": max(
            getattr(settings, "ocr_visual_only_min_mean_delta", 12.0),
            0.0,
        ),
        "snapshots_dir": str(snapshots_dir),
        "raw_frame_count": len(raw_frame_files),
        "raw_frames_bytes": raw_frame_bytes,
        "raw_frames_cleaned": False,
        "raw_frames_reclaimed_bytes": 0,
        "raw_frames_retention": "completed_job_retention",
        "selection_reason_counts": dict(sorted(reason_counts.items())),
        "selection_result_sha256": selection_result_sha256(frames),
        "evidence_coverage": str(job_dir / "evidence_coverage.json"),
        "metrics": {
            "schema_version": 1,
            "phases": phase_metrics,
            "resource": {
                "process_qos": getattr(settings, "process_qos", "unknown"),
                "configured_process_nice": getattr(settings, "process_nice", None),
                "observed_parent_nice": parent_nice,
                "tesseract_nice_increment": configured_tesseract_nice,
                "expected_tesseract_nice": (
                    min(parent_nice + configured_tesseract_nice, 20)
                    if parent_nice is not None
                    else None
                ),
                "ffmpeg_threads": normalized_positive_int(
                    settings.ocr_ffmpeg_threads,
                    1,
                ),
                "workers": workers,
                "tesseract_thread_limit": normalized_positive_int(
                    settings.ocr_tesseract_thread_limit,
                    1,
                ),
            },
        },
        "frames": frames,
    }
    write_json(screen_text_path, result)
    screen_text_txt_path.write_text(render_screen_text(frames), encoding="utf-8")
    return result


def cpu_seconds() -> float:
    usage = os.times()
    return usage.user + usage.system + usage.children_user + usage.children_system


def unavailable_evidence_coverage(status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "coverage_passed": False,
        "reason": reason,
        "raw_frame_count": 0,
        "selected_snapshot_count": 0,
        "accounted_frame_count": 0,
        "accounting_complete": False,
        "reason_counts": {},
        "frames": [],
    }


def empty_phase_metrics() -> dict[str, float]:
    return {
        "wall_seconds": 0.0,
        "cpu_seconds": 0.0,
        "cpu_percent_of_one_core": 0.0,
    }


def measure_phase(operation: Callable[[], Any]) -> tuple[Any, dict[str, float]]:
    started = time.perf_counter()
    cpu_started = cpu_seconds()
    value = operation()
    wall_seconds = max(time.perf_counter() - started, 0.0)
    consumed_cpu_seconds = max(cpu_seconds() - cpu_started, 0.0)
    return value, {
        "wall_seconds": round(wall_seconds, 3),
        "cpu_seconds": round(consumed_cpu_seconds, 3),
        "cpu_percent_of_one_core": (
            round(consumed_cpu_seconds / wall_seconds * 100.0, 1)
            if wall_seconds > 0
            else 0.0
        ),
    }


def run_bounded_ocr_tasks(
    frame_paths: list[Path],
    operation: Callable[[Path], str],
    *,
    executor: ThreadPoolExecutor,
    workers: int,
) -> tuple[list[str], dict[str, int | float]]:
    worker_limit = normalized_positive_int(workers, 1)
    if not frame_paths:
        return [], {
            "call_count": 0,
            "queue_capacity": 0,
            "peak_active_workers": 0,
            "average_active_workers": 0.0,
            "average_queue_wait_seconds": 0.0,
            "max_queue_wait_seconds": 0.0,
        }

    queue_capacity = min(len(frame_paths), worker_limit * 2)
    results: list[str | None] = [None] * len(frame_paths)
    pending: dict[Future[str], int] = {}
    iterator = iter(enumerate(frame_paths))
    lock = threading.Lock()
    active_workers = 0
    peak_active_workers = 0
    active_worker_seconds = 0.0
    total_queue_wait_seconds = 0.0
    max_queue_wait_seconds = 0.0
    phase_started = time.perf_counter()

    def tracked(frame_path: Path, queued_at: float) -> str:
        nonlocal active_workers, peak_active_workers
        nonlocal active_worker_seconds, total_queue_wait_seconds
        nonlocal max_queue_wait_seconds
        started = time.perf_counter()
        queue_wait = max(started - queued_at, 0.0)
        with lock:
            active_workers += 1
            peak_active_workers = max(peak_active_workers, active_workers)
            total_queue_wait_seconds += queue_wait
            max_queue_wait_seconds = max(max_queue_wait_seconds, queue_wait)
        try:
            return operation(frame_path)
        finally:
            elapsed = max(time.perf_counter() - started, 0.0)
            with lock:
                active_worker_seconds += elapsed
                active_workers -= 1

    def submit_next() -> bool:
        try:
            index, frame_path = next(iterator)
        except StopIteration:
            return False
        queued_at = time.perf_counter()
        pending[executor.submit(tracked, frame_path, queued_at)] = index
        return True

    for _ in range(queue_capacity):
        submit_next()
    while pending:
        completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
        for future in completed:
            index = pending.pop(future)
            results[index] = future.result()
            submit_next()

    phase_wall_seconds = max(time.perf_counter() - phase_started, 0.0)
    call_count = len(frame_paths)
    return [value or "" for value in results], {
        "call_count": call_count,
        "queue_capacity": queue_capacity,
        "peak_active_workers": peak_active_workers,
        "average_active_workers": (
            round(active_worker_seconds / phase_wall_seconds, 3)
            if phase_wall_seconds > 0
            else 0.0
        ),
        "average_queue_wait_seconds": round(
            total_queue_wait_seconds / call_count,
            6,
        ),
        "max_queue_wait_seconds": round(max_queue_wait_seconds, 6),
    }


def observed_process_nice() -> int | None:
    try:
        return os.getpriority(os.PRIO_PROCESS, 0)
    except (AttributeError, OSError):
        return None


def selection_result_sha256(frames: list[dict[str, Any]]) -> str:
    payload = []
    for frame in frames:
        snapshot = Path(str(frame.get("snapshot", "")))
        snapshot_hash = sha256_path(snapshot) if snapshot.is_file() else ""
        payload.append(
            {
                "timestamp_seconds": frame.get("timestamp_seconds"),
                "source_frame": frame.get("source_frame"),
                "selection_reason": frame.get("selection_reason"),
                "text": frame.get("text"),
                "snapshot_sha256": snapshot_hash,
            }
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_evidence_coverage(
    job_dir: Path,
    *,
    raw_frame_files: list[Path],
    signature_pairs: list[tuple[bytes | None, bytes | None]],
    content_deltas: list[float],
    full_deltas: list[float],
    localized_content_changes: list[bool],
    outcomes: list[str],
    selected_frames: list[dict[str, Any]],
    frame_interval_seconds: int,
    max_snapshot_gap_seconds: int,
    visual_only_min_mean_delta: float,
) -> dict[str, Any]:
    selected_by_source = {
        str(item.get("source_frame", "")): item for item in selected_frames
    }
    selected_reasons = {
        "selected",
        "forced_coverage",
        "visual_only",
        "speaker_ui_change",
    }
    reason_counts: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    raw_manifest: list[dict[str, str]] = []
    for index, frame_path in enumerate(raw_frame_files):
        reason = outcomes[index] or "unclassified"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        timestamp_seconds = frame_index_to_seconds(
            frame_path,
            frame_interval_seconds,
        )
        raw_hash = sha256_path(frame_path)
        raw_manifest.append({"source_frame": frame_path.name, "sha256": raw_hash})
        selected_frame = selected_by_source.get(frame_path.name)
        snapshot_path = (
            Path(str(selected_frame.get("snapshot", "")))
            if selected_frame is not None
            else None
        )
        full_signature, content_signature = signature_pairs[index]
        record: dict[str, Any] = {
            "evidence_id": f"frame-{index + 1:06d}",
            "source_frame": frame_path.name,
            "timestamp_seconds": timestamp_seconds,
            "timestamp": seconds_to_timestamp(timestamp_seconds),
            "raw_frame": str(frame_path.relative_to(job_dir)),
            "raw_frame_sha256": raw_hash,
            "full_signature_sha256": (
                hashlib.sha256(full_signature).hexdigest()
                if full_signature is not None
                else None
            ),
            "content_signature_sha256": (
                hashlib.sha256(content_signature).hexdigest()
                if content_signature is not None
                else None
            ),
            "full_mean_delta": round(full_deltas[index], 3),
            "content_mean_delta": round(content_deltas[index], 3),
            "localized_content_change": localized_content_changes[index],
            "selected": reason in selected_reasons,
            "reason": reason,
        }
        if selected_frame is not None and snapshot_path is not None:
            record.update(
                {
                    "snapshot_evidence_id": selected_frame.get("evidence_id"),
                    "snapshot": str(snapshot_path.relative_to(job_dir)),
                    "snapshot_sha256": sha256_path(snapshot_path),
                    "ocr_text_present": bool(
                        str(selected_frame.get("text", "")).strip()
                    ),
                }
            )
        records.append(record)

    selected_timestamps = sorted(
        int(item.get("timestamp_seconds", 0)) for item in selected_frames
    )
    raw_timestamps = [
        frame_index_to_seconds(path, frame_interval_seconds)
        for path in raw_frame_files
    ]
    coverage_points = list(selected_timestamps)
    if raw_timestamps:
        coverage_points.extend((raw_timestamps[0], raw_timestamps[-1]))
    coverage_points = sorted(set(coverage_points))
    max_observed_gap = max(
        (
            right - left
            for left, right in zip(coverage_points, coverage_points[1:])
        ),
        default=0,
    )
    selected_reason_counts = {
        reason: count
        for reason, count in sorted(reason_counts.items())
        if reason in selected_reasons
    }
    excluded_reason_counts = {
        reason: count
        for reason, count in sorted(reason_counts.items())
        if reason not in selected_reasons
    }
    accounted_frame_count = sum(reason_counts.values())
    accounting_complete = accounted_frame_count == len(raw_frame_files)
    gap_limit = normalized_positive_int(max_snapshot_gap_seconds, 120)
    coverage_passed = (
        bool(raw_frame_files)
        and bool(selected_frames)
        and accounting_complete
        and max_observed_gap <= gap_limit
        and "unclassified" not in reason_counts
    )
    raw_manifest_sha256 = hashlib.sha256(
        json.dumps(
            raw_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "status": "completed" if coverage_passed else "incomplete",
        "coverage_passed": coverage_passed,
        "frame_interval_seconds": frame_interval_seconds,
        "max_snapshot_gap_limit_seconds": gap_limit,
        "max_selected_snapshot_gap_seconds": max_observed_gap,
        "visual_only_min_mean_delta": max(visual_only_min_mean_delta, 0.0),
        "raw_frame_count": len(raw_frame_files),
        "raw_frames_bytes": sum(path.stat().st_size for path in raw_frame_files),
        "raw_frames_manifest_sha256": raw_manifest_sha256,
        "selected_snapshot_count": len(selected_frames),
        "accounted_frame_count": accounted_frame_count,
        "accounting_complete": accounting_complete,
        "reason_counts": dict(sorted(reason_counts.items())),
        "selected_reason_counts": selected_reason_counts,
        "excluded_reason_counts": excluded_reason_counts,
        "raw_frames_retention": "completed_job_retention",
        "frames": records,
    }


def resolve_ocr_languages(
    configured_languages: str,
    detected_language: str | None,
) -> str:
    configured = configured_languages.strip().lower()
    if configured and configured != "auto":
        return configured
    detected = (detected_language or "").strip().lower()
    if detected in {"en", "eng", "english"} or detected.startswith("en-"):
        return "eng"
    if detected in {"ko", "kor", "korean"} or detected.startswith("ko-"):
        return "kor+eng"
    return "eng+kor"


def extract_frames(
    video_path: Path,
    frames_dir: Path,
    interval_seconds: int,
    ffmpeg_threads: int,
    cpu_limit_percent: int,
    cpu_limit_period_seconds: float,
    cpu_limit_fallback_burst_cores: float,
) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for existing in frames_dir.glob("frame_*.jpg"):
        existing.unlink()
    threads = normalized_positive_int(ffmpeg_threads, 1)
    run_limited(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-nostdin",
            "-y",
            "-threads",
            str(threads),
            "-filter_threads",
            str(threads),
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{interval_seconds}",
            "-threads:v",
            str(threads),
            str(frames_dir / "frame_%06d.jpg"),
        ],
        cpu_limit_percent=cpu_limit_percent,
        period_seconds=cpu_limit_period_seconds,
        fallback_burst_cores=cpu_limit_fallback_burst_cores,
        check=True,
    )


def reset_snapshots(snapshots_dir: Path) -> None:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    for existing in snapshots_dir.glob("snapshot_*.jpg"):
        existing.unlink()


def copy_snapshot(
    frame_path: Path,
    snapshots_dir: Path,
    snapshot_index: int,
    timestamp_seconds: int,
) -> Path:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    timestamp = seconds_to_timestamp(timestamp_seconds).replace(":", "-")
    snapshot_path = snapshots_dir / f"snapshot_{snapshot_index:04d}_{timestamp}.jpg"
    shutil.copy2(frame_path, snapshot_path)
    return snapshot_path


def ocr_frame(
    frame_path: Path,
    languages: str,
    tesseract_thread_limit: int,
    tesseract_nice: int,
    cpu_limit_percent: int,
    cpu_limit_period_seconds: float,
    cpu_limit_fallback_burst_cores: float,
) -> str:
    try:
        completed = run_tesseract(
            frame_path,
            languages,
            tesseract_thread_limit,
            tesseract_nice,
            cpu_limit_percent,
            cpu_limit_period_seconds,
            cpu_limit_fallback_burst_cores,
            check=True,
        )
        return completed.stdout
    except subprocess.CalledProcessError:
        if languages != "eng":
            completed = run_tesseract(
                frame_path,
                "eng",
                tesseract_thread_limit,
                tesseract_nice,
                cpu_limit_percent,
                cpu_limit_period_seconds,
                cpu_limit_fallback_burst_cores,
                check=True,
            )
            return completed.stdout
        raise


def run_tesseract(
    frame_path: Path,
    languages: str,
    tesseract_thread_limit: int,
    tesseract_nice: int,
    cpu_limit_percent: int,
    cpu_limit_period_seconds: float,
    cpu_limit_fallback_burst_cores: float,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    command = [
        "tesseract",
        str(frame_path),
        "stdout",
        "-l",
        languages,
        "--psm",
        "6",
    ]
    nice = normalized_nice(tesseract_nice)
    if nice > 0 and shutil.which("nice") is not None:
        command = ["nice", "-n", str(nice), *command]

    env = None
    thread_limit = normalized_positive_int(tesseract_thread_limit, 1)
    if thread_limit > 0:
        env = {
            **dict(os.environ),
            "OMP_THREAD_LIMIT": str(thread_limit),
            "OMP_NUM_THREADS": str(thread_limit),
        }

    return run_limited(
        command,
        cpu_limit_percent=cpu_limit_percent,
        period_seconds=cpu_limit_period_seconds,
        fallback_burst_cores=cpu_limit_fallback_burst_cores,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def visual_frame_signature(
    frame_path: Path,
    ignore_bottom_ratio: float,
    ignore_right_ratio: float,
    ffmpeg_threads: int,
    cpu_limit_percent: int,
    cpu_limit_period_seconds: float,
    cpu_limit_fallback_burst_cores: float,
) -> bytes | None:
    _, content_signature = visual_frame_signature_pair(
        frame_path,
        ignore_bottom_ratio,
        ignore_right_ratio,
        ffmpeg_threads,
        cpu_limit_percent,
        cpu_limit_period_seconds,
        cpu_limit_fallback_burst_cores,
    )
    return content_signature


def visual_frame_signature_pair(
    frame_path: Path,
    ignore_bottom_ratio: float,
    ignore_right_ratio: float,
    ffmpeg_threads: int,
    cpu_limit_percent: int,
    cpu_limit_period_seconds: float,
    cpu_limit_fallback_burst_cores: float,
) -> tuple[bytes | None, bytes | None]:
    size = 24
    height_ratio = 1.0 - normalized_ratio(ignore_bottom_ratio, 0.18)
    width_ratio = 1.0 - normalized_ratio(ignore_right_ratio, 0.20)
    threads = normalized_positive_int(ffmpeg_threads, 1)
    try:
        completed = run_limited(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-threads",
                str(threads),
                "-filter_threads",
                str(threads),
                "-i",
                str(frame_path),
                "-filter_complex",
                (
                    "[0:v]split=2[full_in][content_in];"
                    f"[full_in]scale={size}:{size},format=gray[full];"
                    "[content_in]"
                    f"crop=iw*{width_ratio:.4f}:ih*{height_ratio:.4f}:0:0,"
                    f"scale={size}:{size},format=gray[content];"
                    "[full][content]vstack=inputs=2,format=gray[out]"
                ),
                "-map",
                "[out]",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-threads:v",
                str(threads),
                "-",
            ],
            cpu_limit_percent=cpu_limit_percent,
            period_seconds=cpu_limit_period_seconds,
            fallback_burst_cores=cpu_limit_fallback_burst_cores,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None, None
    expected_size = size * size * 2
    if len(completed.stdout) != expected_size:
        return None, None
    signature_size = size * size
    return completed.stdout[:signature_size], completed.stdout[signature_size:]


def is_near_duplicate_visual(
    previous: bytes | None,
    current: bytes | None,
    max_mean_delta: float,
) -> bool:
    if previous is None or current is None:
        return False
    if len(previous) != len(current):
        return False
    return mean_pixel_delta(previous, current) <= max(max_mean_delta, 0.0)


def mean_pixel_delta(previous: bytes, current: bytes) -> float:
    if not previous or len(previous) != len(current):
        return 255.0
    return sum(abs(left - right) for left, right in zip(previous, current)) / len(previous)


def is_localized_visual_change(
    previous: bytes | None,
    current: bytes | None,
    max_mean_delta: float,
) -> bool:
    if previous is None or current is None or len(previous) != len(current):
        return False
    deltas = [abs(left - right) for left, right in zip(previous, current)]
    if not deltas:
        return False
    mean_delta = sum(deltas) / len(deltas)
    if mean_delta > max(max_mean_delta, 0.0):
        return False
    strong_pixel_count = sum(delta >= 48 for delta in deltas)
    return mean_delta >= 0.5 and strong_pixel_count >= 3


def normalized_positive_int(value: int, default: int) -> int:
    return value if value > 0 else default


def normalized_nice(value: int) -> int:
    return min(max(value, 0), 20)


def normalized_ratio(value: float, default: float) -> float:
    if value < 0:
        value = default
    return min(max(value, 0.0), 0.8)


def pause_after_frame(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def render_screen_text(frames: list[dict[str, Any]]) -> str:
    blocks = []
    for frame in frames:
        text = str(frame.get("text", "")).strip()
        if text:
            blocks.append(f"[{frame['timestamp']}]\n{text}")
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def normalize_ocr_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def is_near_duplicate(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    return normalize_for_compare(previous) == normalize_for_compare(current)


def normalize_for_compare(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())


def frame_index_to_seconds(frame_path: Path, interval_seconds: int) -> int:
    match = re.search(r"frame_(\d+)", frame_path.stem)
    if not match:
        return 0
    return (int(match.group(1)) - 1) * interval_seconds


def seconds_to_timestamp(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"
