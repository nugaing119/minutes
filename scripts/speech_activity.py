from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from scripts.vad_security import (
    MODEL_FILENAME,
    VadSecurityError,
    verify_model_dir,
    verify_runtime_package,
)


WINDOW_SAMPLES = 512
CONTEXT_SAMPLES = 64


class SileroOnnxVad:
    def __init__(self, model_path: Path) -> None:
        try:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            self.session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Silero VAD runtime initialization failed: {type(exc).__name__}: {exc}"
            ) from exc
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)

    def predict(self, chunk: np.ndarray, sample_rate: int = 16_000) -> float:
        values = np.asarray(chunk, dtype=np.float32).reshape(1, -1)
        if sample_rate != 16_000 or values.shape[1] != WINDOW_SAMPLES:
            raise ValueError("Silero VAD requires 512-sample chunks at 16000 Hz")
        model_input = np.concatenate((self.context, values), axis=1)
        try:
            output, next_state = self.session.run(
                None,
                {
                    "input": model_input,
                    "state": self.state,
                    "sr": np.asarray(sample_rate, dtype=np.int64),
                },
            )
        except Exception as exc:
            raise RuntimeError(
                f"Silero VAD inference failed: {type(exc).__name__}: {exc}"
            ) from exc
        probability = float(np.asarray(output).reshape(-1)[0])
        next_state = np.asarray(next_state, dtype=np.float32)
        if not np.isfinite(probability) or not np.isfinite(next_state).all():
            raise RuntimeError("Silero VAD returned non-finite values")
        self.state = next_state
        self.context = values[:, -CONTEXT_SAMPLES:]
        return probability


def validate_transcript_speech(
    waveform: np.ndarray,
    sample_rate: int,
    transcript: dict[str, Any],
    settings: Any,
) -> dict[str, Any]:
    if not getattr(settings, "speech_activity_validation_enabled", True):
        return _base_report("disabled")
    model_dir = Path(settings.vad_model_dir).expanduser().absolute()
    try:
        runtime_version = verify_runtime_package()
        verify_model_dir(model_dir)
        model = SileroOnnxVad(model_dir / MODEL_FILENAME)
        probabilities = infer_probabilities(waveform, sample_rate, model)
        regions = probabilities_to_regions(probabilities, len(waveform), sample_rate)
        suspects = suspect_transcript_segments(
            transcript.get("segments", []), probabilities, sample_rate
        )
    except (FileNotFoundError, ModuleNotFoundError, VadSecurityError) as exc:
        return {
            **_base_report("skipped"),
            "reason": str(exc),
            "error_type": type(exc).__name__,
        }
    except (RuntimeError, ValueError) as exc:
        return {
            **_base_report("failed"),
            "reason": str(exc),
            "error_type": type(exc).__name__,
        }

    speech_seconds = sum(item["end"] - item["start"] for item in regions)
    audio_seconds = len(waveform) / sample_rate if sample_rate else 0.0
    return {
        **_base_report("completed"),
        "model_version": "6.2.1",
        "runtime": f"onnxruntime=={runtime_version}",
        "threads": 1,
        "audio_duration_seconds": round(audio_seconds, 3),
        "speech_duration_seconds": round(speech_seconds, 3),
        "speech_ratio": round(speech_seconds / audio_seconds, 4) if audio_seconds else 0.0,
        "speech_region_count": len(regions),
        "regions": regions,
        "transcript_segment_count": len(transcript.get("segments", [])),
        "suspect_segment_count": len(suspects),
        "suspect_segments": suspects,
    }


def _base_report(status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "enabled": status != "disabled",
        "status": status,
        "purpose": "speech_presence_validation_only",
        "speaker_identity": False,
        "speaker_diarization": False,
        "transcript_modified": False,
    }


def infer_probabilities(
    waveform: np.ndarray,
    sample_rate: int,
    model: SileroOnnxVad,
) -> list[float]:
    if sample_rate != 16_000:
        raise ValueError("Speech activity validation requires 16000 Hz audio")
    values = np.asarray(waveform, dtype=np.float32)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError("Speech activity validation requires finite mono audio")
    model.reset()
    probabilities = []
    for offset in range(0, len(values), WINDOW_SAMPLES):
        chunk = values[offset : offset + WINDOW_SAMPLES]
        if len(chunk) < WINDOW_SAMPLES:
            chunk = np.pad(chunk, (0, WINDOW_SAMPLES - len(chunk)))
        probabilities.append(model.predict(chunk, sample_rate))
    return probabilities


def probabilities_to_regions(
    probabilities: list[float],
    audio_samples: int,
    sample_rate: int,
    *,
    threshold: float = 0.5,
    min_speech_ms: int = 250,
    min_silence_ms: int = 100,
    pad_ms: int = 30,
) -> list[dict[str, float]]:
    neg_threshold = max(threshold - 0.15, 0.01)
    min_speech = sample_rate * min_speech_ms / 1000
    min_silence = sample_rate * min_silence_ms / 1000
    pad = int(sample_rate * pad_ms / 1000)
    triggered = False
    start = 0
    possible_end: int | None = None
    raw: list[tuple[int, int]] = []
    for index, probability in enumerate(probabilities):
        current = index * WINDOW_SAMPLES
        if probability >= threshold:
            if not triggered:
                triggered = True
                start = current
            possible_end = None
        elif triggered and probability < neg_threshold:
            if possible_end is None:
                possible_end = current
            if current - possible_end >= min_silence:
                if possible_end - start > min_speech:
                    raw.append((start, possible_end))
                triggered = False
                possible_end = None
    if triggered and audio_samples - start > min_speech:
        raw.append((start, audio_samples))

    padded: list[tuple[int, int]] = []
    for start, end in raw:
        start = max(0, start - pad)
        end = min(audio_samples, end + pad)
        if padded and start <= padded[-1][1]:
            padded[-1] = (padded[-1][0], max(padded[-1][1], end))
        else:
            padded.append((start, end))
    return [
        {"start": round(start / sample_rate, 3), "end": round(end / sample_rate, 3)}
        for start, end in padded
    ]


def suspect_transcript_segments(
    segments: list[Any],
    probabilities: list[float],
    sample_rate: int,
) -> list[dict[str, Any]]:
    window_seconds = WINDOW_SAMPLES / sample_rate
    suspects = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = max(float(segment.get("start", 0.0)), 0.0)
        end = max(float(segment.get("end", start)), start)
        first = max(int(start / window_seconds), 0)
        last = min(int(np.ceil(end / window_seconds)), len(probabilities))
        values = probabilities[first:last]
        if not values:
            continue
        maximum = max(values)
        mean = sum(values) / len(values)
        no_speech = float(segment.get("no_speech_prob", 0.0) or 0.0)
        reason = None
        if maximum < 0.15 and mean < 0.08:
            reason = "vad_detected_no_clear_speech"
        elif maximum < 0.35 and no_speech >= 0.60:
            reason = "vad_and_whisper_no_speech_agree"
        if reason is None:
            continue
        suspects.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": str(segment.get("text", "")).strip(),
                "vad_mean_probability": round(mean, 4),
                "vad_max_probability": round(maximum, 4),
                "whisper_no_speech_probability": round(no_speech, 4),
                "reason": reason,
            }
        )
    return suspects


def render_validation_evidence(report: dict[str, Any]) -> str:
    if report.get("status") != "completed":
        return ""
    suspects = report.get("suspect_segments", [])
    if not suspects:
        return (
            "Silero VAD found no transcript segments that clearly occurred over "
            "non-speech. This is validation only, not speaker identity evidence."
        )
    lines = [
        "Silero VAD marked the following transcript segments for review. Do not "
        "delete or rewrite them automatically, and never use this as speaker identity evidence."
    ]
    for item in suspects:
        lines.append(
            f"- [{item['start']:.3f} - {item['end']:.3f}] "
            f"VAD max={item['vad_max_probability']:.4f}, "
            f"Whisper no_speech={item['whisper_no_speech_probability']:.4f}: "
            f"{item['text']}"
        )
    return "\n".join(lines)
