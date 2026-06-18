# Meeting Minutes

회의, 강의, 웨비나 등 녹화 영상 파일을 Mac에서 로컬 전사하고, LLM API로 한국어 회의록 Markdown과 DOCX를 생성하는 도구다.

기본 입력 위치는 `~/remind`다. 작업 파일과 결과물 기본 저장 위치는 `~/minutes`다. repo를 어디에 clone해도 사용자 홈 디렉터리 기준으로 바로 실행할 수 있다. 수동 실행할 때는 `~/remind` 밖에 있는 영상도 경로만 지정하면 처리할 수 있다.

처음 설치하는 사용자는 [INSTALL_USAGE.md](INSTALL_USAGE.md)를 따라가면 된다.

## Codex Skill로 실행

Codex를 함께 쓸 때는 이 방식을 권장한다. 아래 빠른 시작으로 repo를 clone하고 패키지를 설치한 뒤, repo root에서 포함된 skill 파일을 Codex skill 폴더로 복사한다.

```bash
mkdir -p ~/.codex/skills/minutes
cp codex/skills/minutes/SKILL.md ~/.codex/skills/minutes/SKILL.md
```

이후 Codex에서 `$minutes`로 영상 경로를 지정한다.

```text
$minutes Codex 모드로 "/Users/jun/remind/2026-06-18 회의.mov" 내용 정리해줘
$minutes "/Users/jun/Desktop/customer-call.mov" 회의록 만들어줘
$minutes "/Users/jun/remind/demo.mp4" CPU와 소요시간도 측정해줘
```

`Codex 모드`는 전사와 OCR까지 로컬 스크립트로 처리한 뒤, 생성된 `codex_minutes_input.md`를 Codex가 읽어 한국어 `minutes.md`를 작성하고 최종 output 폴더로 정리하는 방식이다.

## 빠른 시작

```bash
git clone https://github.com/nugaing119/minutes.git
cd minutes

brew install ffmpeg
brew install tesseract

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.env .env
python scripts/init_dirs.py
```

OpenAI API를 쓰는 경우 `.env`에 `OPENAI_API_KEY`와 `OPENAI_MODEL`을 넣거나, 기존 shell 환경변수에 같은 값을 설정한다.

```bash
python scripts/process_file.py "~/remind/회의녹화.mov"
```

Skill 없이 Codex 모드를 수동으로 실행할 수도 있다. 이 경우 전사와 OCR이 끝난 뒤 `codex_minutes_input.md`가 생성되며, Codex가 그 파일을 읽어 `minutes.md`를 작성한 다음 archive 명령을 실행한다.

```bash
LLM_PROVIDER=codex python scripts/process_file.py "~/remind/회의녹화.mov"
```

```bash
python scripts/archive_job.py ~/minutes/jobs/<job_id> --title "회의 제목"
```

## 설치

```bash
brew install ffmpeg
brew install tesseract

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp config.example.env .env
```

OCI GenAI provider를 사용할 때만 OCI SDK를 추가로 설치한다.

```bash
pip install -r requirements-oci.txt
```

## 설정

기본값은 입력 폴더 `~/remind`, 작업/결과 폴더 `~/minutes`다.

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
```

`LANGUAGE=auto`는 STT 입력 언어 자동 감지를 의미한다. 영어 회의는 영어로 먼저 전사한 뒤 회의록 생성 단계에서 한국어로 정리된다. 한국어 회의는 한국어 전사를 기반으로 한국어 회의록이 생성된다. `WHISPER_DEVICE=gpu`는 MLX 전사 단계에서 Apple Silicon GPU/Metal 경로를 명시적으로 사용한다.

전사 단계는 `ffmpeg`로 오디오를 추출한 뒤 `mlx-whisper`로 처리한다. `AUDIO_FFMPEG_THREADS=1`은 오디오 추출의 순간 CPU 사용량을 낮춘다. `mlx-whisper` 자체는 Apple Silicon의 MLX를 사용하므로 OCR처럼 Tesseract 스레드를 제한하는 방식과는 다르다. 전사 부하가 크면 `WHISPER_MODEL=mlx-community/whisper-medium`처럼 더 작은 모델로 낮추거나, 전체 실행을 `nice -n 10 python scripts/process_file.py ...` 형태로 낮은 우선순위로 실행한다.

OpenAI API를 사용할 때는 `.env`에 다음 값을 설정한다.

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=<openai-api-key>
OPENAI_MODEL=사용 가능한 모델명
```

키를 `.env`에 저장하고 싶지 않으면 macOS Keychain에 저장할 수 있다.

```bash
python scripts/setup_auth.py
```

이후 실행 시 환경변수나 `.env`에 값이 없으면 Keychain에서 `OPENAI_API_KEY`와 `OPENAI_MODEL`을 읽는다.

OCI GenAI API를 사용할 때는 다음 값을 설정한다.

```env
LLM_PROVIDER=oci
OCI_GENAI_MODEL=...
OCI_GENAI_COMPARTMENT_ID=...
OCI_GENAI_ENDPOINT=...
OCI_CONFIG_FILE=~/.oci/config
OCI_PROFILE=DEFAULT
```

OCI provider는 선택 기능이다. 사용 전에 `pip install -r requirements-oci.txt`로 OCI SDK를 설치한다.

회의록은 한국어로 작성된다. 영어는 제품명, API 이름, 명령어처럼 필요한 경우에만 유지한다.

## 보안과 외부 전송

기본 처리 파일은 로컬에 저장된다. 원본 영상, 추출 음성, 전사 JSON/TXT/SRT, OCR 이미지, snapshot, 최종 회의록은 기본적으로 `~/minutes` 아래에 생성된다.

외부 전송이 발생하는 지점은 다음으로 한정한다.

- `mlx-whisper` 모델은 최초 실행 시 Hugging Face 캐시로 모델 파일을 다운로드할 수 있다. 회의 영상이나 음성을 업로드하지는 않는다.
- `LLM_PROVIDER=openai`이면 회의록 생성을 위해 전사 텍스트와 OCR 텍스트가 OpenAI API로 전송된다.
- `LLM_PROVIDER=oci`이면 전사 텍스트와 OCR 텍스트가 설정한 OCI GenAI endpoint로 전송된다.
- `LLM_PROVIDER=codex`이면 스크립트는 `codex_minutes_input.md`를 로컬 job 폴더에 만들고 멈춘다.

GitHub에 올릴 때는 `.env`, `.venv/`, `.omx/`, job/output 산출물, 영상/음성/자막/DOCX 파일이 `.gitignore`에 의해 제외된다. API 키는 repo에 저장하지 말고 환경변수, `.env`, 또는 macOS Keychain을 사용한다.

모델 라이선스는 사용하는 Hugging Face repo의 모델 카드 기준으로 확인한다. 기본값인 `mlx-community/whisper-large-v3-turbo`는 MLX 변환 모델이며, upstream `openai/whisper-large-v3-turbo`의 라이선스와 모델 카드 정보를 함께 확인하는 것이 좋다.

OCI SDK는 선택 설치로 분리되어 있다. 기본 설치 경로에는 포함되지 않으며, OCI provider를 실제로 사용할 때 별도로 설치하고 검토한다.

API 인증 없이 Codex가 회의록을 직접 작성하게 할 때는 실행 시 provider를 `codex`로 지정한다. 위의 `$minutes` skill 사용을 권장하며, 수동으로 실행할 수도 있다.

```bash
LLM_PROVIDER=codex python scripts/process_file.py ~/remind/meeting_recording.mp4
```

이 모드는 전사/OCR을 완료한 뒤 `codex_minutes_input.md`를 만들고 멈춘다. Codex가 그 파일을 읽어 `minutes.md`를 작성한 뒤 아래 명령으로 최종 output 폴더에 정리한다.

```bash
python scripts/archive_job.py ~/minutes/jobs/<job_id> --title "회의 제목"
```

OCR은 기본적으로 10초마다 프레임 1장을 추출해 로컬 `tesseract`로 처리한다. `tesseract`가 설치되어 있지 않으면 OCR만 건너뛰고 전사와 회의록 생성은 계속 진행한다. 한국어 OCR 품질은 로컬 Tesseract의 한국어 언어 데이터 설치 상태에 영향을 받는다.

OCR snapshot은 OCR 텍스트가 있고 직전 OCR 결과와 중복되지 않은 프레임만 이미지로 저장한다. 또한 `OCR_VISUAL_DEDUPE_ENABLED=true`이면 OCR 전에 화면 유사도를 먼저 비교해, 같은 화면에서 자막만 바뀐 프레임은 OCR과 snapshot 저장을 건너뛴다. 기본값은 하단 18%와 우측 20%를 자막/회의 UI 영역으로 보고 비교에서 제외한다. 작업 중에는 `~/minutes/jobs/<job_id>/snapshots/`에 저장되고, 최종 정리 시 회의록과 같은 영상별 output 폴더의 `snapshots/`로 복사된다. `CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=true`이면 복사 후 job 내부의 `frames/`와 `snapshots/`는 삭제해 이미지 파일이 계속 쌓이지 않게 한다.

CPU peak를 낮춰야 하면 `.env`에서 단계별 외부 프로세스 제한을 둔다. 기본값은 짧지만 peak가 큰 오디오 추출 `ffmpeg`와 OCR 프레임 추출 `ffmpeg`에만 제한을 걸고, 전사와 Tesseract OCR은 처리량을 유지한다. `AUDIO_CPU_LIMIT_PERCENT=60`은 오디오 추출에만 적용된다. `OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80`은 OCR용 프레임 추출에만 적용된다. `mlx-whisper`는 같은 Python 프로세스 안에서 실행되므로 이 제한의 직접 대상은 아니며, 부하가 높으면 모델을 낮춰야 한다.

```env
AUDIO_CPU_LIMIT_PERCENT=60
AUDIO_CPU_LIMIT_PERIOD_SECONDS=0.2
AUDIO_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80
OCR_FRAME_EXTRACT_CPU_LIMIT_FALLBACK_BURST_CORES=1.5
OCR_SIGNATURE_CPU_LIMIT_PERCENT=0
OCR_TESSERACT_CPU_LIMIT_PERCENT=0
OCR_FRAME_INTERVAL_SECONDS=20
OCR_FFMPEG_THREADS=1
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=15
OCR_FRAME_PAUSE_SECONDS=0.2
```

`OCR_TESSERACT_THREAD_LIMIT=1`은 Tesseract의 OpenMP 스레드 사용을 제한한다. `OCR_TESSERACT_NICE`는 OCR 프로세스 우선순위를 낮춘다. `OCR_FRAME_PAUSE_SECONDS`는 프레임 OCR 사이에 대기 시간을 넣어 처리 속도 대신 순간 CPU 부하를 낮춘다.

## 기본 폴더 생성

```bash
python scripts/init_dirs.py
```

생성되는 기본 구조다.

```text
~/remind/

~/minutes/
  jobs/
  output/
```

## 파일 하나 처리

녹화본이 여러 개 있으면 처리할 파일 경로를 직접 지정한다. 수동 실행은 `~/remind`에 묶이지 않으며, `.mp4`, `.mkv`, `.mov` 파일이면 다른 폴더의 절대 경로나 상대 경로도 사용할 수 있다.

```bash
python scripts/process_file.py ~/remind/meeting_recording.mp4
```

예를 들어 `~/remind` 안의 파일 목록을 보고 원하는 파일만 지정할 수 있다.

```bash
ls -lh ~/remind
python scripts/process_file.py "~/remind/2026-06-17 회의 녹화.mp4"
```

다른 위치의 파일도 같은 방식으로 처리한다.

```bash
python scripts/process_file.py "/Users/jun/Desktop/2026-06-18 고객 미팅.mov"
```

결과는 저장 시점 날짜 폴더에 생성된다.

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
      snapshot_0001_00-00-00.jpg
      snapshot_0002_00-00-10.jpg
```

## 자동 감시 실행

```bash
python scripts/watch_recordings.py
```

자동 처리할 영상 파일이 다음 폴더에 저장되도록 지정한다.

```text
~/remind
```

watcher는 `.mp4`, `.mkv`, `.mov` 파일을 감지한다. 파일 크기와 수정 시간이 일정 시간 변하지 않을 때 저장 완료로 판단하고 처리한다.

## 처리 상태

각 작업은 `~/minutes/jobs/<job_id>/status.json`에 상태를 남긴다.

작업 중간 파일은 같은 job 폴더에 저장된다.

```text
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
```

이미 완료된 파일은 fingerprint 기준으로 재처리하지 않는다.
