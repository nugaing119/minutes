from __future__ import annotations

import subprocess


SERVICE = "meeting-minutes"


def get_secret(account: str) -> str | None:
    completed = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            SERVICE,
            "-a",
            account,
            "-w",
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def set_secret(account: str, value: str) -> None:
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            SERVICE,
            "-a",
            account,
            "-w",
            value,
        ],
        check=True,
    )
