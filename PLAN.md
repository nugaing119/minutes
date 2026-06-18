# Mac Local Meeting Minutes Plan

## 1. 목표

OBS로 저장된 회의 녹화 파일을 Mac에서 로컬 처리해 전사 파일, OCR 보조 자료, 한국어 회의록 Markdown, DOCX를 생성한다.

기본 입력 폴더는 `~/remind/`다. 자동 감시 모드에서는 이 폴더에 새로 저장된 OBS 녹화 파일을 감지한다. 수동 실행 모드에서는 영상 파일 경로를 직접 지정하며, 이 경우 파일이 꼭 `~/remind/` 안에 있을 필요는 없다. 시스템은 파일 저장 완료 또는 지정된 입력 파일을 기준으로 오디오 추출, STT, OCR, 회의록 생성, 최종 파일 정리를 순서대로 수행한다.

최종 결과물은 회의록이 저장된 시점의 로컬 날짜 폴더에 모은다.

```text
~/minutes/output/YYYY-MM-DD/
  회의-주제/
    YYYY-MM-DD_회의-주제.mp4
    YYYY-MM-DD_회의-주제.md
    YYYY-MM-DD_회의-주제.docx
    YYYY-MM-DD_회의-주제.transcript.txt
    YYYY-MM-DD_회의-주제.transcript.json
    YYYY-MM-DD_회의-주제.transcript.srt
    YYYY-MM-DD_회의-주제.screen_text.txt
    YYYY-MM-DD_회의-주제.screen_text.json
    snapshots/
```

## 2. 기본 경로 정책

프로젝트 코드는 git clone 받은 위치에 둔다. OBS 녹화본은 기본적으로 사용자 홈 디렉터리의 `remind/`에 저장한다. 내부 작업 파일과 결과물은 repo 내부가 아니라 사용자 홈 디렉터리 아래 `minutes/`에 저장한다.

기본 입력 위치는 `~/remind`이고, 기본 작업/결과 저장 위치는 `~/minutes`다.

코드에서는 하드코딩하지 않고 `RECORDINGS_INBOX`와 `MINUTES_HOME` 변수로 관리한다.

```python
from pathlib import Path
import os

MINUTES_HOME = Path(os.environ.get("MINUTES_HOME", "~/minutes")).expanduser()
RECORDINGS_INBOX = Path(os.environ.get("RECORDINGS_INBOX", "~/remind")).expanduser()

JOBS_DIR = MINUTES_HOME / "jobs"
OUTPUT_DIR = MINUTES_HOME / "output"
```

사용자는 환경변수로 저장 위치를 바꿀 수 있다.

```bash
export MINUTES_HOME="$HOME/Documents/minutes"
export RECORDINGS_INBOX="$HOME/Movies/OBS"
```

이 원칙 때문에 어떤 사용자가 repo를 clone해도 기본적으로 자기 홈 디렉터리의 `remind/`와 `minutes/` 기준으로 바로 사용할 수 있다.

## 3. 디렉터리 구조

repo에는 실행 코드와 설정 예시만 둔다.

```text
meeting-minutes/
  scripts/
    transcribe.py
    summarize.py
    watch_recordings.py
  config.example.env
  requirements.txt
  README.md
  PLAN.md
```

자동 감시 대상 입력은 `RECORDINGS_INBOX`에 둔다. 수동 실행 시에는 임의 경로의 영상 파일을 직접 지정할 수 있다. 작업 파일과 최종 결과물은 `MINUTES_HOME` 아래에 저장한다.

```text
~/remind/
  zoom_meeting.mp4

~/minutes/
  jobs/
    <job_id>/
      source.mp4
      audio.wav
      transcript.json
      transcript.txt
      transcript.srt
      screen_text.json
      screen_text.txt
      snapshots/
      minutes.raw.json
      minutes.md
      status.json
      logs.txt

  output/
    2026-06-17/
      신규-회의록-서비스-MVP-검토/
        2026-06-17_신규-회의록-서비스-MVP-검토.mp4
        2026-06-17_신규-회의록-서비스-MVP-검토.md
        2026-06-17_신규-회의록-서비스-MVP-검토.docx
        2026-06-17_신규-회의록-서비스-MVP-검토.transcript.txt
        2026-06-17_신규-회의록-서비스-MVP-검토.transcript.json
        2026-06-17_신규-회의록-서비스-MVP-검토.transcript.srt
        2026-06-17_신규-회의록-서비스-MVP-검토.screen_text.txt
        2026-06-17_신규-회의록-서비스-MVP-검토.screen_text.json
        snapshots/
```

`jobs/`는 내부 처리, 실패 복구, 디버깅을 위한 공간이다.

`output/`은 사용자가 실제로 열어보는 최종 결과물 공간이다.

## 4. 전체 처리 흐름

```text
영상 파일
  ↓
파일 생성 완료 감지 또는 수동 지정
  ↓
~/minutes/jobs/<job_id>/ 생성
  ↓
source.mp4 복사
  ↓
ffmpeg로 audio.wav 추출
  ↓
mlx-whisper로 STT
  ↓
transcript.json / transcript.txt / transcript.srt 생성
  ↓
10초 간격 프레임 추출
  ↓
로컬 OCR로 screen_text.json / screen_text.txt 생성
  ↓
설정된 LLM provider로 회의록 구조화
  ↓
minutes.raw.json 생성
  ↓
minutes.md 생성
  ↓
회의 주제 추출
  ↓
저장 시점 날짜 계산
  ↓
~/minutes/output/YYYY-MM-DD/ 생성
  ↓
영상, 회의록, 전사 파일을 같은 basename으로 저장
```

## 5. 파일 감지

자동 감시 모드는 `~/remind/` 폴더를 감시한다. 설정값 `RECORDINGS_INBOX`로 다른 폴더를 지정할 수 있다.

수동 실행 모드는 감시 폴더와 무관하게 사용자가 지정한 영상 파일을 바로 처리한다.

```bash
python scripts/process_file.py "/Users/jun/Desktop/2026-06-18 고객 미팅.mov"
```

우선 지원할 입력 확장자는 다음과 같다.

```text
.mp4
.mkv
.mov
```

OBS 녹화 파일은 저장 중에도 파일이 먼저 보일 수 있으므로, 생성 이벤트만 보고 바로 처리하지 않는다. 파일 크기와 수정 시각이 일정 시간 동안 변하지 않을 때 저장 완료로 판단한다.

권장 흐름은 다음과 같다.

```text
새 파일 발견
  ↓
파일 크기와 수정 시각 확인
  ↓
WATCH_POLL_SECONDS 뒤 다시 확인
  ↓
WATCH_STABLE_SECONDS 동안 변화가 없으면 저장 완료로 판단
  ↓
job 생성 후 처리 시작
```

## 6. Job 생성과 중복 방지

각 영상 파일마다 고유한 `job_id`를 만든다. 파일명만 기준으로 삼으면 같은 이름의 파일이 다시 들어왔을 때 충돌할 수 있으므로 다음 정보를 함께 사용한다.

```text
원본 파일 경로
파일 크기
수정 시각
필요하면 해시 일부
```

예시다.

```text
~/minutes/jobs/20260617_143000_zoom_meeting_ab12cd34/
```

완료된 파일은 다시 처리하지 않는다. 처리 이력은 각 job의 `status.json`과 전체 index 파일로 추적할 수 있다.

```text
~/minutes/jobs/index.json
```

index에는 원본 파일 fingerprint, 최종 output 경로, basename을 기록한다.

## 7. 오디오 추출

`ffmpeg`로 영상에서 오디오를 추출한다.

출력 위치는 다음과 같다.

```text
~/minutes/jobs/<job_id>/audio.wav
```

기본 포맷은 STT에 맞춰 단순하게 유지한다.

```text
mono
16kHz
wav
```

명령 개념은 다음과 같다.

```bash
ffmpeg -y -i source.mp4 -vn -ac 1 -ar 16000 audio.wav
```

## 8. STT 전사

전사는 `mlx-whisper`로 수행한다.

기본 모델은 다음과 같다.

```text
mlx-community/whisper-large-v3-turbo
```

모델과 언어는 설정값으로 관리한다.

```env
WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
WHISPER_DEVICE=gpu
LANGUAGE=auto
```

`LANGUAGE=auto`는 STT 입력 언어 자동 감지를 의미한다. 영어 회의는 영어로 전사한 뒤 회의록 생성 단계에서 한국어로 정리한다. 한국어 회의는 한국어 전사를 기반으로 한국어 회의록을 만든다. 특정 입력 언어를 강제하고 싶을 때만 `LANGUAGE=ko` 또는 `LANGUAGE=en`처럼 지정한다.

`WHISPER_DEVICE=gpu`는 Apple Silicon에서 MLX Metal/GPU 경로를 명시적으로 사용한다.

전사 결과는 세 가지 파일로 저장한다.

```text
transcript.json
transcript.txt
transcript.srt
```

각 파일의 역할은 다음과 같다.

```text
transcript.json
- segment, timestamp 등 구조화 정보 보존
- 후속 처리와 재처리에 사용

transcript.txt
- 회의록 생성 입력
- 사람이 빠르게 확인하기 쉬운 텍스트

transcript.srt
- 영상과 맞춰 검수하기 좋은 자막 파일
```

## 9. OCR 처리

OCR은 화면 공유 자료나 슬라이드의 텍스트를 회의록 생성 입력에 보조 근거로 넣기 위한 단계다.

기본 설정은 다음과 같다.

```env
OCR_ENABLED=true
OCR_FRAME_INTERVAL_SECONDS=10
OCR_LANGUAGES=kor+eng
OCR_MAX_CONTEXT_CHARS=12000
OCR_FFMPEG_THREADS=1
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=10
OCR_FRAME_PAUSE_SECONDS=0
OCR_VISUAL_DEDUPE_ENABLED=true
OCR_VISUAL_DEDUPE_IGNORE_BOTTOM_RATIO=0.18
OCR_VISUAL_DEDUPE_IGNORE_RIGHT_RATIO=0.20
OCR_VISUAL_DEDUPE_MAX_MEAN_DELTA=6.0
AUDIO_CPU_LIMIT_PERCENT=60
AUDIO_CPU_LIMIT_PERIOD_SECONDS=0.2
AUDIO_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80
OCR_FRAME_EXTRACT_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_FRAME_EXTRACT_CPU_LIMIT_FALLBACK_BURST_CORES=1.5
OCR_SIGNATURE_CPU_LIMIT_PERCENT=0
OCR_SIGNATURE_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_SIGNATURE_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_TESSERACT_CPU_LIMIT_PERCENT=0
OCR_TESSERACT_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_TESSERACT_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=true
```

기본 동작은 다음과 같다.

```text
영상 파일
  ↓
ffmpeg로 10초마다 프레임 1장 추출
  ↓
로컬 tesseract OCR 실행
  ↓
화면 유사도 기반 중복 프레임 선제 제거
  ↓
인접 중복 텍스트 제거
  ↓
중복 제거 후 남은 프레임을 snapshots/에 이미지로 저장
  ↓
screen_text.json / screen_text.txt 저장
```

OCR 산출물은 job 폴더와 최종 output 폴더에 함께 저장한다.

```text
screen_text.json
screen_text.txt
snapshots/
  snapshot_0001_00-00-00.jpg
  snapshot_0002_00-00-10.jpg
```

OCR 결과는 회의록 생성 입력에 `[화면 공유 OCR 텍스트]` 섹션으로 추가한다. 이 텍스트는 음성 전사를 보완하는 근거로만 사용하고, OCR 오인식이 의심되는 내용은 단정하지 않는다.

최종 output 폴더에는 날짜 폴더 아래 회의 주제명으로 된 `회의-주제/` 폴더를 만들고, 그 안의 `snapshots/`에 OCR 중복 제거를 통과한 snapshot 이미지만 복사한다.

최종 output 복사가 끝나면 기본적으로 job 내부의 `frames/`와 `snapshots/`를 삭제한다. `screen_text.json/txt`는 작고 재검토에 필요하므로 job에도 남긴다. 디버깅을 위해 원본 OCR 이미지를 job에 남기고 싶으면 `CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=false`로 끈다.

`tesseract`가 설치되어 있지 않거나 OCR이 실패하면 `screen_text.json`에 `skipped` 또는 `failed` 상태를 남긴다. 이 경우에도 STT와 회의록 생성은 계속 진행한다.

CPU peak를 낮추기 위해 단계별 외부 프로세스 제한을 분리한다. 짧지만 peak가 큰 오디오 추출 `ffmpeg`는 `AUDIO_CPU_LIMIT_PERCENT`로 제어하고, OCR 프레임 추출 `ffmpeg`는 `OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT`로 따로 제어한다. 화면 서명 계산과 Tesseract OCR도 각각 `OCR_SIGNATURE_CPU_LIMIT_PERCENT`, `OCR_TESSERACT_CPU_LIMIT_PERCENT`로 분리되어 있다.

기본 balanced 설정은 오디오 추출과 OCR 프레임 추출만 제한하고, 화면 서명과 Tesseract는 처리량 유지를 위해 별도 CPU 제한을 끈다. Tesseract는 `OCR_TESSERACT_THREAD_LIMIT=1`과 `OCR_TESSERACT_NICE=10`으로 스레드 수와 우선순위를 낮춘다. 더 낮은 부하가 필요하면 `OCR_FRAME_INTERVAL_SECONDS`를 늘리고, `OCR_TESSERACT_NICE`를 높이며, `OCR_FRAME_PAUSE_SECONDS`로 프레임 사이 대기 시간을 둔다.

## 10. LLM provider 정책

회의록 생성에는 LLM API를 사용한다. 구현은 특정 벤더에 고정하지 않고 provider 인터페이스로 분리한다.

초기 provider는 다음 두 가지를 대상으로 한다.

```text
openai
oci
```

설정값으로 사용할 provider를 고른다.

```env
LLM_PROVIDER=openai
```

OpenAI API를 사용할 때 필요한 설정은 다음과 같다.

```env
OPENAI_API_KEY=
OPENAI_MODEL=
```

OCI GenAI API를 사용할 때 필요한 설정은 다음과 같다.

```env
OCI_GENAI_MODEL=
OCI_GENAI_COMPARTMENT_ID=
OCI_GENAI_ENDPOINT=
OCI_CONFIG_FILE=~/.oci/config
OCI_PROFILE=DEFAULT
```

구현에서는 provider별 호출부만 분리하고, 회의록 생성 파이프라인은 동일하게 유지한다.

```text
transcript.txt
  ↓
provider.generate_minutes_json(...)
  ↓
minutes.raw.json
  ↓
minutes.md
```

provider 구현은 다음 계약을 맞춘다.

```text
입력:
- 전사 텍스트 또는 전사 chunk 목록
- 회의록 생성 프롬프트
- 출력 JSON schema 설명
- 출력 언어 설정

출력:
- meeting_title
- summary
- decisions
- action_items
- discussion
- open_questions
```

회의록 출력 언어는 한국어로 고정한다. 영어는 제품명, 회사명, 사람 이름, API 이름, 코드명, 명령어, 원문 의미 보존이 필요한 짧은 인용에만 사용한다.

LLM 호출 실패는 `status.json`에 `step: summarize`로 기록한다. 인증 실패, quota 부족, 네트워크 실패, 응답 JSON 파싱 실패는 구분해서 로그에 남긴다.

구현 시점에는 각 provider의 최신 공식 문서를 기준으로 SDK, 인증, 요청/응답 형식을 확인한다.

## 11. 회의록 생성

전사 결과에서 바로 Markdown만 만들지 않고, 먼저 구조화된 중간 JSON을 만든다.

```text
transcript.txt
  ↓
minutes.raw.json
  ↓
minutes.md
```

`minutes.raw.json`은 회의록 생성 결과의 원본 구조다.

```json
{
  "meeting_title": "신규 회의록 서비스 MVP 검토",
  "summary": [
    "OBS 녹화 파일을 기반으로 Mac 로컬 회의록 시스템을 만든다.",
    "mlx-whisper를 사용해 로컬 전사를 수행한다.",
    "최종 결과물은 저장 시점 날짜 폴더에 정리한다."
  ],
  "decisions": [
    "회의록은 Markdown 파일로 저장한다.",
    "원본 영상과 회의록은 같은 날짜 폴더에 배치한다."
  ],
  "action_items": [
    {
      "owner": "미정",
      "task": "단일 파일 STT 스크립트 작성",
      "due": "미정",
      "evidence": "MVP 처리 흐름 논의"
    }
  ],
  "open_questions": [
    "초기 요약 모델 설정 결정 필요"
  ]
}
```

최종 Markdown은 다음 형식을 사용한다.

```markdown
# 회의록

## 1. 회의 요약
- 핵심 내용을 5개 이내로 요약

## 2. 주요 결정사항
- 결정된 내용만 작성

## 3. 액션 아이템
| 담당자 | 할 일 | 기한 | 근거 |
|---|---|---|---|

## 4. 논의 상세
### 주제명
- 논의 내용
- 쟁점
- 결론

## 5. 확인 필요 사항
- 전사상 불명확하거나 추가 확인이 필요한 내용
```

회의록 본문 작성 규칙은 다음과 같다.

```text
회의록은 반드시 한국어로 작성한다.
영어는 필요한 경우에만 유지한다.
한국어 문장은 자연스럽게 끝낸다.
전사 오류는 문맥상 자연스럽게 보정하되 없는 내용을 만들지 않는다.
불확실한 내용은 단정하지 않고 확인 필요 사항에 적는다.
```

긴 회의는 전사 전체를 한 번에 처리하지 않고 chunk 단위로 나눈다.

```text
전사 segment
  ↓
5~10분 단위 chunk
  ↓
chunk별 요약/결정/액션 후보 추출
  ↓
전체 병합
  ↓
최종 minutes.raw.json 생성
  ↓
minutes.md 생성
```

## 12. 파일명 생성

최종 파일명은 저장 시점 날짜와 회의 주제를 결합한다.

```text
YYYY-MM-DD_회의-주제
```

예시다.

```text
2026-06-17_신규-회의록-서비스-MVP-검토
```

회의 주제는 `minutes.raw.json`의 `meeting_title`을 사용한다.

파일명 안전화 규칙은 다음과 같다.

```text
공백은 -로 바꾼다.
슬래시 등 경로 문자는 제거한다.
제목이 너무 길면 적당히 자른다.
제목이 비어 있으면 원본 파일명을 기반으로 fallback한다.
같은 이름이 이미 있으면 -2, -3처럼 suffix를 붙인다.
```

## 13. 최종 저장

회의록 생성이 끝난 시점의 로컬 날짜를 계산한다.

```text
saved_date = YYYY-MM-DD
```

그 날짜로 output 폴더를 만들고, 그 아래에 회의별 폴더를 만든다.

```text
~/minutes/output/YYYY-MM-DD/
  회의-주제/
```

그리고 회의별 폴더 안에 같은 basename으로 파일들을 저장한다.

```text
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.mp4
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.md
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.docx
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.transcript.txt
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.transcript.json
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.transcript.srt
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.screen_text.txt
~/minutes/output/YYYY-MM-DD/회의-주제/YYYY-MM-DD_회의-주제.screen_text.json
~/minutes/output/YYYY-MM-DD/회의-주제/snapshots/
```

초기 구현에서는 원본 영상을 이동하지 않고 복사한다.

```text
~/remind/zoom_meeting.mp4
  ↓ copy
~/minutes/output/2026-06-17/2026-06-17_신규-회의록-서비스-MVP-검토.mp4
```

## 14. 상태 관리

각 job은 `status.json`을 가진다.

처리 중 예시다.

```json
{
  "status": "running",
  "step": "transcribing",
  "source": "~/remind/zoom_meeting.mp4",
  "started_at": "2026-06-17T14:30:00+09:00"
}
```

완료 예시다.

```json
{
  "status": "completed",
  "source": "~/remind/zoom_meeting.mp4",
  "completed_at": "2026-06-17T14:45:12+09:00",
  "output_dir": "~/minutes/output/2026-06-17",
  "base_name": "2026-06-17_신규-회의록-서비스-MVP-검토"
}
```

실패 예시다.

```json
{
  "status": "failed",
  "step": "summarize",
  "error": "회의록 생성 실패 메시지",
  "retryable": true
}
```

상태 파일은 다음 용도로 사용한다.

```text
이미 완료된 파일 재처리 방지
실패한 job 재시도
실패 단계 확인
진행률 표시를 위한 기반 데이터
```

## 15. 설정 파일

repo에는 예시 설정 파일을 둔다.

```text
config.example.env
```

초기 예시는 다음과 같다.

```env
MINUTES_HOME=~/minutes
RECORDINGS_INBOX=~/remind
WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
WHISPER_DEVICE=gpu
LANGUAGE=auto
OUTPUT_LANGUAGE=ko
DOCX_ENABLED=true
AUDIO_SAMPLE_RATE=16000
AUDIO_FFMPEG_THREADS=1
AUDIO_CPU_LIMIT_PERCENT=60
AUDIO_CPU_LIMIT_PERIOD_SECONDS=0.2
AUDIO_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_ENABLED=true
OCR_FRAME_INTERVAL_SECONDS=10
OCR_LANGUAGES=kor+eng
OCR_MAX_CONTEXT_CHARS=12000
OCR_FFMPEG_THREADS=1
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=10
OCR_FRAME_PAUSE_SECONDS=0
OCR_VISUAL_DEDUPE_ENABLED=true
OCR_VISUAL_DEDUPE_IGNORE_BOTTOM_RATIO=0.18
OCR_VISUAL_DEDUPE_IGNORE_RIGHT_RATIO=0.20
OCR_VISUAL_DEDUPE_MAX_MEAN_DELTA=6.0
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80
OCR_FRAME_EXTRACT_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_FRAME_EXTRACT_CPU_LIMIT_FALLBACK_BURST_CORES=1.5
OCR_SIGNATURE_CPU_LIMIT_PERCENT=0
OCR_SIGNATURE_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_SIGNATURE_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_TESSERACT_CPU_LIMIT_PERCENT=0
OCR_TESSERACT_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_TESSERACT_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=true
WATCH_STABLE_SECONDS=15
WATCH_POLL_SECONDS=5
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=
OCI_GENAI_MODEL=
OCI_GENAI_COMPARTMENT_ID=
OCI_GENAI_ENDPOINT=
OCI_CONFIG_FILE=~/.oci/config
OCI_PROFILE=DEFAULT
```

사용자는 필요하면 `.env`로 복사해 조정한다.

```bash
cp config.example.env .env
```

## 16. git 관리 기준

repo에 포함하는 파일은 실행 코드와 문서, 설정 예시다.

```text
scripts/
README.md
requirements.txt
config.example.env
PLAN.md
```

회의 영상, 오디오 파일, 전사 결과, 회의록 결과, job 작업 파일은 `MINUTES_HOME` 아래에 저장한다.

## 17. MVP 완료 기준

MVP는 아래 조건이 모두 충족되면 완료로 본다.

```text
1. git clone 후 설치하면 바로 실행할 수 있다.
2. 기본 입력 위치가 ~/remind로 잡힌다.
3. MINUTES_HOME 환경변수로 저장 위치를 바꿀 수 있다.
4. RECORDINGS_INBOX 환경변수로 입력 위치를 바꿀 수 있다.
5. ~/remind에 영상 파일을 넣을 수 있다.
6. 파일 저장 완료를 감지한다.
7. ~/minutes/jobs/<job_id>를 만든다.
8. ffmpeg로 audio.wav를 만든다.
9. mlx-whisper로 transcript.json/txt/srt를 만든다.
10. OCR이 활성화되어 있으면 10초 간격 프레임 추출과 screen_text.json/txt 및 snapshots 생성을 시도한다.
11. LLM_PROVIDER 설정에 따라 OpenAI API 또는 OCI GenAI API로 회의록 생성을 요청한다.
12. transcript와 screen_text 기반으로 minutes.raw.json을 만든다.
13. minutes.md와 minutes.docx를 한국어 회의록으로 만든다.
14. 저장 시점 날짜로 ~/minutes/output/YYYY-MM-DD 폴더를 만들고 그 안에 영상별 폴더를 만든다.
15. 원본 영상, Markdown/DOCX 회의록, 전사 파일, OCR 파일, snapshot 폴더를 영상별 폴더에 저장한다.
16. status.json에 완료 상태를 기록한다.
17. 이미 처리한 파일은 재처리하지 않는다.
```

## 18. 개발 단계

### Phase 1. 기본 경로와 단일 파일 STT

목표는 git clone 후 `~/minutes`를 기준으로 단일 영상 전사를 수행하는 것이다.

작업 항목은 다음과 같다.

```text
MINUTES_HOME 경로 처리
RECORDINGS_INBOX 경로 처리
기본 폴더 자동 생성
ffmpeg 설치 확인
mp4/mkv/mov 입력 처리
audio.wav 추출
mlx-whisper 전사
transcript.json/txt/srt 저장
```

완료 기준은 다음과 같다.

```text
~/minutes/jobs/<job_id>/transcript.txt 생성
10분 이상 회의 영상 전사 성공
```

### Phase 2. OCR 처리

목표는 화면 공유 텍스트를 회의록 생성 입력에 보조 근거로 포함하는 것이다.

작업 항목은 다음과 같다.

```text
OCR 설정 처리
10초 간격 프레임 추출
tesseract OCR 실행
인접 중복 텍스트 제거
screen_text.json/txt 저장
OCR 실패 시 STT 파이프라인 계속 진행
```

완료 기준은 다음과 같다.

```text
~/minutes/jobs/<job_id>/screen_text.json 생성
최종 output 폴더에 screen_text.json/txt 복사
```

### Phase 3. LLM provider와 Markdown 회의록 생성

목표는 `transcript.txt`에서 회의록 Markdown을 만드는 것이다.

작업 항목은 다음과 같다.

```text
LLM_PROVIDER 설정 처리
OpenAI provider 구현
OCI GenAI provider 구현
한국어 출력 프롬프트 고정
회의록 프롬프트 작성
회의 제목 추출
minutes.raw.json 생성
minutes.md 렌더링
긴 전사 chunk 처리
```

완료 기준은 다음과 같다.

```text
회의 요약, 결정사항, 액션 아이템, 논의 상세, 확인 필요 사항이 포함된 md 생성
```

### Phase 4. 최종 저장 구조

목표는 결과물을 `~/minutes/output/YYYY-MM-DD`에 정리하는 것이다.

작업 항목은 다음과 같다.

```text
저장 시점 날짜 계산
회의 제목 기반 basename 생성
파일명 안전화
영상 파일 복사
md/transcript 파일 복사
파일명 충돌 처리
```

완료 기준은 다음과 같다.

```text
날짜 폴더 아래 회의별 폴더에서 영상과 회의록, 전사 파일을 함께 확인할 수 있음
```

### Phase 5. Watcher 자동화

목표는 `~/remind`에 파일이 들어오면 자동 처리하는 것이다.

작업 항목은 다음과 같다.

```text
폴더 감시
파일 생성 완료 감지
job_id 생성
status.json 기록
중복 처리 방지
실패 로그 저장
```

완료 기준은 다음과 같다.

```text
새 OBS 녹화 파일이 들어오면 자동으로 output 날짜 폴더까지 생성됨
```

## 19. 최종 요약

현재 설계는 다음 한 줄로 정리된다.

```text
~/remind에 OBS 영상을 넣으면,
로컬에서 전사와 회의록 생성이 끝난 뒤,
~/minutes/output/YYYY-MM-DD에 영상과 회의록이 함께 정리된다.
```

최종 결과물 예시는 다음과 같다.

```text
~/minutes/output/2026-06-17/
  2026-06-17_신규-회의록-서비스-MVP-검토.mp4
  2026-06-17_신규-회의록-서비스-MVP-검토.md
  2026-06-17_신규-회의록-서비스-MVP-검토.transcript.txt
  2026-06-17_신규-회의록-서비스-MVP-검토.transcript.json
  2026-06-17_신규-회의록-서비스-MVP-검토.transcript.srt
  2026-06-17_신규-회의록-서비스-MVP-검토.screen_text.txt
  2026-06-17_신규-회의록-서비스-MVP-검토.screen_text.json
```
