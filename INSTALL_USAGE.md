# minutes 설치 및 사용 가이드

`minutes`는 회의, 강의, 웨비나 등 영상 또는 녹음 파일을 Mac에서 로컬로 전사하고, OCR 텍스트와 함께 LLM으로 한국어 회의록을 생성하는 도구다.

기본 입력 폴더는 `~/remind`, 기본 작업/결과 폴더는 `~/minutes`다. repository를 어디에 clone해도 이 홈 디렉터리 기준 경로를 사용하므로 다른 사용자도 별도 경로 수정 없이 시작할 수 있다.

## 1. 요구 사항

- Apple Silicon Mac 권장
- Homebrew
- Python 3.11 이상 권장
- Codex, OpenAI API, OCI GenAI API 중 하나

Codex Skill 방식으로 실행하려면 Codex가 설치되어 있고 로그인되어 있어야 한다. OpenAI API나 OCI GenAI API를 직접 provider로 사용할 경우에는 해당 API 인증 정보가 필요하다.

## 2. 설치

```bash
git clone https://github.com/nugaing119/minutes.git
cd minutes
```

필수 로컬 도구를 설치한다.

```bash
brew install ffmpeg
brew install tesseract
```

Python 가상환경을 만들고 패키지를 설치한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

OCI GenAI를 사용할 때만 OCI SDK를 추가로 설치한다.

```bash
pip install -r requirements-oci.txt
```

## 3. 설정

예시 설정 파일을 복사한다.

```bash
cp config.example.env .env
```

OpenAI를 사용할 경우 `.env`에 아래 값을 설정한다. 이미 PC shell 환경변수에 같은 값이 설정되어 있으면 `.env`에 다시 넣지 않아도 된다.

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=<openai-api-key>
OPENAI_MODEL=사용 가능한 모델명
```

OCI GenAI를 사용할 경우 `.env`에 OCI provider 값을 설정한다.

```env
LLM_PROVIDER=oci
OCI_GENAI_MODEL=...
OCI_GENAI_COMPARTMENT_ID=...
OCI_GENAI_ENDPOINT=...
OCI_CONFIG_FILE=~/.oci/config
OCI_PROFILE=DEFAULT
```

API 키를 파일에 저장하고 싶지 않으면 macOS Keychain을 사용할 수 있다.

```bash
python scripts/setup_auth.py
```

## 4. 기본 폴더 생성

```bash
python scripts/init_dirs.py
```

생성되는 기본 구조는 다음과 같다.

```text
~/remind/

~/minutes/
  jobs/
  output/
```

자동 처리할 파일 저장 폴더는 `~/remind`로 맞추면 감시 기능과 잘 맞는다. 수동 실행은 다른 폴더의 영상이나 녹음 파일도 처리할 수 있다.

## 5. Codex Skill로 실행

Codex를 함께 사용할 때는 repo에 포함된 skill 파일을 Codex skill 폴더로 복사한다.

```bash
mkdir -p ~/.codex/skills/minutes
cp codex/skills/minutes/SKILL.md ~/.codex/skills/minutes/SKILL.md
```

이후 Codex에서 `$minutes`로 영상 또는 녹음 파일 경로를 지정한다.

```text
$minutes Codex 모드로 "/Users/jun/remind/2026-06-18 회의.mov" 내용 정리해줘
$minutes Codex 모드로 "/Users/jun/remind/2026-06-18 회의.m4a" 내용 정리해줘
$minutes "/Users/jun/Desktop/customer-call.mov" 회의록 만들어줘
$minutes "/Users/jun/remind/demo.mp4" CPU와 소요시간도 측정해줘
```

`Codex 모드`는 전사와 OCR까지 로컬 스크립트로 처리한 뒤, 생성된 `codex_minutes_input.md`를 Codex가 읽어 한국어 `minutes.md`를 작성하고 최종 output 폴더로 정리하는 방식이다.

## 6. 파일 하나 처리

가상환경이 활성화되어 있는지 확인한다.

```bash
source .venv/bin/activate
```

처리할 영상 또는 녹음 파일 경로를 직접 지정한다.

```bash
python scripts/process_file.py "~/remind/2026-06-18 회의.mov"
python scripts/process_file.py "~/remind/2026-06-18 회의.m4a"
```

공백이 있는 경로는 전체를 따옴표로 감싼다.

```bash
python scripts/process_file.py "/Users/jun/Desktop/고객 미팅.mov"
```

회의록은 항상 한국어로 생성된다. 영어 회의는 전사 내용을 기반으로 한국어 회의록으로 정리되고, 한국어 회의는 한국어 그대로 정리된다. 제품명, API 이름, 명령어처럼 필요한 영어만 유지한다.

## 7. Codex 모드 수동 실행

Skill을 쓰지 않고 Codex 모드를 수동으로 실행할 수도 있다.

```bash
LLM_PROVIDER=codex python scripts/process_file.py "~/remind/회의.mov"
```

이 모드는 전사와 OCR을 끝낸 뒤 `~/minutes/jobs/<job_id>/codex_minutes_input.md`를 만들고 멈춘다. Codex가 해당 파일을 읽어 같은 job 폴더에 `minutes.md`를 작성한 뒤 다음 명령으로 최종 output 폴더에 정리한다.

```bash
python scripts/archive_job.py ~/minutes/jobs/<job_id> --title "회의 제목"
```

## 8. 자동 감시 실행

처리할 영상 또는 녹음 파일이 `~/remind`에 저장되도록 설정한 뒤 watcher를 실행한다.

```bash
python scripts/watch_recordings.py
```

watcher는 `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`, `.ogg` 파일을 감지한다. 파일 크기와 수정 시간이 일정 시간 변하지 않으면 저장이 끝난 것으로 보고 처리한다.

## 9. 결과물 위치

최종 결과는 회의록 저장 시점 날짜 기준으로 정리된다.

```text
~/minutes/output/YYYY-MM-DD/
  회의-주제/
    YYYY-MM-DD_회의-주제.mov 또는 YYYY-MM-DD_회의-주제.m4a
    YYYY-MM-DD_회의-주제.md
    YYYY-MM-DD_회의-주제.docx
    YYYY-MM-DD_회의-주제.transcript.txt
    YYYY-MM-DD_회의-주제.transcript.json
    YYYY-MM-DD_회의-주제.transcript.srt
    YYYY-MM-DD_회의-주제.screen_text.txt
    YYYY-MM-DD_회의-주제.screen_text.json
    snapshots/
      snapshot_0001_00-00-00.jpg
```

중간 작업 파일은 `~/minutes/jobs/<job_id>/`에 남는다.

## 10. CPU 부하 조정

기본 설정은 오디오 추출과 OCR 프레임 추출의 순간 CPU peak를 낮추도록 되어 있다.

```env
AUDIO_CPU_LIMIT_PERCENT=60
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80
OCR_FFMPEG_THREADS=1
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=10
```

팬 소음이나 순간 부하가 크면 `.env`에서 OCR 간격을 늘리거나 대기 시간을 추가한다.

```env
OCR_FRAME_INTERVAL_SECONDS=20
OCR_FRAME_PAUSE_SECONDS=0.2
OCR_TESSERACT_NICE=15
```

전사 부하가 크면 `WHISPER_MODEL=mlx-community/whisper-medium`처럼 더 작은 모델로 낮춘다.

## 10. 보안과 외부 전송

원본 영상/녹음, 추출 음성, 전사 파일, OCR 이미지, snapshot, 최종 회의록은 기본적으로 로컬 `~/minutes` 아래에 저장된다.

외부 전송은 선택한 LLM provider에 회의록 생성을 요청할 때 발생한다. `LLM_PROVIDER=openai`이면 전사/OCR 텍스트가 OpenAI API로 전송되고, `LLM_PROVIDER=oci`이면 설정한 OCI GenAI endpoint로 전송된다.

`.env`, `.venv/`, `.omx/`, job/output 산출물, 영상/녹음/음성/자막/DOCX 파일은 `.gitignore`로 제외된다. API 키는 GitHub에 올리지 말고 환경변수, `.env`, 또는 macOS Keychain을 사용한다.
