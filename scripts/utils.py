from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")


def now_local() -> datetime:
    return datetime.now(SEOUL)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def make_job_id(path: Path) -> str:
    stat = path.stat()
    timestamp = datetime.fromtimestamp(stat.st_mtime, tz=SEOUL).strftime("%Y%m%d_%H%M%S")
    safe_stem = safe_filename(path.stem, max_length=40) or "meeting"
    return f"{timestamp}_{safe_stem}_{file_fingerprint(path)}"


def safe_filename(value: str, max_length: int = 80) -> str:
    normalized = re.sub(r"\s+", "-", value.strip())
    normalized = re.sub(r"[\\/:\*\?\"<>\|\x00-\x1f]", "", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip(".-_")
    return normalized[:max_length].strip(".-_")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def copy_to_unique(src: Path, dst: Path) -> Path:
    final_path = unique_path(dst)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, final_path)
    return final_path


def format_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"
