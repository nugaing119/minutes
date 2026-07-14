from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


class TranscribeTests(unittest.TestCase):
    def _load_module(self) -> tuple[object, types.ModuleType]:
        fake_mlx_whisper = types.ModuleType("mlx_whisper")
        sys.modules.pop("scripts.transcribe", None)
        with patch.dict(sys.modules, {"mlx_whisper": fake_mlx_whisper}):
            module = importlib.import_module("scripts.transcribe")
        return module, fake_mlx_whisper

    def test_transcribe_writes_json_text_and_srt(self) -> None:
        module, fake_mlx_whisper = self._load_module()
        result = {
            "text": " 첫 번째 발언 두 번째 발언 ",
            "segments": [
                {"start": 0.0, "end": 1.25, "text": "첫 번째 발언"},
                {"start": 1.25, "end": 2.5, "text": "두 번째 발언"},
            ],
        }
        calls: list[tuple[object, dict[str, object]]] = []

        def fake_transcribe(audio: object, **kwargs: object) -> dict:
            calls.append((audio, kwargs))
            return result

        fake_mlx_whisper.transcribe = fake_transcribe
        settings = SimpleNamespace(
            whisper_device="auto",
            whisper_model="local-model",
            language="auto",
            speaker_attribution_mode="off",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "audio.wav"
            transcript_path = root / "transcript.json"
            audio_path.write_bytes(b"fixture")
            waveform = np.array([0.0, 0.25, -0.25], dtype=np.float32)

            returned = module.transcribe_audio(
                audio_path,
                transcript_path,
                settings,
                waveform=waveform,
            )

            self.assertEqual(returned, result)
            self.assertEqual(json.loads(transcript_path.read_text(encoding="utf-8")), result)
            self.assertEqual(
                transcript_path.with_suffix(".txt").read_text(encoding="utf-8"),
                "첫 번째 발언 두 번째 발언\n",
            )
            srt = transcript_path.with_suffix(".srt").read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,250", srt)
            self.assertIn("첫 번째 발언", srt)

        self.assertIs(calls[0][0], waveform)
        self.assertEqual(calls[0][1], {"path_or_hf_repo": "local-model"})

    def test_evidence_mode_keeps_segment_timestamps_without_word_cost(self) -> None:
        module, fake_mlx_whisper = self._load_module()
        result = {
            "text": "안녕하세요",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "안녕하세요",
                }
            ],
        }
        calls: list[dict[str, object]] = []

        def fake_transcribe(audio: object, **kwargs: object) -> dict:
            calls.append(kwargs)
            return result

        fake_mlx_whisper.transcribe = fake_transcribe
        settings = SimpleNamespace(
            whisper_device="auto",
            whisper_model="local-model",
            language="ko",
            speaker_attribution_mode="evidence",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "audio.wav"
            transcript_path = root / "transcript.json"
            audio_path.write_bytes(b"fixture")
            returned = module.transcribe_audio(
                audio_path,
                transcript_path,
                settings,
                waveform=np.zeros(16, dtype=np.float32),
            )

        self.assertEqual(returned["timing_precision"], "segment")
        self.assertEqual(
            calls,
            [
                {
                    "path_or_hf_repo": "local-model",
                    "language": "ko",
                }
            ],
        )

    def test_load_pcm_wav_returns_normalized_mono_float32(self) -> None:
        module, _fake_mlx_whisper = self._load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.wav"
            with wave.open(str(audio_path), "wb") as audio_file:
                audio_file.setnchannels(1)
                audio_file.setsampwidth(2)
                audio_file.setframerate(16000)
                audio_file.writeframes(np.array([-32768, 0, 16384], dtype="<i2").tobytes())

            waveform, sample_rate = module.load_pcm_wav(
                audio_path,
                expected_sample_rate=16000,
            )

        self.assertEqual(sample_rate, 16000)
        self.assertEqual(waveform.dtype, np.float32)
        np.testing.assert_allclose(waveform, [-1.0, 0.0, 0.5])


if __name__ == "__main__":
    unittest.main()
