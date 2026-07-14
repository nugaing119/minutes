from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path
from string import Template
from typing import Mapping, Sequence


DEFAULT_PATHS = {
    "MINUTES_HOME": "~/minutes",
    "RECORDINGS_INBOX": "~/remind",
}


def read_dotenv_value(path: Path, name: str) -> str | None:
    """Read one dotenv value without executing or exporting the file."""
    if not path.is_file():
        return None

    found: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator or key.strip() != name:
            continue
        lexer = shlex.shlex(raw_value, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
        if len(tokens) > 1:
            raise ValueError(f"{name} must be a single dotenv value")
        found = tokens[0] if tokens else ""
    return found


def resolve_configured_path(
    repo_root: Path,
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    env = dict(os.environ if environ is None else environ)
    raw_value = env.get(name) or read_dotenv_value(repo_root / ".env", name)
    raw_value = raw_value or DEFAULT_PATHS[name]

    home = Path(env.get("HOME") or Path.home()).expanduser()
    variables = {**env, "HOME": str(home)}
    expanded = Template(raw_value).safe_substitute(variables)
    if expanded == "~":
        path = home
    elif expanded.startswith("~/"):
        path = home / expanded[2:]
    else:
        path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve(strict=False)


def build_codex_command(
    codex_binary: str,
    repo_root: Path,
    writable_paths: Sequence[Path],
    passthrough_args: Sequence[str],
) -> list[str]:
    command = [codex_binary, "-C", str(repo_root)]
    seen: set[Path] = set()
    for path in writable_paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        command.extend(("--add-dir", str(resolved)))
    command.extend(passthrough_args)
    return command


def main(argv: Sequence[str] | None = None) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    child_env = dict(os.environ)
    minutes_home = resolve_configured_path(
        repo_root,
        "MINUTES_HOME",
        environ=child_env,
    )
    recordings_inbox = resolve_configured_path(
        repo_root,
        "RECORDINGS_INBOX",
        environ=child_env,
    )
    for path in (minutes_home, recordings_inbox):
        path.mkdir(parents=True, exist_ok=True)

    child_env["MINUTES_HOME"] = str(minutes_home)
    child_env["RECORDINGS_INBOX"] = str(recordings_inbox)
    codex_binary = shutil.which("codex", path=child_env.get("PATH"))
    if codex_binary is None:
        raise SystemExit("error: codex executable was not found on PATH")

    command = build_codex_command(
        codex_binary,
        repo_root,
        (minutes_home, recordings_inbox),
        tuple(sys.argv[1:] if argv is None else argv),
    )
    os.execve(codex_binary, command, child_env)


if __name__ == "__main__":
    main()
