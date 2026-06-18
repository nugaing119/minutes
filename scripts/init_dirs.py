from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import load_settings


def main() -> None:
    settings = load_settings()
    for path in (
        settings.recordings_inbox,
        settings.jobs_dir,
        settings.output_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
        print(f"created: {path}")


if __name__ == "__main__":
    main()
