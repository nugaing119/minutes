from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from scripts.config import load_settings
from scripts.media_types import SUPPORTED_EXTENSIONS


class RecordingHandler(FileSystemEventHandler):
    def __init__(self) -> None:
        self.settings = load_settings()
        self.processing: set[Path] = set()

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        self._maybe_process(event.src_path, event.is_directory)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        self._maybe_process(event.dest_path, event.is_directory)

    def _maybe_process(self, raw_path: str, is_directory: bool) -> None:
        if is_directory:
            return
        path = Path(raw_path).expanduser().resolve()
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        if path in self.processing:
            return

        self.processing.add(path)
        try:
            if wait_until_stable(
                path,
                self.settings.watch_poll_seconds,
                self.settings.watch_stable_seconds,
            ):
                subprocess.run(
                    [sys.executable, "-m", "scripts.process_file", str(path)],
                    check=True,
                )
        finally:
            self.processing.discard(path)


def wait_until_stable(path: Path, poll_seconds: int, stable_seconds: int) -> bool:
    stable_for = 0
    last_signature: tuple[int, int] | None = None
    while path.exists():
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if signature == last_signature:
            stable_for += poll_seconds
            if stable_for >= stable_seconds:
                return True
        else:
            stable_for = 0
            last_signature = signature
        time.sleep(poll_seconds)
    return False


def main() -> None:
    settings = load_settings()
    settings.recordings_inbox.mkdir(parents=True, exist_ok=True)
    handler = RecordingHandler()
    observer = Observer()
    observer.schedule(handler, str(settings.recordings_inbox), recursive=False)
    observer.start()
    print(f"watching: {settings.recordings_inbox}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
