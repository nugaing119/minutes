from __future__ import annotations

import fcntl
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


RESOURCE_POLICY_APPLIED_ENV = "MINUTES_RESOURCE_POLICY_APPLIED"


@contextmanager
def single_job_lock(lock_path: Path) -> Iterator[None]:
    """Serialize heavy meeting jobs across watchers and manual invocations."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.flush()
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def resource_policy_command(
    command: Sequence[str],
    *,
    qos: str,
    nice: int,
) -> list[str]:
    """Prefix a command with supported macOS scheduling controls."""
    result = list(command)
    normalized_qos = qos.strip().lower()
    taskpolicy = shutil.which("taskpolicy")
    if normalized_qos != "off" and taskpolicy is not None:
        result = [taskpolicy, "-c", normalized_qos, *result]

    nice_value = min(max(nice, 0), 20)
    nice_command = shutil.which("nice")
    if nice_value > 0 and nice_command is not None:
        if result and result[0] == taskpolicy:
            result = [*result[:3], nice_command, "-n", str(nice_value), *result[3:]]
        else:
            result = [nice_command, "-n", str(nice_value), *result]
    return result


def reexec_with_resource_policy(
    script_path: Path,
    script_args: Sequence[str],
    *,
    qos: str,
    nice: int,
) -> None:
    """Re-exec the CLI once so the process and descendants inherit its policy."""
    if os.environ.get(RESOURCE_POLICY_APPLIED_ENV) == "1":
        return

    base_command = [sys.executable, str(script_path.resolve()), *script_args]
    command = resource_policy_command(base_command, qos=qos, nice=nice)
    if command == base_command:
        return

    env = dict(os.environ)
    env[RESOURCE_POLICY_APPLIED_ENV] = "1"
    os.execvpe(command[0], command, env)
