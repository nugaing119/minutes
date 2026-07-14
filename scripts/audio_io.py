from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def load_pcm_wav(
    audio_path: Path,
    *,
    expected_sample_rate: int | None = None,
) -> tuple[np.ndarray, int]:
    """Load the pipeline's mono 16-bit PCM WAV without importing STT/MLX."""
    with wave.open(str(audio_path), "rb") as audio_file:
        channels = audio_file.getnchannels()
        sample_width = audio_file.getsampwidth()
        sample_rate = audio_file.getframerate()
        compression = audio_file.getcomptype()
        frame_count = audio_file.getnframes()
        raw_audio = audio_file.readframes(frame_count)

    if channels != 1:
        raise ValueError(f"Expected mono PCM WAV, got {channels} channels: {audio_path}")
    if sample_width != 2 or compression != "NONE":
        raise ValueError(f"Expected uncompressed 16-bit PCM WAV: {audio_path}")
    if expected_sample_rate is not None and sample_rate != expected_sample_rate:
        raise ValueError(
            f"Expected {expected_sample_rate} Hz audio, got {sample_rate} Hz: {audio_path}"
        )

    waveform = np.frombuffer(raw_audio, dtype="<i2").astype(np.float32)
    waveform *= 1.0 / 32768.0
    return waveform, sample_rate
