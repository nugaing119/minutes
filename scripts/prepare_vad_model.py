from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings
from scripts.vad_security import (
    MODEL_FILENAME,
    MODEL_SHA256,
    MODEL_SIZE,
    WHEEL_MODEL_MEMBER,
    WHEEL_SHA256,
    WHEEL_SIZE,
    WHEEL_URL,
    VadSecurityError,
    model_is_prepared,
    sha256_file,
    verify_model_dir,
    verify_model_file,
    verify_runtime_package,
    write_manifest,
)


def download_wheel(destination: Path) -> None:
    request = urllib.request.Request(
        WHEEL_URL,
        headers={"User-Agent": "minutes-silero-vad-preparer/1"},
    )
    written = 0
    with urllib.request.urlopen(request, timeout=60) as response:
        with destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                written += len(chunk)
                if written > WHEEL_SIZE:
                    raise VadSecurityError("Silero VAD wheel exceeds pinned size")
                output.write(chunk)
    if written != WHEEL_SIZE or sha256_file(destination) != WHEEL_SHA256:
        raise VadSecurityError("Silero VAD wheel integrity check failed")


def extract_model_from_wheel(wheel_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(wheel_path) as archive:
        info = archive.getinfo(WHEEL_MODEL_MEMBER)
        if info.file_size != MODEL_SIZE:
            raise VadSecurityError("Silero VAD model member size mismatch")
        with archive.open(info) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)
    if sha256_file(destination) != MODEL_SHA256:
        raise VadSecurityError("Silero VAD model member hash mismatch")


def prepare_model(
    model_dir: Path,
    *,
    source_model: Path | None = None,
    force: bool = False,
) -> dict[str, object]:
    model_dir = model_dir.expanduser().absolute()
    verify_runtime_package()
    if model_is_prepared(model_dir) and not force:
        return {"status": "ready", "downloaded": False, "model_dir": str(model_dir)}
    if model_dir.exists() and not force:
        raise VadSecurityError(
            "Existing Silero VAD model directory failed validation; use --force to replace it"
        )
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{model_dir.name}.staging-", dir=model_dir.parent))
    try:
        destination = staging / MODEL_FILENAME
        if source_model is not None:
            verify_model_file(source_model)
            shutil.copyfile(source_model, destination)
        else:
            wheel = staging / "source.whl"
            download_wheel(wheel)
            extract_model_from_wheel(wheel, destination)
            wheel.unlink()
        destination.chmod(0o600)
        write_manifest(staging)
        verify_model_dir(staging)
        from scripts.speech_activity import SileroOnnxVad

        model = SileroOnnxVad(destination)
        model.predict(__import__("numpy").zeros(512, dtype="float32"))
        if model_dir.exists():
            backup = model_dir.with_name(f".{model_dir.name}.backup-{os.getpid()}")
            os.replace(model_dir, backup)
            try:
                os.replace(staging, model_dir)
            except Exception:
                os.replace(backup, model_dir)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(staging, model_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {"status": "ready", "downloaded": source_model is None, "model_dir": str(model_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare the pinned Silero VAD ONNX model without installing PyTorch audio packages."
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    if args.status:
        verify_runtime_package()
        verify_model_dir(settings.vad_model_dir)
        print(f"prepared=true\nmodel_dir={settings.vad_model_dir}\nruntime_network=disabled")
        return
    result = prepare_model(settings.vad_model_dir, source_model=args.source, force=args.force)
    print(f"prepared: {result['model_dir']}")


if __name__ == "__main__":
    main()
