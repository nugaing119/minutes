from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.config import Settings, load_settings
from scripts.content_audit import content_fidelity_instruction
from scripts.cleanup_completed_jobs import cleanup_completed_jobs
from scripts.media_types import (
    SUPPORTED_EXTENSIONS,
    is_video_extension,
    supported_extensions_text,
)
from scripts.resource_control import reexec_with_resource_policy, single_job_lock
from scripts.summarize import generate_minutes, render_markdown
from scripts.utils import (
    file_fingerprint,
    format_timestamp,
    make_job_id,
    now_local,
    read_json,
    write_json,
)


SPEAKER_ATTRIBUTION_PROFILE_VERSION = "7"


def process_file(media_path: Path) -> Path:
    settings = load_settings()
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    with single_job_lock(settings.jobs_dir / ".process.lock"):
        try:
            cleanup = cleanup_completed_jobs(
                settings.jobs_dir,
                apply=True,
                retention_hours=settings.completed_job_retention_hours,
            )
            if cleanup["purged_jobs"]:
                print(
                    "expired jobs purged: "
                    f"{cleanup['purged_jobs']} "
                    f"({cleanup['reclaimed_bytes']} bytes)"
                )
        except OSError as exc:
            print(f"warning: expired job cleanup failed: {exc}")
        return _process_file(media_path, settings)


def _process_file(media_path: Path, settings: Settings) -> Path:
    speaker_mode = getattr(settings, "speaker_attribution_mode", "off")
    speaker_required = getattr(settings, "speaker_attribution_required", False)
    if speaker_mode not in {"off", "evidence"}:
        raise ValueError(
            "Automatic local audio diarization is disabled by policy; "
            "SPEAKER_ATTRIBUTION_MODE must be off or evidence"
        )
    if speaker_required:
        raise ValueError(
            "SPEAKER_ATTRIBUTION_REQUIRED=true is incompatible with the "
            "evidence-only speaker policy; uncertain speakers must remain unknown"
        )
    speaker_profile = _speaker_profile(settings)
    source = media_path.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension: {source.suffix}. "
            f"Supported: {supported_extensions_text()}",
        )
    has_video = is_video_extension(source.suffix)

    settings.recordings_inbox.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = file_fingerprint(source)
    index_path = settings.jobs_dir / "index.json"
    index = read_json(index_path, {"processed_files": []})
    for item in index.get("processed_files", []):
        if (
            item.get("fingerprint") == fingerprint
            and item.get("speaker_attribution_profile") == speaker_profile
        ):
            output_dir = Path(item["output_dir"]).expanduser()
            print(f"already processed: {output_dir}")
            return output_dir

    job_id = make_job_id(source)
    job_dir = settings.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_source = job_dir / f"source{source.suffix.lower()}"
    move_from_inbox = source.parent == settings.recordings_inbox.expanduser().resolve()
    write_json(
        job_dir / "source_metadata.json",
        {
            "original_path": str(source),
            "original_name": source.name,
            "source_size": source.stat().st_size,
            "source_mtime_ns": source.stat().st_mtime_ns,
            "fingerprint": fingerprint,
            "move_from_inbox": move_from_inbox,
            "speaker_attribution_mode": speaker_mode,
            "speaker_attribution_profile": speaker_profile,
        },
    )
    status_path = job_dir / "status.json"
    logs_path = job_dir / "logs.txt"
    current_step = "starting"
    resource_policy = {
        "qos": getattr(settings, "process_qos", "utility"),
        "nice": getattr(settings, "process_nice", 10),
        "single_job": True,
        "ocr_workers": getattr(settings, "ocr_workers", 3),
        "ocr_ffmpeg_threads": getattr(settings, "ocr_ffmpeg_threads", 2),
        "ocr_prestart_cooldown_seconds": getattr(
            settings,
            "ocr_prestart_cooldown_seconds",
            0.0,
        ),
        "ocr_tesseract_thread_limit": getattr(
            settings,
            "ocr_tesseract_thread_limit",
            1,
        ),
        "ocr_frame_extract_cpu_limit_percent": getattr(
            settings,
            "ocr_frame_extract_cpu_limit_percent",
            0,
        ),
        "ocr_tesseract_nice_increment": getattr(
            settings,
            "ocr_tesseract_nice",
            0,
        ),
        "speaker_evidence_policy": "stt_ocr_selected_snapshots",
        "local_audio_diarization": "disabled_by_policy",
        "speech_activity_validation": (
            "silero_onnx_cpu_single_thread"
            if getattr(settings, "speech_activity_validation_enabled", True)
            else "disabled"
        ),
    }
    metrics_path = job_dir / "process_metrics.json"
    metrics_started_at = now_local()
    metrics_started_clock = time.perf_counter()
    metrics_stage: str | None = None
    metrics_stage_clock = metrics_started_clock
    metrics_stage_cpu = _cpu_seconds()
    metrics_stages: list[dict[str, object]] = []
    intermediate_cleanup: dict[str, object] = {}
    ocr_metrics: dict[str, object] = {}
    peak_observed_job_bytes = 0

    def finish_metrics_stage() -> None:
        nonlocal metrics_stage, metrics_stage_clock, metrics_stage_cpu
        nonlocal peak_observed_job_bytes
        if metrics_stage is None:
            return
        finished_clock = time.perf_counter()
        finished_cpu = _cpu_seconds()
        wall_seconds = max(finished_clock - metrics_stage_clock, 0.0)
        cpu_seconds = max(finished_cpu - metrics_stage_cpu, 0.0)
        artifact_sizes = _job_artifact_sizes(job_dir)
        peak_observed_job_bytes = max(
            peak_observed_job_bytes,
            int(artifact_sizes["job_bytes"]),
        )
        metrics_stages.append(
            {
                "step": metrics_stage,
                "wall_seconds": round(wall_seconds, 3),
                "cpu_seconds": round(cpu_seconds, 3),
                "cpu_percent_of_one_core": (
                    round(cpu_seconds / wall_seconds * 100.0, 1)
                    if wall_seconds > 0
                    else 0.0
                ),
                **artifact_sizes,
            }
        )
        metrics_stage = None

    def start_metrics_stage(step: str) -> None:
        nonlocal metrics_stage, metrics_stage_clock, metrics_stage_cpu
        metrics_stage = step
        metrics_stage_clock = time.perf_counter()
        metrics_stage_cpu = _cpu_seconds()

    def write_process_metrics(state: str) -> None:
        write_json(
            metrics_path,
            {
                "schema_version": 2,
                "job_id": job_id,
                "source": str(source),
                "state": state,
                "started_at": metrics_started_at.isoformat(),
                "updated_at": now_local().isoformat(),
                "elapsed_seconds": round(
                    time.perf_counter() - metrics_started_clock,
                    3,
                ),
                "resource_policy": resource_policy,
                "stages": metrics_stages,
                "ocr": ocr_metrics,
                "intermediate_cleanup": intermediate_cleanup,
                "peak_observed_job_bytes": peak_observed_job_bytes,
                "cpu_metric_note": (
                    "CPU includes this process and completed child processes; "
                    "MLX GPU/Metal time is not represented as CPU time."
                ),
            },
        )

    def record_intermediate_cleanup(name: str, result: dict[str, object]) -> None:
        intermediate_cleanup[name] = result
        write_process_metrics("running")

    def status(step: str, state: str = "running", **extra: object) -> None:
        nonlocal current_step
        metrics_changed = False
        if metrics_stage is not None and (step != metrics_stage or state != "running"):
            finish_metrics_stage()
            metrics_changed = True
        if state == "running" and metrics_stage is None:
            start_metrics_stage(step)
            metrics_changed = True
        if state == "running":
            current_step = step
        if metrics_changed or state != "running":
            write_process_metrics(state)
        payload = {
            "status": state,
            "step": step,
            "source": str(source),
            "job_id": job_id,
            "updated_at": now_local().isoformat(),
            "resource_policy": resource_policy,
            "process_metrics": {
                "path": str(metrics_path),
                "completed_stage_count": len(metrics_stages),
            },
            **extra,
        }
        write_json(status_path, payload)

    try:
        status("prepare_source", move_from_inbox=move_from_inbox)
        if move_from_inbox:
            working_source = source
        else:
            working_source = job_source
        if not move_from_inbox and not job_source.exists():
            shutil.copy2(source, job_source)

        from scripts.transcribe import extract_audio, load_pcm_wav, transcribe_audio

        status("extract_audio")
        audio_path = job_dir / "audio.wav"
        extract_audio(working_source, audio_path, settings)

        status("load_audio")
        audio_waveform, _audio_sample_rate = load_pcm_wav(
            audio_path,
            expected_sample_rate=settings.audio_sample_rate,
        )

        status("transcribe")
        transcript_path = job_dir / "transcript.json"
        transcript_result = transcribe_audio(
            audio_path,
            transcript_path,
            settings,
            waveform=audio_waveform,
        )
        status("validate_speech_activity")
        from scripts.speech_activity import validate_transcript_speech

        speech_validation = validate_transcript_speech(
            audio_waveform,
            _audio_sample_rate,
            transcript_result,
            settings,
        )
        write_json(job_dir / "speech_activity.json", speech_validation)
        transcript_text_path = job_dir / "transcript.txt"
        minutes_transcript_path = transcript_text_path
        speaker_summary: dict[str, object] = {
            "requested_mode": speaker_mode,
            "effective_mode": speaker_mode,
            "status": "collecting_evidence" if speaker_mode == "evidence" else "disabled",
            "local_audio_diarization": "disabled_by_policy",
            "audio_separation_available": False,
            "audio_separation_usable": False,
            "speech_activity_validation": speech_validation.get("status"),
        }

        if speaker_mode == "evidence":
            minutes_transcript_path = job_dir / "transcript.evidence.txt"
            minutes_transcript_path.write_text(
                _timestamped_transcript_text(transcript_result),
                encoding="utf-8",
            )

        del audio_waveform
        transcript_text = minutes_transcript_path.read_text(encoding="utf-8")
        if getattr(settings, "cleanup_job_media_after_archive", True):
            record_intermediate_cleanup(
                "audio_wav",
                _remove_intermediate_file(audio_path),
            )

        ocr_prestart_cooldown_seconds = float(
            getattr(settings, "ocr_prestart_cooldown_seconds", 0.0)
        )
        if has_video and ocr_prestart_cooldown_seconds > 0:
            status("pre_ocr_cooldown")
            time.sleep(ocr_prestart_cooldown_seconds)

        status("ocr")
        screen_text = ""
        screen_text_json_path = job_dir / "screen_text.json"
        screen_text_txt_path = job_dir / "screen_text.txt"
        if has_video:
            try:
                from scripts.ocr import run_ocr

                ocr_result = run_ocr(
                    working_source,
                    job_dir,
                    settings,
                    detected_language=str(transcript_result.get("language", "")),
                )
                runtime_metrics = ocr_result.get("metrics", {})
                if isinstance(runtime_metrics, dict):
                    ocr_metrics.update(runtime_metrics)
                record_intermediate_cleanup(
                    "ocr_raw_frames",
                    {
                        "cleaned": bool(
                            ocr_result.get("raw_frames_cleaned", False)
                        ),
                        "removed_files": int(
                            ocr_result.get("raw_frame_count", 0)
                        ),
                        "reclaimed_bytes": int(
                            ocr_result.get("raw_frames_reclaimed_bytes", 0)
                        ),
                    },
                )
                if screen_text_txt_path.exists():
                    screen_text = screen_text_txt_path.read_text(encoding="utf-8")
            except Exception as ocr_error:
                write_json(
                    screen_text_json_path,
                    {
                        "enabled": settings.ocr_enabled,
                        "status": "failed",
                        "error": str(ocr_error),
                        "frames": [],
                    },
                )
                write_json(
                    job_dir / "evidence_coverage.json",
                    {
                        "schema_version": 1,
                        "status": "failed",
                        "coverage_passed": False,
                        "error": str(ocr_error)[:500],
                        "raw_frame_count": 0,
                        "selected_snapshot_count": 0,
                        "accounted_frame_count": 0,
                        "accounting_complete": False,
                        "reason_counts": {},
                        "frames": [],
                    },
                )
                screen_text_txt_path.write_text("", encoding="utf-8")
                screen_text = ""
        else:
            write_json(
                screen_text_json_path,
                {
                    "enabled": settings.ocr_enabled,
                    "status": "skipped",
                    "reason": "audio input has no video frames",
                    "frames": [],
                },
            )
            screen_text_txt_path.write_text("", encoding="utf-8")

        selected_snapshot_count = (
            sum(1 for _ in (job_dir / "snapshots").glob("*.jpg"))
            if (job_dir / "snapshots").is_dir()
            else 0
        )
        snapshot_evidence_available = selected_snapshot_count > 0
        if speaker_mode == "evidence":
            screen_available = bool(screen_text.strip()) or snapshot_evidence_available
            if snapshot_evidence_available:
                identity_resolution_method = "llm_timestamped_stt_ocr_selected_snapshots"
            elif screen_text.strip():
                identity_resolution_method = "llm_timestamped_stt_ocr"
            else:
                identity_resolution_method = "llm_explicit_stt_only"
            speaker_summary.update(
                {
                    "effective_mode": "evidence",
                    "status": "evidence_prepared",
                    "identity_resolution_method": identity_resolution_method,
                    "screen_evidence_available": screen_available,
                    "snapshot_evidence_available": snapshot_evidence_available,
                    "speaker_resolution_rule": (
                        "use only corroborated timestamped STT/OCR/selected snapshots; "
                        "leave uncertain speakers unknown"
                    ),
                }
            )
            evidence_sources = ["timestamped_stt"]
            if screen_text.strip():
                evidence_sources.append("timestamped_ocr")
            if snapshot_evidence_available:
                evidence_sources.append("selected_snapshots")
            report_path = job_dir / "speaker_attribution_report.json"
            write_json(
                report_path,
                {
                    "schema_version": 1,
                    **speaker_summary,
                    "evidence_sources": evidence_sources,
                },
            )

        if settings.llm_provider == "codex":
            codex_input_path = job_dir / "codex_minutes_input.md"
            codex_input_path.write_text(
                build_codex_minutes_input(
                    transcript_text,
                    screen_text,
                    output_language=getattr(settings, "output_language", "auto"),
                    detected_language=str(transcript_result.get("language", "")),
                    speaker_attribution_mode=speaker_mode,
                    content_audit_mode=getattr(
                        settings,
                        "content_audit_mode",
                        "off",
                    ),
                    official_source_verification=getattr(
                        settings,
                        "official_source_verification",
                        "off",
                    ),
                    snapshot_evidence_available=snapshot_evidence_available,
                    speech_validation=speech_validation,
                ),
                encoding="utf-8",
            )
            if move_from_inbox:
                from scripts.archive_job import move_required

                status("stage_source", move_from_inbox=True)
                move_required(source, job_source)
            status(
                "awaiting_codex",
                state="awaiting_codex",
                speaker_attribution=speaker_summary,
                managed_source=str(job_source),
                codex_handoff={
                    "fresh_context_required": True,
                    "input_path": str(codex_input_path),
                    "snapshots_path": str(job_dir / "snapshots"),
                    "output_language": getattr(
                        settings,
                        "output_language",
                        "auto",
                    ),
                    "detected_language": str(
                        transcript_result.get("language", "") or "unknown"
                    ),
                    "docx_enabled": bool(getattr(settings, "docx_enabled", True)),
                    "selected_snapshot_count": selected_snapshot_count,
                },
                content_audit={
                    "mode": getattr(settings, "content_audit_mode", "off"),
                    "official_source_verification": getattr(
                        settings,
                        "official_source_verification",
                        "off",
                    ),
                },
            )
            print(f"prepared for Codex: {codex_input_path}")
            return job_dir

        status("summarize")
        minutes = generate_minutes(transcript_text, settings, screen_text=screen_text)
        raw_minutes_path = job_dir / "minutes.raw.json"
        write_json(raw_minutes_path, minutes)

        status("render_markdown")
        markdown = render_markdown(minutes, settings.output_language)
        minutes_path = job_dir / "minutes.md"
        minutes_path.write_text(markdown, encoding="utf-8")

        from scripts.archive_job import archive_job, move_required

        if move_from_inbox:
            status("stage_source", move_from_inbox=True)
            move_required(source, job_source)
        status("archive", speaker_attribution=speaker_summary)
        document_title = str(
            minutes.get("document_title") or minutes.get("meeting_title") or ""
        )
        output_dir = archive_job(
            job_dir,
            title=document_title or None,
            settings=settings,
        )
        finish_metrics_stage()
        write_process_metrics("completed")
        return output_dir
    except Exception as exc:
        logs_path.write_text(traceback.format_exc(), encoding="utf-8")
        status(
            current_step,
            state="failed",
            error=str(exc),
            retryable=True,
            logs=str(logs_path),
        )
        raise


def _cpu_seconds() -> float:
    usage = os.times()
    return usage.user + usage.system + usage.children_user + usage.children_system


def _path_size(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _job_artifact_sizes(job_dir: Path) -> dict[str, int]:
    return {
        "job_bytes": _path_size(job_dir),
        "source_media_bytes": sum(
            _path_size(path) for path in job_dir.glob("source.*")
        ),
        "audio_wav_bytes": _path_size(job_dir / "audio.wav"),
        "raw_frames_bytes": _path_size(job_dir / "frames"),
        "snapshots_bytes": _path_size(job_dir / "snapshots"),
    }


def _remove_intermediate_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "cleaned": False,
            "removed_files": [],
            "reclaimed_bytes": 0,
        }
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Refusing unexpected intermediate path: {path}")
    reclaimed_bytes = path.stat().st_size
    path.unlink()
    return {
        "cleaned": True,
        "removed_files": [path.name],
        "reclaimed_bytes": reclaimed_bytes,
    }


def _speaker_profile(settings: Settings) -> str:
    mode = getattr(settings, "speaker_attribution_mode", "off")
    if mode == "off":
        return "off"
    return f"{mode}:{SPEAKER_ATTRIBUTION_PROFILE_VERSION}"


def _timestamped_transcript_text(transcript_result: dict[str, object]) -> str:
    lines: list[str] = []
    for segment in transcript_result.get("segments", []):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = format_timestamp(float(segment.get("start", 0.0))).replace(",", ".")
        end = format_timestamp(float(segment.get("end", 0.0))).replace(",", ".")
        lines.append(f"[{start} - {end}] {text}")
    if not lines:
        fallback = str(transcript_result.get("text", "")).strip()
        return fallback + ("\n" if fallback else "")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Process one video or audio file.")
    parser.add_argument(
        "media_path",
        help=f"Path to a supported media file: {supported_extensions_text()}",
    )
    args = parser.parse_args()
    settings = load_settings()
    reexec_with_resource_policy(
        Path(__file__),
        [args.media_path],
        qos=settings.process_qos,
        nice=settings.process_nice,
    )
    try:
        process_file(Path(args.media_path))
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc


def build_codex_minutes_input(
    transcript_text: str,
    screen_text: str,
    *,
    output_language: str = "auto",
    detected_language: str = "",
    speaker_attribution_mode: str = "off",
    content_audit_mode: str = "off",
    official_source_verification: str = "off",
    snapshot_evidence_available: bool = False,
    speech_validation: dict[str, object] | None = None,
) -> str:
    from scripts.llm import language_instruction, speaker_identity_instruction
    from scripts.speech_activity import render_validation_evidence

    if output_language == "en":
        output_sections = (
            "# <A concise title derived from the actual content>\n\n"
            "Document type: <lecture, technical briefing, interview, discussion, "
            "demo, training, or another evidence-based type>\n\n"
            "## <Content-specific section heading>"
        )
    elif output_language == "ko":
        output_sections = (
            "# <내용에 맞는 문서 제목>\n\n"
            "문서 유형: <강의, 기술 발표, 웨비나, 인터뷰, 토론, 업무 협의, "
            "데모, 교육 자료 등 근거에 맞는 유형>\n\n"
            "## <내용에 맞는 섹션 제목>"
        )
    else:
        output_sections = (
            "원문의 지배적인 언어로 내용에 맞는 문서 제목, 문서 유형, "
            "필요한 수만큼의 고유한 섹션 제목을 작성하세요."
        )
    fidelity_instruction = content_fidelity_instruction(
        content_audit_mode,
        official_source_verification,
    )
    speaker_instruction = speaker_identity_instruction(
        transcript_text,
        screen_text,
        speaker_attribution_mode,
        snapshot_evidence_available=snapshot_evidence_available,
    )
    parts = [
        "# Codex 영상 분석 문서 작성 입력",
        "",
        "아래 원문 자료에서 영상 내용에 맞는 최종 Markdown 문서를 직접 작성하세요.",
        language_instruction(output_language),
        "원문을 별도 완성 문서로 먼저 번역한 뒤 다시 요약하지 마세요.",
        f"STT 감지 언어: {detected_language or 'unknown'}",
        "전사 오류는 문맥상 자연스럽게 보정하되 없는 내용을 만들지 마세요.",
        "파일명이나 플랫폼이 아니라 영상의 실제 성격을 분석해 문서 유형을 정하세요.",
        "'회의록'이나 '영상 요약'을 고정 제목으로 사용하지 마세요.",
        "회의 요약·결정사항·액션 아이템 같은 고정 항목을 강요하지 말고 실제 내용에 맞는 만큼 섹션을 구성하세요.",
        "결정·후속 조치·추가 확인 항목은 근거가 있고 해당 콘텐츠에 필요할 때만 넣으세요.",
        "",
        fidelity_instruction,
        "",
        "## Speaker identity evidence policy / 화자 식별 근거 정책",
        "",
        speaker_instruction,
        "",
        "## 출력 계약",
        "",
        output_sections,
        "",
        "## Audio transcript / 음성 전사",
        "",
        transcript_text.strip(),
    ]
    if screen_text.strip():
        parts.extend(
            [
                "",
                "## Screen OCR evidence / 화면 OCR 근거",
                "",
                "OCR은 음성 전사를 보완하는 근거이며 오인식을 단정하지 마세요.",
                "",
                screen_text.strip(),
            ]
        )
    validation_evidence = render_validation_evidence(speech_validation or {})
    if validation_evidence:
        parts.extend(
            [
                "",
                "## Speech activity validation / 발화 존재 검증",
                "",
                validation_evidence,
            ]
        )
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    main()
