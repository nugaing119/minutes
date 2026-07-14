# minutes 현재 아키텍처와 운영 계획

기준일: 2026-07-14

이 문서는 초기 MVP 구상이 아니라 현재 구현된 동작과 앞으로 유지할 운영 계약을 설명한다.
사용 방법은 [README.md](README.md)와 [INSTALL_USAGE.md](INSTALL_USAGE.md), 보안 경계는
[SECURITY.md](SECURITY.md), Codex 실행 절차는
[codex/skills/minutes/SKILL.md](codex/skills/minutes/SKILL.md)를 따른다. 문서와 코드가
다르면 `scripts/`, `config.example.env`, 자동화 테스트에서 확인되는 실제 동작을 우선하고
문서를 함께 수정한다.

## 1. 목표와 범위

영상 또는 오디오 파일을 Mac에서 로컬 분석해 원문 근거가 보존된 Markdown과 DOCX 문서를
만든다. 입력을 회의나 특정 영상 서비스로 가정하지 않는다. 강의, 기술 발표, 인터뷰,
토론, 업무 협의, 데모 등 실제 내용에 따라 제목, 문서 유형, 섹션을 결정한다.

다음 원칙을 고정한다.

- `회의록`, `Meeting Minutes`, `영상 요약` 같은 제목이나 회의 전용 목차를 고정하지 않는다.
- Zoom, Teams, Meet, OBS 등 서비스별 프로필을 만들지 않는다.
- STT와 OCR은 감지한 원문 언어를 유지한다.
- 최종 언어만 `OUTPUT_LANGUAGE`로 선택한다.
- 로컬 음향 화자분리는 자동 처리에서 실행하지 않는다.
- 원문에 없는 사실이나 화자 이름을 만들지 않는다.
- 글자 수, 토큰 수, 페이지 수, bullet 수, section 수의 하드 상한을 두지 않는다.
- 최종 폴더에는 전달에 필요한 파일만 남긴다.
- 전역 설치된 skill은 `allow_implicit_invocation: false`로 두고 `$minutes`를 명시한
  요청에서만 활성화해 다른 프로젝트 세션에 자동 주입하지 않는다.
- Codex 문서 합성은 전처리를 지시한 긴 부모 대화를 상속하지 않는 새 ephemeral 세션에서
  수행한다. 전사·OCR을 요약해 handoff하지 않고 새 세션이 job의 전체 근거를 직접 읽는다.

지원 확장자는 `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`,
`.ogg`다. OCR은 영상 입력에만 적용한다.

## 2. 경로와 입력 파일 정책

경로는 사용자 이름이나 clone 위치를 하드코딩하지 않는다.

```env
MINUTES_HOME=~/minutes
RECORDINGS_INBOX=~/remind
```

기본 구조는 다음과 같다.

```text
~/remind/                    기본 입력 위치
~/minutes/
  jobs/                      처리 근거, 상태, 재작업 자료
  models/                    고정된 로컬 추론 모델
  output/                    최종 전달물
```

`models/`에는 해시가 고정된 Silero ONNX를 한 번 준비한다. 영상마다 증가하지 않는다.

입력 미디어의 이동 정책은 다음과 같다.

- `RECORDINGS_INBOX` 바로 아래 파일은 분석 성공 후 job을 거쳐 최종 output으로 이동한다.
  입력 위치에 중복본을 남기지 않는다.
- inbox 밖의 파일을 직접 지정하면 원본을 보존하고 job 작업본만 최종 output으로 이동한다.
- 분석이나 보관에 실패하면 원본을 잃지 않도록 이동을 완료하지 않거나 원래 job 위치로
  복구한다.

결과 날짜는 원본 파일명에 유효한 `YYYY-MM-DD`가 있으면 이를 우선하고, 없으면 미디어의
수정 시각을 사용한다. 처리 완료일로 영상 날짜를 바꾸지 않는다.

## 3. 전체 처리 흐름

```text
입력 파일 안정성 확인 또는 수동 경로 검증
  → 단일 heavy-job 잠금과 job 생성
  → ffmpeg 오디오 추출
  → MLX Whisper 원문 언어 STT
  → Silero ONNX 발화 존재 검증(`validate_speech_activity`)
  → 영상이면 프레임 추출·로컬 OCR·Snapshot 선별
  → 전체 근거의 크기·SHA-256·Snapshot 수를 handoff manifest에 기록
  → job 경로·정책만 받은 새 `codex exec --ephemeral` 세션 실행
  → 새 세션이 전체 STT/OCR/Snapshot을 직접 읽음
  → 근거 기반 화자 판단과 내용 inventory 작성
  → 내용 기반 최종 Markdown 작성
  → 내용 보존 audit와 선택적 공식 근거 확인
  → DOCX 생성·렌더 검증
  → 날짜와 H1 제목으로 최종 폴더 정리
  → 임시 파일 정리와 완료 job 보존기간 적용
```

각 입력은 `~/minutes/jobs/<job_id>/`에서 독립적으로 처리한다. `status.json`은 현재 단계,
실패 원인, 원본 경로, 최종 경로를 기록하고 `process_metrics.json`은 단계별 wall time,
CPU time, 디스크 증감, 적용된 자원 정책을 기록한다.

## 4. 오디오 추출과 STT

`ffmpeg`는 입력에서 16 kHz mono PCM `audio.wav`를 한 번 추출한다. MLX Whisper
`mlx-community/whisper-large-v3-turbo`가 Apple Silicon GPU/Metal 경로에서 이를 읽어
시간 구간이 있는 `transcript.json`, `transcript.txt`, `transcript.srt`를 만든다.

```env
WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
WHISPER_DEVICE=gpu
LANGUAGE=auto
HF_HUB_OFFLINE=1
```

Whisper 캐시는 모델별로 공유되며 영상마다 새로 다운로드하지 않는다.
`HF_HUB_OFFLINE=1`인 실제 처리에서는 Hugging Face에 접속하거나 최신 리비전을 확인하지
않는다. 새 PC에서는 신뢰한 네트워크에서 모델을 최초 한 번 준비한 뒤 오프라인 모드로
처리한다.

`audio.wav`는 STT가 성공하면 다시 만들 수 있는 임시파일이므로
`CLEANUP_JOB_MEDIA_AFTER_ARCHIVE=true`일 때 즉시 제거한다. 오디오 추출과 STT 자체는
화자분리 정책과 무관한 필수 단계다.

## 5. 언어 정책

```env
LANGUAGE=auto
OCR_LANGUAGES=auto
OUTPUT_LANGUAGE=auto
```

- `LANGUAGE=auto`는 STT 입력 언어를 감지하고 전사를 원문 언어로 유지한다.
- `OCR_LANGUAGES=auto`는 영어 입력에 `eng`, 한국어 입력에 `kor+eng`을 선택한다.
- `OUTPUT_LANGUAGE=auto`는 최종 문서도 원문의 지배적인 언어로 작성한다.
- 영어 원문을 한국어로 작성할 때만 `OUTPUT_LANGUAGE=ko`를 지정한다.
- 최종 문서를 영어로 강제해야 할 때만 `OUTPUT_LANGUAGE=en`을 지정한다.

번역이 필요해도 완성된 중간 문서를 만든 뒤 다시 번역하거나 재요약하지 않는다. 원문 STT와
OCR 근거에서 목표 언어의 최종 문서를 직접 작성한다. 영어 입력과 한국어 입력은 오디오
추출, STT 1회, OCR 1회라는 같은 파이프라인을 사용하며 언어 때문에 별도 화자분리나 추가
전사 단계를 실행하지 않는다.

## 6. 영상 OCR과 Snapshot

영상은 기본 10초 간격으로 프레임을 추출한다. 로컬 Tesseract OCR 전에 화면 서명으로
유사 프레임을 거르고, OCR 뒤에는 인접 중복 텍스트를 제거한다. 자막이나 플레이어 UI가
화면 변화로 오인되지 않도록 기본적으로 하단 18%와 우측 20%를 시각 비교에서 제외한다.

```env
OCR_ENABLED=true
OCR_FRAME_INTERVAL_SECONDS=10
OCR_LANGUAGES=auto
OCR_FFMPEG_THREADS=1
OCR_WORKERS=1
OCR_TESSERACT_THREAD_LIMIT=1
OCR_VISUAL_DEDUPE_ENABLED=true
```

공개 설정의 `OCR_WORKERS=1`은 다양한 Mac에서 안전하게 시작하기 위한 기본값이다. 현재
11-core M3 Pro 로컬 프로필은 다음 값을 사용한다.

```env
OCR_WORKERS=5
OCR_TESSERACT_THREAD_LIMIT=1
```

서로 다른 프레임은 최대 5개 Tesseract 프로세스로 처리하되 각 프로세스의 OpenMP 내부
스레드는 1개로 제한한다. 병렬 결과는 반드시 타임스탬프 순서로 다시 적용해 기존 화면·텍스트
중복 제거 결과와 Snapshot 번호가 worker 수에 따라 달라지지 않게 한다.

원본 `frames/`는 OCR 성공 후 제거한다. 의미 있는 선별 이미지만 job의 `snapshots/`에 두고
최종 문서 확인과 보관까지 유지한다. Tesseract가 없거나 OCR이 실패해도 STT와 문서 생성은
계속하며 실패 상태를 `screen_text.json`에 기록한다.

## 7. 화자 판단 정책

자동 처리 설정은 다음으로 고정한다.

```env
SPEAKER_ATTRIBUTION_MODE=evidence
SPEAKER_ATTRIBUTION_REQUIRED=false
SPEECH_ACTIVITY_VALIDATION_ENABLED=true
```

화자 이름은 시간 정보가 있는 STT, OCR, 필요한 소수의 선별 Snapshot을 함께 확인해 판단한다.
이름이 표시된 자막이나 이름표도 발화 시점과 대응해야 하며, 참가자 목록·화면 공유자·단순히
화면에 보이는 이름만으로 현재 화자를 확정하지 않는다. 특정 서비스, 초록색 테두리, 색상,
고정 레이아웃을 화자 증거로 가정하지 않는다.

화면 근거가 없으면 자기소개, 직접 호명 뒤 응답, 명확한 발언 인계 같은 STT 근거만 사용할
수 있다. 근거가 약하거나 충돌하면 `화자 미상` 또는 `unknown speaker`로 남긴다. 화자명을
모른다는 이유로 해당 발언 내용을 생략하지 않는다.

SpeechBrain ECAPA/x-vector, pyannote를 이용한 별도 음향 화자분리는 화면 근거가
충분하거나 부족한 경우 모두 자동 실행하지 않는다. Silero ONNX는 발화 존재 검증만 한다. `audio`, `hybrid`, 강제 화자 수,
`SPEAKER_ATTRIBUTION_REQUIRED=true`는 거부한다. 완료 job의
`speaker_attribution_report.json`에는
`local_audio_diarization=disabled_by_policy`가 기록되어야 하며,
`process_metrics.json`에는 `diarize` 또는 `attribute_speakers` 단계가 없어야 한다.

## 8. 문서 생성과 내용 보존

Codex 모드의 현재 로컬 품질 설정은 다음과 같다.

```env
LLM_PROVIDER=codex
CONTENT_AUDIT_MODE=strict
OFFICIAL_SOURCE_VERIFICATION=auto
```

전처리 후 부모 세션은 전체 `codex_minutes_input.md`, transcript, OCR, Snapshot을 읽지 않고
`./scripts/run_fresh_codex_job.py`에 준비된 job 경로를 전달한다. launcher prompt에는 job 경로,
`OUTPUT_LANGUAGE`, 감사·공식 근거 정책, 짧은 작업별 추가 요청만 넣는다. 원문은 prompt에
복사하지 않으며 새 세션이 job 파일을 직접 읽는다. `fresh_codex_handoff.json`은
`parent_conversation_inherited=false`, `raw_evidence_embedded_in_handoff=false`, 입력 파일
크기와 SHA-256, Snapshot 수, 실행 시간과 완료 상태를 기록한다. fresh worker는 재귀적으로
다른 worker를 실행할 수 없고, Codex가 정상 종료해도 `status.json=completed`와 실제 보관
파일이 확인되지 않으면 실패로 처리한다.

macOS Codex seatbelt 안에서는 중첩 app-server 초기화가 차단되므로 launcher 명령 자체만
처음부터 escalation한다. 재사용 승인은 `./scripts/run_fresh_codex_job.py`의 정확한 prefix로
제한한다. launcher가 만드는 worker는 다시 `workspace-write` 샌드박스를 적용하고 repo와
설정된 `MINUTES_HOME`만 쓰기 가능 경로로 받는다.

최종 H1, 문서 유형, H2/H3 구조는 파일명이나 플랫폼이 아니라 실제 내용을 바탕으로 정한다.
최종 문서는 영상에서 전달된 내용을 기본적으로 그대로 보존하고, 인사·말버릇·의미가 완전히
같은 반복만 축약할 수 있다.

`strict` 모드는 문서 작성 전에 `content_inventory.json`, 작성 후
`content_audit.json`을 요구한다. 날짜, 버전, 수치, 범위, 단위, 조건, 예외, 부정 표현,
제한, 위험, 질의응답, STT/OCR 충돌을 추적한다. 원문이 한 context보다 길면 타임스탬프
순서로 읽어 하나의 누적 inventory에 추가한다. 구간별 축약본을 만든 뒤 다시 요약하는
손실성 병합은 하지 않는다. 감사가 통과하기 전에는 최종 보관하지 않는다.

녹음된 내용은 무엇이 말해졌는지를 판단하는 기준이다. STT와 OCR이 모호하거나 충돌하면
먼저 주변 음성 문맥과 Snapshot을 확인한다. 그래도 제품명, 버전, 정책, API 동작 등이
불분명할 때만 최신 공식 문서, 공식 release note, service announcement, 표준 원문 또는
upstream 보안 권고를 조사한다. 공식 정보가 영상 발언과 다르더라도 영상 내용을 조용히
바꾸지 않는다.

외부 근거를 사용하면 최종 문서 맨 아래에 한국어는 `## 외부 근거 확인`, 영어는
`## External Evidence Check`를 추가한다. 전사·OCR 보강 근거와 영상 내용에 상충하는
근거를 구분하고 timestamp, 조사 목적, 공식 확인 결과, 확인일, 링크를 기록한다. 그 뒤에
다른 H2를 추가하지 않는다.

## 9. DOCX 계약

Markdown H1을 표지 제목과 최종 폴더명·파일명에 사용한다. DOCX에는 내용 기반 표지,
본문과 같은 번호를 가진 정적 목차, 일치하는 bookmark와 내부 링크, 언어별 스타일,
명시적인 표 너비, 반복 표 머리글, 가능한 범위의 행 분할 방지, 바닥글 페이지 번호가 있어야
한다.

macOS Codex sandbox에서 `soffice`를 직접 시험 실행하지 않는다. 반복되는 LibreOffice crash
dialog를 막기 위해 다음 guard를 처음부터 sandbox 밖의 허용된 실행으로 사용한다.

```bash
python scripts/render_docx_checked.py \
  "/absolute/path/to/final.docx" \
  --output_dir /private/tmp/minutes-docx-render \
  --emit_pdf
```

렌더링한 모든 페이지에서 표지, 목차 번호와 링크, 표, 마지막 페이지를 확인한다.

## 10. 최종 산출물과 보존주기

최종 폴더는 다음 형식이다.

```text
~/minutes/output/YYYY-MM-DD/
  내용-기반-제목/
    YYYY-MM-DD_내용-기반-제목.mov 또는 YYYY-MM-DD_내용-기반-제목.m4a
    YYYY-MM-DD_내용-기반-제목.md
    YYYY-MM-DD_내용-기반-제목.docx
    snapshots/
      snapshot_0001_00-00-00.jpg
```

최종 폴더에는 이름을 변경한 미디어, Markdown 하나, DOCX 하나, 의미 있는 Snapshot만 둔다.
transcript, OCR JSON/TXT, 화자 근거 보고서, inventory, audit, 상태, 로그, metrics는
`jobs/<job_id>/`에만 둔다. `.DS_Store`는 macOS Finder가 다시 만들 수 있는 메타데이터이며
프로젝트 산출물이나 Git 추적 대상이 아니다.

```env
CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=true
CLEANUP_JOB_MEDIA_AFTER_ARCHIVE=true
COMPLETED_JOB_RETENTION_HOURS=24
```

완료 job은 재작업을 위해 기본 24시간 보관한다. 다음 처리 또는 아카이브 시 만료된 완료
job의 최종 미디어, Markdown, 선택적 DOCX와 Snapshot 존재 여부와 output 경로 안전성을
검증한 뒤 job 폴더 전체를 삭제한다. 실패, 진행 중, Codex 입력 대기 job과 `jobs/index.json`,
`.process.lock`은 자동 삭제하지 않는다.

## 11. CPU와 성능 정책

자원 제한은 Mac 전체의 전역 설정이 아니라 이 프로젝트 프로세스와 자식 프로세스에만
적용한다. 다른 프로젝트의 QoS나 nice 값을 변경하지 않는다. watcher와 수동 실행을 합쳐
무거운 job은 한 번에 하나만 실행한다.

```env
PROCESS_QOS=utility
PROCESS_NICE=10
AUDIO_FFMPEG_THREADS=1
AUDIO_CPU_LIMIT_PERCENT=60
OCR_FFMPEG_THREADS=1
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80
OCR_WORKERS=5
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=10
```

CPU limit 값은 Mac 전체 사용률의 정밀한 hard cap이 아니라 해당 외부 프로세스의 평균
부하를 낮추는 duty-cycle 근사값이다. MLX Whisper는 GPU/Metal 경로라 이 CPU 백분율
제한의 직접 대상이 아니다.

28분 36초 한국어 영상의 동일한 172개 프레임으로 검증한 결과는 다음과 같다.

- 1-worker 기준 OCR: 290.169초
- 5-worker OCR: 248.646초
- 단축: 41.5초, 14.3%
- 선별 Snapshot: 두 실행 모두 50개
- timestamp, source frame, OCR text: 완전 일치
- 전체 시스템 CPU: 평균 23.9%, p95 51.8%, 최대 69.1%
- 동시 OCR 프로세스 최대: 5

이 측정에서 70%는 5→6 worker 비교 여부를 정하기 위한 일회성 중단 기준이었다. 스킬이나
일반 런타임의 CPU 상한이 아니며, 실행 중 CPU 사용률로 worker 수를 자동 조정하는 로직도
없다. 실제 처리는 `.env`의 `OCR_WORKERS` 명시값을 그대로 사용한다. 가장 큰 잔여 병목은
OCR worker가 아니라 duty-cycle이 적용된 프레임 추출 약 221.6초다. 이후 최적화는
hardware decode 또는 프레임 추출 방식의 동등성과 자원 사용량을 별도 검증한 뒤 명시적인
설정 변경으로 적용한다.

## 12. 네트워크와 보안 경계

ffmpeg, MLX Whisper, Tesseract OCR과 표준 화자 근거 처리는 로컬에서 실행한다. 미디어,
전사, OCR, Snapshot은 사용자가 선택한 LLM provider 외부로는 보내지 않는다. Codex가 최신
공식 근거를 검색할 때도 공개 제품명, 버전, 일반화한 정책 검색어만 사용하며 원문 STT/OCR,
Snapshot, 참석자·고객 이름, 내부 식별자, 비밀정보를 검색 서비스로 보내지 않는다.

표준 `process_file.py` 경로는 로컬의 해시 검증된 Silero ONNX만 발화 존재 검증에 사용한다.
ECAPA·pyannote·사용자 제공 `.pkl`, `.joblib`, `.ckpt`, `.pt` 모델은 로드하지 않는다.
세부 버전·해시·라이선스 정책은 `SECURITY.md`를 따른다.

## 13. 공개 기본값과 현재 로컬 프로필

`config.example.env`는 새 clone과 다양한 Mac을 위한 보수적 기본값을 제공한다.

```env
OUTPUT_LANGUAGE=auto
CONTENT_AUDIT_MODE=off
OFFICIAL_SOURCE_VERIFICATION=off
OCR_WORKERS=1
OCR_TESSERACT_THREAD_LIMIT=1
COMPLETED_JOB_RETENTION_HOURS=24
```

현재 검증된 로컬 `.env`는 품질 감사와 공식 근거 확인을 활성화하고 M3 Pro 측정값을 적용한다.

```env
LLM_PROVIDER=codex
OUTPUT_LANGUAGE=auto
CONTENT_AUDIT_MODE=strict
OFFICIAL_SOURCE_VERIFICATION=auto
OCR_WORKERS=5
OCR_TESSERACT_THREAD_LIMIT=1
```

두 설정의 차이는 문서 불일치가 아니다. 공개 기본값은 외부 검색을 자동으로 시작하지 않고
낮은 CPU에서 출발하며, 현재 로컬 프로필은 사용자가 합의한 품질과 성능 설정이다.

## 14. 검증과 완료 기준

변경 후 최소 검증은 다음과 같다.

```bash
.venv/bin/python -m py_compile scripts/*.py
.venv/bin/python -m unittest discover -s tests -v
```

실제 미디어 처리 완료 기준은 다음과 같다.

1. STT와 영상 OCR이 원문 언어로 한 번씩 실행된다.
2. 로컬 음향 화자분리 산출물이나 단계가 없다.
3. 근거 없는 화자 이름이나 강제 화자 수가 없다.
4. 내용 inventory와 audit이 필수 사실의 누락·변형 없이 통과한다.
5. 외부 공식 근거가 영상 발언을 덮어쓰지 않고 마지막 부록에 분리된다.
6. H1과 문서 유형·섹션이 실제 내용에서 결정된다.
7. DOCX 목차 번호·본문 번호·bookmark 링크가 일치한다.
8. 최종 폴더에는 미디어, MD, DOCX, 의미 있는 Snapshot만 있다.
9. inbox 바로 아래 원본은 성공 후 중복 없이 이동되고 외부 원본은 보존된다.
10. `process_metrics.json`에 worker, CPU 정책, 단계별 시간과 디스크 증감이 기록된다.
11. 완료 job은 24시간 후 최종 산출물 검증을 통과한 경우에만 정리된다.

위 계약을 변경할 때는 코드, 설정 예시, README, 설치 가이드, 보안 문서, 저장소 스킬과
문서 계약 테스트를 같은 변경에서 함께 갱신한다.
