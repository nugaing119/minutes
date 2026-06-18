VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


def supported_extensions_text() -> str:
    return ", ".join(sorted(SUPPORTED_EXTENSIONS))


def is_video_extension(suffix: str) -> bool:
    return suffix.lower() in VIDEO_EXTENSIONS

