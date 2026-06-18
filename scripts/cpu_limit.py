from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from typing import Any, Sequence


def run_limited(
    command: Sequence[str],
    *,
    cpu_limit_percent: int = 0,
    period_seconds: float = 0.2,
    fallback_burst_cores: float = 2.5,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    env: dict[str, str] | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    limit = normalized_limit(cpu_limit_percent)
    if limit <= 0:
        return subprocess.run(
            list(command),
            check=check,
            capture_output=capture_output,
            text=text,
            env=env,
            **kwargs,
        )

    return run_with_duty_cycle(
        command,
        limit,
        period_seconds=period_seconds,
        fallback_burst_cores=fallback_burst_cores,
        check=check,
        capture_output=capture_output,
        text=text,
        env=env,
        **kwargs,
    )


def run_with_duty_cycle(
    command: Sequence[str],
    limit: int,
    *,
    period_seconds: float,
    fallback_burst_cores: float,
    check: bool,
    capture_output: bool,
    text: bool,
    env: dict[str, str] | None,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    period = max(period_seconds, 0.05)
    burst_cores = max(fallback_burst_cores, 1.0)
    # ps reports summed multi-core CPU. A single "thread-limited" ffmpeg can
    # still burst above 100%, so the fallback budgets for expected burst cores.
    duty = max(min((limit / 100.0) / burst_cores, 0.8), 0.03)
    active_seconds = max(period * duty, 0.01)
    pause_seconds = max(period - active_seconds, 0.0)

    stdout = subprocess.PIPE if capture_output else kwargs.pop("stdout", None)
    stderr = subprocess.PIPE if capture_output else kwargs.pop("stderr", None)
    process = subprocess.Popen(
        list(command),
        stdout=stdout,
        stderr=stderr,
        text=text,
        env=env,
        **kwargs,
    )
    try:
        while process.poll() is None:
            time.sleep(active_seconds)
            if process.poll() is not None or pause_seconds <= 0:
                break
            try:
                os.kill(process.pid, signal.SIGSTOP)
                time.sleep(pause_seconds)
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                break
        stdout_data, stderr_data = process.communicate()
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise

    completed = subprocess.CompletedProcess(
        list(command),
        process.returncode,
        stdout_data,
        stderr_data,
    )
    if check and completed.returncode:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def normalized_limit(value: int) -> int:
    if value <= 0:
        return 0
    return min(max(value, 1), 99)
