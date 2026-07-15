from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.community1_governance import (
    GovernanceError,
    MODEL_ID,
    evaluate_community1_governance,
    offline_runtime_environment,
)
from scripts.utils import write_json


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Community1GovernanceTests(unittest.TestCase):
    def _approval(self, root: Path) -> Path:
        capture = root / "gate-capture.txt"
        capture.write_text("captured gated repository terms", encoding="utf-8")
        approval = root / "approval.json"
        write_json(
            approval,
            {
                "schema_version": 1,
                "model_id": MODEL_ID,
                "license": "CC BY 4.0",
                "gate_text_sha256": digest(b"gated terms"),
                "gate_capture_path": capture.name,
                "accepted_at": "2026-07-15T09:00:00+09:00",
                "accepted_by_hf_user": "named.employee",
                "company_owner": "ML Platform Security",
                "approved_use": "internal meeting transcription",
                "source_url": (
                    "https://huggingface.co/pyannote/"
                    "speaker-diarization-community-1"
                ),
            },
        )
        return approval

    def _model_mirror(self, root: Path, approval: Path) -> Path:
        model_dir = root / "model"
        model_dir.mkdir()
        model_card = model_dir / "README.md"
        attribution = model_dir / "CC-BY-4.0.txt"
        weights = model_dir / "model.bin"
        model_card.write_text("model card", encoding="utf-8")
        attribution.write_text("CC BY 4.0 attribution", encoding="utf-8")
        weights.write_bytes(b"fixed test weights")
        files = [model_card, attribution, weights]
        write_json(
            model_dir / "model_manifest.json",
            {
                "schema_version": 1,
                "model_id": MODEL_ID,
                "license": "CC BY 4.0",
                "revision": "0123456789abcdef0123456789abcdef01234567",
                "approval_artifact_sha256": digest(approval.read_bytes()),
                "model_card_path": model_card.name,
                "attribution_path": attribution.name,
                "files": [
                    {
                        "path": path.name,
                        "sha256": digest(path.read_bytes()),
                    }
                    for path in files
                ],
            },
        )
        return model_dir

    def test_missing_approval_disables_download_and_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = evaluate_community1_governance(
                root / "missing.json",
                root / "model",
            )

        self.assertEqual(result["status"], "disabled_by_governance")
        self.assertFalse(result["activation_allowed"])
        self.assertEqual(result["model_download"], "forbidden")
        self.assertEqual(result["network_access"], "forbidden")

    def test_valid_approval_without_mirror_is_skipped_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            approval = self._approval(root)

            result = evaluate_community1_governance(approval, root / "model")

        self.assertEqual(result["status"], "skipped_unavailable")
        self.assertFalse(result["activation_allowed"])
        self.assertNotIn("accepted_by_hf_user", json.dumps(result))

    def test_hash_verified_mirror_is_ready_only_for_offline_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            approval = self._approval(root)
            model_dir = self._model_mirror(root, approval)

            result = evaluate_community1_governance(approval, model_dir)

        self.assertEqual(result["status"], "ready_offline")
        self.assertTrue(result["activation_allowed"])
        self.assertEqual(result["runtime_environment"]["HF_HUB_OFFLINE"], "1")
        self.assertEqual(result["runtime_environment"]["PYANNOTE_METRICS_ENABLED"], "0")
        self.assertEqual(result["model"]["file_count"], 3)

    def test_tampered_model_file_blocks_offline_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            approval = self._approval(root)
            model_dir = self._model_mirror(root, approval)
            (model_dir / "model.bin").write_bytes(b"tampered")

            result = evaluate_community1_governance(approval, model_dir)

        self.assertEqual(result["status"], "skipped_unavailable")
        self.assertIn("hash mismatch", result["reason"])

    def test_job_runtime_rejects_hugging_face_token_environment(self) -> None:
        with self.assertRaisesRegex(GovernanceError, "must not receive"):
            offline_runtime_environment({"HF_TOKEN": "hf_private_value"})

        runtime = offline_runtime_environment({"PATH": "/usr/bin"})
        self.assertEqual(runtime["PATH"], "/usr/bin")
        self.assertEqual(runtime["HF_HUB_OFFLINE"], "1")
        self.assertNotIn("HF_TOKEN", runtime)

    def test_secret_like_value_in_approval_is_never_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            approval = self._approval(root)
            value = json.loads(approval.read_text(encoding="utf-8"))
            value["approved_use"] = "hf_1234567890secret"
            write_json(approval, value)

            result = evaluate_community1_governance(approval, root / "model")

        self.assertEqual(result["status"], "disabled_by_governance")
        self.assertNotIn("hf_1234567890secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
