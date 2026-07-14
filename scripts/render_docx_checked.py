from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


SANDBOX_EXIT_CODE = 78


def is_macos_codex_sandbox(env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return sys.platform == "darwin" and values.get("CODEX_SANDBOX") == "seatbelt"


def find_documents_renderer(codex_home: Path | None = None) -> Path:
    home = codex_home or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    candidates = list(
        home.glob(
            "plugins/cache/openai-primary-runtime/documents/*/skills/documents/"
            "render_docx.py"
        )
    )
    if not candidates:
        raise FileNotFoundError(
            "Documents skill render_docx.py was not found under "
            f"{home / 'plugins/cache/openai-primary-runtime/documents'}"
        )
    return max(candidates, key=_renderer_version_key)


def _renderer_version_key(path: Path) -> tuple[int, ...]:
    version = path.parents[2].name
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


def build_renderer_command(renderer: Path, forwarded_args: Sequence[str]) -> list[str]:
    if importlib.util.find_spec("pdf2image") is not None:
        return [sys.executable, str(renderer), *forwarded_args]

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError(
            "pdf2image is unavailable and uv was not found. Install pdf2image in an "
            "isolated environment or install uv; do not add it to the minutes runtime "
            "dependencies solely for document QA."
        )
    return [
        uv,
        "run",
        "--no-project",
        "--with",
        "pdf2image",
        "python",
        str(renderer),
        *forwarded_args,
    ]


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bundled Documents renderer without triggering LibreOffice inside "
            "the macOS Codex seatbelt sandbox."
        )
    )
    parser.add_argument(
        "--renderer",
        type=Path,
        help="Override the bundled Documents render_docx.py path.",
    )
    known, forwarded = parser.parse_known_args(argv)
    if not forwarded:
        parser.error("pass the DOCX input path and render_docx.py arguments")

    if is_macos_codex_sandbox():
        print(
            "Refusing to launch LibreOffice inside the macOS Codex seatbelt sandbox; "
            "this path can abort soffice and show a crash dialog. Re-run this exact "
            "command once with sandbox_permissions=require_escalated.",
            file=sys.stderr,
        )
        return SANDBOX_EXIT_CODE

    renderer = (known.renderer or find_documents_renderer()).expanduser().resolve()
    if not renderer.is_file():
        print(f"renderer not found: {renderer}", file=sys.stderr)
        return 2

    for executable in ("soffice", "pdftoppm"):
        if shutil.which(executable) is None:
            print(f"required executable not found: {executable}", file=sys.stderr)
            return 2

    try:
        command = build_renderer_command(renderer, forwarded)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    env = os.environ.copy()
    if sys.platform == "darwin" and Path("/private/tmp").is_dir():
        env["TMPDIR"] = "/private/tmp"
        env["TEMP"] = "/private/tmp"
        env["TMP"] = "/private/tmp"
        env.setdefault("UV_CACHE_DIR", "/private/tmp/minutes-uv-cache")
    return subprocess.run(command, check=False, env=env).returncode


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
