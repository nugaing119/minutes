from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from scripts.speech_activity import (
    infer_probabilities,
    probabilities_to_regions,
    suspect_transcript_segments,
    validate_transcript_speech,
)
from scripts import vad_security


class FakeVad:
    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = iter(probabilities)

    def reset(self) -> None:
        pass

    def predict(self, chunk: np.ndarray, sample_rate: int) -> float:
        self.last_shape = chunk.shape
        return next(self.probabilities)


class SpeechActivityTests(unittest.TestCase):
    def test_inference_pads_final_window_without_torch(self) -> None:
        model = FakeVad([0.1, 0.9])
        probabilities = infer_probabilities(
            np.zeros(700, dtype=np.float32),
            16_000,
            model,
        )
        self.assertEqual(probabilities, [0.1, 0.9])
        self.assertEqual(model.last_shape, (512,))

    def test_regions_require_sustained_speech_and_silence(self) -> None:
        probabilities = [0.0] * 2 + [0.9] * 12 + [0.0] * 5
        regions = probabilities_to_regions(
            probabilities,
            len(probabilities) * 512,
            16_000,
        )
        self.assertEqual(len(regions), 1)
        self.assertLess(regions[0]["start"], regions[0]["end"])

    def test_only_clear_non_speech_transcript_is_flagged(self) -> None:
        probabilities = [0.02] * 40 + [0.9] * 40
        suspects = suspect_transcript_segments(
            [
                {"start": 0.0, "end": 1.0, "text": "hallucination"},
                {"start": 1.4, "end": 2.2, "text": "speech"},
            ],
            probabilities,
            16_000,
        )
        self.assertEqual([item["text"] for item in suspects], ["hallucination"])

    def test_missing_optional_model_is_reported_without_modifying_transcript(self) -> None:
        settings = SimpleNamespace(
            speech_activity_validation_enabled=True,
            vad_model_dir=Path("/definitely/missing/silero"),
        )
        report = validate_transcript_speech(
            np.zeros(512, dtype=np.float32),
            16_000,
            {"segments": []},
            settings,
        )
        self.assertEqual(report["status"], "skipped")
        self.assertFalse(report["transcript_modified"])
        self.assertFalse(report["speaker_identity"])

    def test_unsupported_audio_is_reported_without_stopping_the_pipeline(self) -> None:
        settings = SimpleNamespace(
            speech_activity_validation_enabled=True,
            vad_model_dir=Path("/validated/in-test"),
        )
        with (
            patch("scripts.speech_activity.verify_runtime_package", return_value="1.27.0"),
            patch("scripts.speech_activity.verify_model_dir"),
            patch(
                "scripts.speech_activity.SileroOnnxVad",
                return_value=FakeVad([]),
            ),
        ):
            report = validate_transcript_speech(
                np.zeros(512, dtype=np.float32),
                8_000,
                {"segments": []},
                settings,
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_type"], "ValueError")
        self.assertFalse(report["transcript_modified"])

    def test_model_manifest_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = root / vad_security.MODEL_FILENAME
            model.write_bytes(b"verified-model")
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            with (
                patch.object(vad_security, "MODEL_SIZE", model.stat().st_size),
                patch.object(vad_security, "MODEL_SHA256", digest),
            ):
                vad_security.write_manifest(root)
                vad_security.verify_model_dir(root)
                model.write_bytes(b"tampered-model")
                with self.assertRaises(vad_security.VadSecurityError):
                    vad_security.verify_model_dir(root)


if __name__ == "__main__":
    unittest.main()
