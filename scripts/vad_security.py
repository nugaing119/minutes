from __future__ import annotations

import hashlib
import json
import stat
from importlib import metadata
from pathlib import Path
from typing import Any


MODEL_VERSION = "6.2.1"
MODEL_DIRECTORY_NAME = f"silero-vad-{MODEL_VERSION}"
MODEL_FILENAME = "silero_vad.onnx"
MODEL_SIZE = 2_327_524
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
MANIFEST_FILENAME = "manifest.json"
ONNXRUNTIME_VERSION = "1.27.0"
WHEEL_FILENAME = "silero_vad-6.2.1-py3-none-any.whl"
WHEEL_SIZE = 9_146_242
WHEEL_SHA256 = "09de93c4d874bb19c53e62a47dd38be5f163cedad2b5599583231f2a84ef79cb"
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/0b/2b/"
    "48566f29a8b53d856ceb1994f209122749b3fda0a733a07e82047257de7a/"
    + WHEEL_FILENAME
)
WHEEL_MODEL_MEMBER = "silero_vad/data/silero_vad.onnx"


class VadSecurityError(RuntimeError):
    pass


def expected_model_dir(minutes_home: Path) -> Path:
    return minutes_home.expanduser() / "models" / MODEL_DIRECTORY_NAME


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "backend": "silero-vad-onnx",
        "model": {
            "version": MODEL_VERSION,
            "filename": MODEL_FILENAME,
            "size": MODEL_SIZE,
            "sha256": MODEL_SHA256,
            "license": "MIT",
            "source_wheel": WHEEL_FILENAME,
            "source_wheel_sha256": WHEEL_SHA256,
        },
        "runtime": {
            "package": "onnxruntime",
            "version": ONNXRUNTIME_VERSION,
            "provider": "CPUExecutionProvider",
            "threads": 1,
            "network": "disabled",
            "remote_code": False,
            "purpose": "speech_presence_validation_only",
            "speaker_identity": False,
        },
    }


def write_manifest(model_dir: Path) -> None:
    path = model_dir / MANIFEST_FILENAME
    path.write_text(
        json.dumps(canonical_manifest(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def verify_runtime_package() -> str:
    try:
        actual = metadata.version("onnxruntime")
    except metadata.PackageNotFoundError as exc:
        raise ModuleNotFoundError(
            f"onnxruntime=={ONNXRUNTIME_VERSION} is required for speech validation"
        ) from exc
    if actual != ONNXRUNTIME_VERSION:
        raise VadSecurityError(
            f"Unexpected onnxruntime version: {actual}; expected {ONNXRUNTIME_VERSION}"
        )
    return actual


def verify_model_file(path: Path) -> None:
    _require_plain_file(path)
    if path.stat().st_size != MODEL_SIZE:
        raise VadSecurityError("Silero VAD model size mismatch")
    if sha256_file(path) != MODEL_SHA256:
        raise VadSecurityError("Silero VAD model hash mismatch")


def verify_model_dir(model_dir: Path) -> dict[str, Any]:
    _require_plain_directory(model_dir)
    expected = {MODEL_FILENAME, MANIFEST_FILENAME}
    actual = {path.name for path in model_dir.iterdir()}
    if actual != expected:
        raise VadSecurityError(
            f"Unexpected Silero VAD model contents; missing={sorted(expected-actual)}, "
            f"unexpected={sorted(actual-expected)}"
        )
    verify_model_file(model_dir / MODEL_FILENAME)
    manifest_path = model_dir / MANIFEST_FILENAME
    _require_plain_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VadSecurityError("Invalid Silero VAD manifest") from exc
    if manifest != canonical_manifest():
        raise VadSecurityError("Silero VAD manifest mismatch")
    return manifest


def model_is_prepared(model_dir: Path) -> bool:
    try:
        verify_model_dir(model_dir)
    except (FileNotFoundError, NotADirectoryError, VadSecurityError):
        return False
    return True


def _require_plain_directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Silero VAD model directory is missing: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise VadSecurityError(f"Silero VAD model directory must be a real directory: {path}")


def _require_plain_file(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Silero VAD model file is missing: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise VadSecurityError(f"Silero VAD artifact must be a regular file: {path.name}")
