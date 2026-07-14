from __future__ import annotations

import json
from pathlib import Path

import mlx_whisper
import numpy as np

from scripts.config import Settings
from scripts.cpu_limit import run_limited
from scripts.audio_io import load_pcm_wav
from scripts.utils import format_timestamp


def extract_audio(video_path: Path, audio_path: Path, settings: Settings) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    run_limited(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-threads",
            str(settings.audio_ffmpeg_threads),
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(settings.audio_sample_rate),
            "-c:a",
            "pcm_s16le",
            "-threads:a",
            str(settings.audio_ffmpeg_threads),
            str(audio_path),
        ],
        cpu_limit_percent=settings.audio_cpu_limit_percent,
        period_seconds=settings.audio_cpu_limit_period_seconds,
        fallback_burst_cores=settings.audio_cpu_limit_fallback_burst_cores,
        check=True,
    )


def transcribe_audio(
    audio_path: Path,
    transcript_path: Path,
    settings: Settings,
    *,
    waveform: np.ndarray | None = None,
) -> dict:
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    configure_mlx_device(settings.whisper_device)
    if waveform is None:
        waveform, _sample_rate = load_pcm_wav(
            audio_path,
            expected_sample_rate=getattr(settings, "audio_sample_rate", None),
        )
    transcribe_kwargs = {
        "path_or_hf_repo": settings.whisper_model,
    }
    if settings.language and settings.language.lower() != "auto":
        transcribe_kwargs["language"] = settings.language
    speaker_mode = getattr(settings, "speaker_attribution_mode", "off")
    result = mlx_whisper.transcribe(waveform, **transcribe_kwargs)
    if speaker_mode == "evidence":
        result["timing_precision"] = "segment"
    transcript_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    transcript_path.with_suffix(".txt").write_text(
        result.get("text", "").strip() + "\n",
        encoding="utf-8",
    )
    transcript_path.with_suffix(".srt").write_text(
        to_srt(result),
        encoding="utf-8",
    )
    return result


def configure_mlx_device(device: str) -> None:
    if device == "auto":
        return
    import mlx.core as mx

    if device == "gpu":
        mx.set_default_device(mx.gpu)
    elif device == "cpu":
        mx.set_default_device(mx.cpu)
    else:
        raise ValueError(f"Unsupported WHISPER_DEVICE: {device}")


def to_srt(result: dict) -> str:
    lines: list[str] = []
    for index, segment in enumerate(result.get("segments", []), start=1):
        start = format_timestamp(float(segment.get("start", 0)))
        end = format_timestamp(float(segment.get("end", 0)))
        text = str(segment.get("text", "")).strip()
        lines.extend([str(index), f"{start} --> {end}", text, ""])
    return "\n".join(lines)
