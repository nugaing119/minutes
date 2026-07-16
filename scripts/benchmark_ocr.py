from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Sequence

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings
from scripts.ocr import run_ocr
from scripts.utils import write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cpu_seconds() -> float:
    usage = os.times()
    return usage.user + usage.system + usage.children_user + usage.children_system


def snapshot_manifest(job_dir: Path) -> dict[str, Any]:
    records = []
    for path in sorted((job_dir / "snapshots").glob("snapshot_*.jpg")):
        records.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    canonical = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "count": len(records),
        "bytes": sum(record["bytes"] for record in records),
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
        "files": records,
    }


def build_benchmark_report(
    *,
    video_path: Path,
    job_dir: Path,
    result: dict[str, Any],
    coverage: dict[str, Any],
    wall_seconds: float,
    cpu_seconds_used: float,
) -> dict[str, Any]:
    metrics = result.get("metrics", {})
    phases = metrics.get("phases", {}) if isinstance(metrics, dict) else {}
    return {
        "schema_version": 1,
        "benchmark": "ocr_pipeline",
        "video": {
            "path": str(video_path),
            "bytes": video_path.stat().st_size,
            "sha256": sha256_file(video_path),
        },
        "job_dir": str(job_dir),
        "runtime": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pid": os.getpid(),
            "observed_process_nice": os.getpriority(os.PRIO_PROCESS, 0),
        },
        "configuration": {
            "frame_interval_seconds": result.get("frame_interval_seconds"),
            "ffmpeg_threads": result.get("ffmpeg_threads"),
            "workers": result.get("workers"),
            "tesseract_thread_limit": result.get("tesseract_thread_limit"),
            "tesseract_nice_increment": result.get("tesseract_nice"),
            "frame_extract_cpu_limit_percent": result.get(
                "frame_extract_cpu_limit_percent"
            ),
            "signature_cpu_limit_percent": result.get(
                "signature_cpu_limit_percent"
            ),
            "tesseract_cpu_limit_percent": result.get(
                "tesseract_cpu_limit_percent"
            ),
            "max_snapshot_gap_seconds": result.get("max_snapshot_gap_seconds"),
        },
        "timing": {
            "wall_seconds": round(wall_seconds, 6),
            "cpu_seconds": round(cpu_seconds_used, 6),
            "cpu_to_wall_ratio": round(cpu_seconds_used / wall_seconds, 6)
            if wall_seconds > 0
            else None,
            "phases": phases,
        },
        "work": {
            "raw_frame_count": result.get("raw_frame_count"),
            "raw_frames_bytes": result.get("raw_frames_bytes"),
            "selection_reason_counts": result.get("selection_reason_counts", {}),
            "selection_result_sha256": result.get("selection_result_sha256"),
            "coverage_passed": coverage.get("coverage_passed"),
            "accounting_complete": coverage.get("accounting_complete"),
            "max_selected_snapshot_gap_seconds": coverage.get(
                "max_selected_snapshot_gap_seconds"
            ),
            "raw_frames_manifest_sha256": coverage.get(
                "raw_frames_manifest_sha256"
            ),
            "snapshots": snapshot_manifest(job_dir),
        },
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run a reproducible OCR-only benchmark and write bounded JSON metrics."
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--detected-language", default="ko")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--ffmpeg-threads", type=int)
    parser.add_argument("--frame-interval", type=int)
    parser.add_argument("--frame-extract-cap", type=int)
    parser.add_argument("--tesseract-nice", type=int)
    args = parser.parse_args(list(argv) if argv is not None else None)

    video_path = args.video.expanduser().resolve()
    job_dir = args.job_dir.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else job_dir / "ocr_benchmark.json"
    )
    if not video_path.is_file():
        parser.error(f"video does not exist: {video_path}")
    if job_dir.exists() and any(job_dir.iterdir()):
        parser.error(f"job_dir must be empty: {job_dir}")
    job_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    overrides: dict[str, Any] = {}
    if args.workers is not None:
        overrides["ocr_workers"] = args.workers
    if args.ffmpeg_threads is not None:
        overrides["ocr_ffmpeg_threads"] = args.ffmpeg_threads
    if args.frame_interval is not None:
        overrides["ocr_frame_interval_seconds"] = args.frame_interval
    if args.frame_extract_cap is not None:
        overrides["ocr_frame_extract_cpu_limit_percent"] = args.frame_extract_cap
    if args.tesseract_nice is not None:
        overrides["ocr_tesseract_nice"] = args.tesseract_nice
    settings = replace(settings, **overrides)

    started_wall = time.perf_counter()
    started_cpu = cpu_seconds()
    result = run_ocr(
        video_path,
        job_dir,
        settings,
        detected_language=args.detected_language,
    )
    wall_seconds = time.perf_counter() - started_wall
    cpu_seconds_used = cpu_seconds() - started_cpu
    coverage = json.loads(
        (job_dir / "evidence_coverage.json").read_text(encoding="utf-8")
    )
    report = build_benchmark_report(
        video_path=video_path,
        job_dir=job_dir,
        result=result,
        coverage=coverage,
        wall_seconds=wall_seconds,
        cpu_seconds_used=cpu_seconds_used,
    )
    write_json(output_path, report)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "wall_seconds": report["timing"]["wall_seconds"],
                "cpu_seconds": report["timing"]["cpu_seconds"],
                "raw_frame_count": report["work"]["raw_frame_count"],
                "snapshot_count": report["work"]["snapshots"]["count"],
                "coverage_passed": report["work"]["coverage_passed"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
