from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


load_dotenv()

try:
    from scripts.keychain import get_secret
except Exception:
    def get_secret(account: str) -> str | None:
        return None

from scripts.vad_security import expected_model_dir


def _path_from_env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _nonnegative_float_from_env(name: str, default: float) -> float:
    value = _float_from_env(name, default)
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")
    return value


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _bounded_int_from_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = _int_from_env(name, default)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _choice_from_env(name: str, default: str, choices: set[str]) -> str:
    value = os.environ.get(name, default).strip().lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"Unsupported {name}: {value}. Allowed: {allowed}")
    return value


@dataclass(frozen=True)
class Settings:
    minutes_home: Path
    recordings_inbox: Path
    jobs_dir: Path
    output_dir: Path
    whisper_model: str
    whisper_device: str
    language: str
    output_language: str
    content_audit_mode: str
    official_source_verification: str
    speaker_attribution_mode: str
    speaker_attribution_required: bool
    speech_activity_validation_enabled: bool
    vad_model_dir: Path
    community1_approval_path: Path
    community1_model_dir: Path
    process_qos: str
    process_nice: int
    docx_enabled: bool
    audio_sample_rate: int
    audio_ffmpeg_threads: int
    audio_cpu_limit_percent: int
    audio_cpu_limit_period_seconds: float
    audio_cpu_limit_fallback_burst_cores: float
    ocr_enabled: bool
    ocr_frame_interval_seconds: int
    ocr_languages: str
    ocr_max_context_chars: int
    ocr_ffmpeg_threads: int
    ocr_workers: int
    ocr_tesseract_thread_limit: int
    ocr_tesseract_nice: int
    ocr_prestart_cooldown_seconds: float
    ocr_frame_pause_seconds: float
    ocr_visual_dedupe_enabled: bool
    ocr_visual_dedupe_ignore_bottom_ratio: float
    ocr_visual_dedupe_ignore_right_ratio: float
    ocr_visual_dedupe_max_mean_delta: float
    ocr_max_snapshot_gap_seconds: int
    ocr_visual_only_min_mean_delta: float
    ocr_frame_extract_cpu_limit_percent: int
    ocr_frame_extract_cpu_limit_period_seconds: float
    ocr_frame_extract_cpu_limit_fallback_burst_cores: float
    ocr_signature_cpu_limit_percent: int
    ocr_signature_cpu_limit_period_seconds: float
    ocr_signature_cpu_limit_fallback_burst_cores: float
    ocr_tesseract_cpu_limit_percent: int
    ocr_tesseract_cpu_limit_period_seconds: float
    ocr_tesseract_cpu_limit_fallback_burst_cores: float
    cleanup_job_ocr_images_after_archive: bool
    cleanup_job_media_after_archive: bool
    completed_job_retention_hours: int
    watch_stable_seconds: int
    watch_poll_seconds: int
    llm_provider: str
    openai_api_key: str | None
    openai_model: str | None
    oci_genai_model: str | None
    oci_genai_compartment_id: str | None
    oci_genai_endpoint: str | None
    oci_config_file: Path
    oci_profile: str


def load_settings() -> Settings:
    minutes_home = _path_from_env("MINUTES_HOME", "~/minutes")
    recordings_inbox = _path_from_env("RECORDINGS_INBOX", "~/remind")
    llm_provider = _choice_from_env(
        "LLM_PROVIDER",
        "openai",
        {"openai", "oci", "codex"},
    )
    content_audit_mode = _choice_from_env(
        "CONTENT_AUDIT_MODE",
        "off",
        {"off", "warn", "strict"},
    )
    official_source_verification = _choice_from_env(
        "OFFICIAL_SOURCE_VERIFICATION",
        "off",
        {"off", "auto", "required"},
    )
    if content_audit_mode != "off" and llm_provider != "codex":
        raise ValueError(
            "CONTENT_AUDIT_MODE requires LLM_PROVIDER=codex until provider-side "
            "audit artifacts are supported"
        )
    if official_source_verification != "off" and content_audit_mode == "off":
        raise ValueError(
            "OFFICIAL_SOURCE_VERIFICATION requires CONTENT_AUDIT_MODE=warn or strict"
        )
    if official_source_verification != "off" and llm_provider != "codex":
        raise ValueError(
            "OFFICIAL_SOURCE_VERIFICATION requires LLM_PROVIDER=codex"
        )
    speaker_attribution_mode = _choice_from_env(
        "SPEAKER_ATTRIBUTION_MODE",
        "evidence",
        {"off", "evidence"},
    )
    speaker_attribution_required = _bool_from_env(
        "SPEAKER_ATTRIBUTION_REQUIRED",
        False,
    )
    if speaker_attribution_required:
        raise ValueError(
            "SPEAKER_ATTRIBUTION_REQUIRED=true is incompatible with the "
            "evidence-only speaker policy; uncertain speakers must remain unknown"
        )
    return Settings(
        minutes_home=minutes_home,
        recordings_inbox=recordings_inbox,
        jobs_dir=minutes_home / "jobs",
        output_dir=minutes_home / "output",
        whisper_model=os.environ.get(
            "WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo"
        ),
        whisper_device=os.environ.get("WHISPER_DEVICE", "gpu").lower(),
        language=os.environ.get("LANGUAGE", "auto"),
        output_language=_choice_from_env(
            "OUTPUT_LANGUAGE",
            "auto",
            {"auto", "en", "ko"},
        ),
        content_audit_mode=content_audit_mode,
        official_source_verification=official_source_verification,
        speaker_attribution_mode=speaker_attribution_mode,
        speaker_attribution_required=speaker_attribution_required,
        speech_activity_validation_enabled=_bool_from_env(
            "SPEECH_ACTIVITY_VALIDATION_ENABLED",
            True,
        ),
        vad_model_dir=expected_model_dir(minutes_home),
        community1_approval_path=_path_from_env(
            "COMMUNITY1_APPROVAL_PATH",
            str(minutes_home / "governance" / "pyannote-community1-approval.json"),
        ),
        community1_model_dir=_path_from_env(
            "COMMUNITY1_MODEL_DIR",
            str(minutes_home / "models" / "pyannote-community1"),
        ),
        process_qos=_choice_from_env(
            "PROCESS_QOS",
            "utility",
            {"off", "utility", "background", "maintenance"},
        ),
        process_nice=_bounded_int_from_env("PROCESS_NICE", 10, 0, 20),
        docx_enabled=_bool_from_env("DOCX_ENABLED", True),
        audio_sample_rate=_int_from_env("AUDIO_SAMPLE_RATE", 16000),
        audio_ffmpeg_threads=_int_from_env("AUDIO_FFMPEG_THREADS", 1),
        audio_cpu_limit_percent=_int_from_env(
            "AUDIO_CPU_LIMIT_PERCENT",
            _int_from_env("CPU_LIMIT_PERCENT", 60),
        ),
        audio_cpu_limit_period_seconds=_float_from_env(
            "AUDIO_CPU_LIMIT_PERIOD_SECONDS",
            _float_from_env("CPU_LIMIT_PERIOD_SECONDS", 0.2),
        ),
        audio_cpu_limit_fallback_burst_cores=_float_from_env(
            "AUDIO_CPU_LIMIT_FALLBACK_BURST_CORES",
            _float_from_env("CPU_LIMIT_FALLBACK_BURST_CORES", 2.5),
        ),
        ocr_enabled=_bool_from_env("OCR_ENABLED", True),
        ocr_frame_interval_seconds=_int_from_env("OCR_FRAME_INTERVAL_SECONDS", 5),
        ocr_languages=os.environ.get("OCR_LANGUAGES", "auto").strip().lower(),
        ocr_max_context_chars=_int_from_env("OCR_MAX_CONTEXT_CHARS", 12_000),
        ocr_ffmpeg_threads=_bounded_int_from_env("OCR_FFMPEG_THREADS", 2, 1, 16),
        ocr_workers=_bounded_int_from_env("OCR_WORKERS", 3, 1, 16),
        ocr_tesseract_thread_limit=_int_from_env("OCR_TESSERACT_THREAD_LIMIT", 1),
        ocr_tesseract_nice=_int_from_env("OCR_TESSERACT_NICE", 0),
        ocr_prestart_cooldown_seconds=_nonnegative_float_from_env(
            "OCR_PRESTART_COOLDOWN_SECONDS",
            20.0,
        ),
        ocr_frame_pause_seconds=_float_from_env("OCR_FRAME_PAUSE_SECONDS", 0.0),
        ocr_visual_dedupe_enabled=_bool_from_env("OCR_VISUAL_DEDUPE_ENABLED", True),
        ocr_visual_dedupe_ignore_bottom_ratio=_float_from_env(
            "OCR_VISUAL_DEDUPE_IGNORE_BOTTOM_RATIO",
            0.18,
        ),
        ocr_visual_dedupe_ignore_right_ratio=_float_from_env(
            "OCR_VISUAL_DEDUPE_IGNORE_RIGHT_RATIO",
            0.20,
        ),
        ocr_visual_dedupe_max_mean_delta=_float_from_env(
            "OCR_VISUAL_DEDUPE_MAX_MEAN_DELTA",
            6.0,
        ),
        ocr_max_snapshot_gap_seconds=_bounded_int_from_env(
            "OCR_MAX_SNAPSHOT_GAP_SECONDS",
            120,
            10,
            3600,
        ),
        ocr_visual_only_min_mean_delta=_float_from_env(
            "OCR_VISUAL_ONLY_MIN_MEAN_DELTA",
            12.0,
        ),
        ocr_frame_extract_cpu_limit_percent=_int_from_env(
            "OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT",
            _int_from_env("OCR_CPU_LIMIT_PERCENT", 0),
        ),
        ocr_frame_extract_cpu_limit_period_seconds=_float_from_env(
            "OCR_FRAME_EXTRACT_CPU_LIMIT_PERIOD_SECONDS",
            _float_from_env("OCR_CPU_LIMIT_PERIOD_SECONDS", 0.2),
        ),
        ocr_frame_extract_cpu_limit_fallback_burst_cores=_float_from_env(
            "OCR_FRAME_EXTRACT_CPU_LIMIT_FALLBACK_BURST_CORES",
            _float_from_env("OCR_CPU_LIMIT_FALLBACK_BURST_CORES", 1.5),
        ),
        ocr_signature_cpu_limit_percent=_int_from_env(
            "OCR_SIGNATURE_CPU_LIMIT_PERCENT",
            0,
        ),
        ocr_signature_cpu_limit_period_seconds=_float_from_env(
            "OCR_SIGNATURE_CPU_LIMIT_PERIOD_SECONDS",
            0.2,
        ),
        ocr_signature_cpu_limit_fallback_burst_cores=_float_from_env(
            "OCR_SIGNATURE_CPU_LIMIT_FALLBACK_BURST_CORES",
            2.5,
        ),
        ocr_tesseract_cpu_limit_percent=_int_from_env(
            "OCR_TESSERACT_CPU_LIMIT_PERCENT",
            0,
        ),
        ocr_tesseract_cpu_limit_period_seconds=_float_from_env(
            "OCR_TESSERACT_CPU_LIMIT_PERIOD_SECONDS",
            0.2,
        ),
        ocr_tesseract_cpu_limit_fallback_burst_cores=_float_from_env(
            "OCR_TESSERACT_CPU_LIMIT_FALLBACK_BURST_CORES",
            2.5,
        ),
        cleanup_job_ocr_images_after_archive=_bool_from_env(
            "CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE",
            False,
        ),
        cleanup_job_media_after_archive=_bool_from_env(
            "CLEANUP_JOB_MEDIA_AFTER_ARCHIVE",
            True,
        ),
        completed_job_retention_hours=_bounded_int_from_env(
            "COMPLETED_JOB_RETENTION_HOURS",
            0,
            0,
            8760,
        ),
        watch_stable_seconds=_int_from_env("WATCH_STABLE_SECONDS", 15),
        watch_poll_seconds=_int_from_env("WATCH_POLL_SECONDS", 5),
        llm_provider=llm_provider,
        openai_api_key=os.environ.get("OPENAI_API_KEY") or get_secret("OPENAI_API_KEY"),
        openai_model=os.environ.get("OPENAI_MODEL") or get_secret("OPENAI_MODEL"),
        oci_genai_model=os.environ.get("OCI_GENAI_MODEL"),
        oci_genai_compartment_id=os.environ.get("OCI_GENAI_COMPARTMENT_ID"),
        oci_genai_endpoint=os.environ.get("OCI_GENAI_ENDPOINT"),
        oci_config_file=_path_from_env("OCI_CONFIG_FILE", "~/.oci/config"),
        oci_profile=os.environ.get("OCI_PROFILE", "DEFAULT"),
    )
