from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from scripts.config import Settings
from scripts.cpu_limit import run_limited
from scripts.utils import write_json


def run_ocr(video_path: Path, job_dir: Path, settings: Settings) -> dict[str, Any]:
    screen_text_path = job_dir / "screen_text.json"
    screen_text_txt_path = job_dir / "screen_text.txt"

    if not settings.ocr_enabled:
        result = {"enabled": False, "status": "disabled", "frames": []}
        write_json(screen_text_path, result)
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
        screen_text_txt_path.write_text("", encoding="utf-8")
        return result

    frames_dir = job_dir / "frames"
    snapshots_dir = job_dir / "snapshots"
    extract_frames(
        video_path,
        frames_dir,
        settings.ocr_frame_interval_seconds,
        settings.ocr_ffmpeg_threads,
        settings.ocr_frame_extract_cpu_limit_percent,
        settings.ocr_frame_extract_cpu_limit_period_seconds,
        settings.ocr_frame_extract_cpu_limit_fallback_burst_cores,
    )
    reset_snapshots(snapshots_dir)
    frames = []
    previous_text = ""
    previous_visual_signature: bytes | None = None
    snapshot_index = 0

    for frame_path in sorted(frames_dir.glob("frame_*.jpg")):
        timestamp_seconds = frame_index_to_seconds(
            frame_path,
            settings.ocr_frame_interval_seconds,
        )
        visual_signature = None
        if settings.ocr_visual_dedupe_enabled:
            visual_signature = visual_frame_signature(
                frame_path,
                settings.ocr_visual_dedupe_ignore_bottom_ratio,
            settings.ocr_visual_dedupe_ignore_right_ratio,
            settings.ocr_ffmpeg_threads,
            settings.ocr_signature_cpu_limit_percent,
            settings.ocr_signature_cpu_limit_period_seconds,
            settings.ocr_signature_cpu_limit_fallback_burst_cores,
        )
            if is_near_duplicate_visual(
                previous_visual_signature,
                visual_signature,
                settings.ocr_visual_dedupe_max_mean_delta,
            ):
                pause_after_frame(settings.ocr_frame_pause_seconds)
                continue

        text = ocr_frame(
            frame_path,
            settings.ocr_languages,
            settings.ocr_tesseract_thread_limit,
            settings.ocr_tesseract_nice,
            settings.ocr_tesseract_cpu_limit_percent,
            settings.ocr_tesseract_cpu_limit_period_seconds,
            settings.ocr_tesseract_cpu_limit_fallback_burst_cores,
        )
        text = normalize_ocr_text(text)
        if not text:
            pause_after_frame(settings.ocr_frame_pause_seconds)
            continue
        if is_near_duplicate(previous_text, text):
            pause_after_frame(settings.ocr_frame_pause_seconds)
            continue
        previous_text = text
        if visual_signature is not None:
            previous_visual_signature = visual_signature
        snapshot_index += 1
        snapshot_path = copy_snapshot(
            frame_path,
            snapshots_dir,
            snapshot_index,
            timestamp_seconds,
        )
        frames.append(
            {
                "timestamp_seconds": timestamp_seconds,
                "timestamp": seconds_to_timestamp(timestamp_seconds),
                "frame": str(frame_path),
                "snapshot": str(snapshot_path),
                "text": text,
            }
        )
        pause_after_frame(settings.ocr_frame_pause_seconds)

    result = {
        "enabled": True,
        "status": "completed",
        "frame_interval_seconds": settings.ocr_frame_interval_seconds,
        "languages": settings.ocr_languages,
        "ffmpeg_threads": normalized_positive_int(settings.ocr_ffmpeg_threads, 1),
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
        "snapshots_dir": str(snapshots_dir),
        "frames": frames,
    }
    write_json(screen_text_path, result)
    screen_text_txt_path.write_text(render_screen_text(frames), encoding="utf-8")
    return result


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
            "-y",
            "-threads",
            str(threads),
            "-filter_threads",
            str(threads),
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{interval_seconds}",
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
                "-i",
                str(frame_path),
                "-vf",
                (
                    f"crop=iw*{width_ratio:.4f}:ih*{height_ratio:.4f}:0:0,"
                    f"scale={size}:{size},format=gray"
                ),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ],
            cpu_limit_percent=cpu_limit_percent,
            period_seconds=cpu_limit_period_seconds,
            fallback_burst_cores=cpu_limit_fallback_burst_cores,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    expected_size = size * size
    if len(completed.stdout) != expected_size:
        return None
    return completed.stdout


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
        blocks.append(f"[{frame['timestamp']}]\n{frame['text']}")
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
