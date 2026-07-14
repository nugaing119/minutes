# minutes 설치 및 사용 가이드

`minutes`는 영상 또는 녹음 파일을 Mac에서 원문 언어로 전사하고, 시간별 STT·OCR·선별 Snapshot 근거와 함께 설정한 최종 언어의 문서를 생성하는 도구다. 콘텐츠를 강의, 기술 발표, 인터뷰, 토론, 업무 협의, 데모 등으로 분석해 제목과 섹션을 정하며 `회의록` 구조를 고정하지 않는다.

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
brew install tesseract-lang
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

자동 처리에는 별도 화자분리 의존성이 필요하지 않다. 오디오는 MLX Whisper STT에
사용하고 Silero ONNX는 발화 존재만 검증한다. ECAPA 음성 군집화는 실행하지 않는다.

```bash
python scripts/prepare_vad_model.py
python scripts/prepare_vad_model.py --status
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

화자 판단은 기본 `evidence` 모드를 사용한다.

```env
SPEAKER_ATTRIBUTION_MODE=evidence
SPEAKER_ATTRIBUTION_REQUIRED=false
SPEECH_ACTIVITY_VALIDATION_ENABLED=true
```

이 모드는 Whisper 구간 타임스탬프 STT, 시간별 OCR, 필요한 소수 Snapshot만 최종
LLM에 제공한다. 화면 근거가 없거나 약해도 로컬 음성 화자분리, 강제 화자 수 지정,
음성 재분석을 시작하지 않는다. 화면 근거가 없으면 자기소개·직접 호명과 응답·발언
인계 같은 명시적 STT 근거만 사용하고, 부족하면 `화자 미상`으로 남긴다. 참가자 목록,
화면 공유자, 특정 서비스·색상·테두리·화면 배치를 발언자 근거로 단정하지 않는다.
`audio`, `hybrid`, `SPEAKER_ATTRIBUTION_REQUIRED=true`는 자동 처리에서 거부된다.

오디오 추출과 STT는 이 정책과 무관하게 항상 유지된다. Silero ONNX는 무음 구간의
전사 의심 여부만 기록하며 화자 분리·이름 추정·전사 자동 삭제를 하지 않는다.

전체 해시와 보안 운영 절차는 `SECURITY.md`를 따른다.

## 4. 기본 폴더 생성

```bash
python scripts/init_dirs.py
```

생성되는 기본 구조는 다음과 같다.

```text
~/remind/

~/minutes/
  jobs/
  models/
  output/
```

자동 처리할 파일 저장 폴더는 `~/remind`로 맞추면 감시 기능과 잘 맞는다. 수동 실행은 다른 폴더의 영상이나 녹음 파일도 처리할 수 있다.

## 5. Codex Skill로 실행

Codex를 함께 사용할 때는 repo에 포함된 skill 파일을 Codex skill 폴더로 복사한다.

```bash
mkdir -p ~/.codex/skills/minutes
cp -R codex/skills/minutes/. ~/.codex/skills/minutes/
python3 scripts/run_codex.py
```

설치되는 skill은 `allow_implicit_invocation: false`를 사용한다. 사용자 전역 skill 폴더에
있어도 다른 프로젝트 세션에 자동 주입되지 않으며 `$minutes`를 명시했을 때만 활성화된다.

`run_codex.py`는 현재 shell 환경변수, `.env`, 기본값 순서로 `MINUTES_HOME`과 `RECORDINGS_INBOX`를 해석하고 두 경로를 Codex `--add-dir`에 전달한다. 경로를 변경할 때는 저장소 파일을 수정하지 않고 각 사용자의 `.env`만 바꾸면 된다. 실행된 Codex에서 `$minutes`로 영상 또는 녹음 파일 경로를 지정한다.

```text
$minutes Codex 모드로 "~/remind/2026-06-18 회의.mov" 내용 정리해줘
$minutes Codex 모드로 "~/remind/2026-06-18 회의.m4a" 내용 정리해줘
$minutes "~/Desktop/customer-call.mov" 내용에 맞는 문서로 정리해줘
$minutes "~/remind/demo.mp4" CPU와 소요시간도 측정해줘
```

`Codex 모드`는 오디오 추출·전사·OCR을 로컬 처리한 뒤, 생성된 `codex_minutes_input.md`와 선별 Snapshot을 Codex가 읽어 근거 기반 화자 판단과 실제 내용에 맞는 제목·섹션을 정하고 목표 언어의 `minutes.md`를 작성한 다음 최종 output 폴더로 정리하는 방식이다.

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
python scripts/process_file.py "~/Desktop/고객 미팅.mov"
```

`OUTPUT_LANGUAGE=auto`이면 영어 영상은 영어, 한국어 영상은 한국어로 최종 작성한다. 한국어 변환이 필요할 때만 `OUTPUT_LANGUAGE=ko`, 영어를 강제하려면 `OUTPUT_LANGUAGE=en`을 지정한다. 영어와 한국어 모두 오디오 추출·STT 1회와 영상 OCR 1회로 처리하며 로컬 음성 화자분리는 실행하지 않는다. 언어별로 달라지는 것은 STT·OCR 언어와 최종 출력 언어뿐이다. STT와 OCR은 원문 언어를 유지하고, 긴 영상의 부분 분석도 원문 언어로 만든 뒤 마지막 전체 병합 단계에서만 목표 언어를 적용한다.

Whisper 모델 캐시는 영상마다 새로 생기지 않는다. 같은 모델 파일은 Hugging Face `blobs/`에 한 번 저장되고 리비전 Snapshot이 공유한다. `HF_HUB_OFFLINE=1`인 실제 처리에서는 기존 캐시만 사용하므로 캐시가 늘지 않는다. 새 PC에서는 최초 한 번 모델을 준비해야 하며, 모델이나 실제 가중치 리비전을 바꾼 경우에만 추가 blob이 생길 수 있다.

내용을 억지로 줄이지 않고 원문 대비 감사를 필수화하려면 Codex 모드에 다음 설정을 사용한다.

```env
LLM_PROVIDER=codex
CONTENT_AUDIT_MODE=strict
OFFICIAL_SOURCE_VERIFICATION=auto
```

최종 문서에는 글자·token·페이지·bullet·section 수의 상한을 두지 않는다. `strict` 모드는 `content_inventory.json`에서 정책·수치·조건·예외·질의응답·근거 충돌을 먼저 목록화하고, `content_audit.json`에서 완성본과 다시 대조한다. 필수 항목 누락, 의미 강도 변경, 공개되지 않은 근거 충돌이 있으면 보관을 중단한다.

원문이 한 context보다 길면 시간 구간별로 순차 처리해 같은 inventory에 누적한다. 구간별 서술 요약을 다시 종합하는 손실형 처리는 사용하지 않으며, context 한계 때문에 최종 문서 길이나 필수 내용을 줄이지 않는다.

공식 문서 확인은 먼저 음성 문맥·시간별 STT·OCR·Snapshot을 교차 확인한 뒤에도 표현·고유명사·버전 또는 의미가 모호하거나 충돌할 때만 수행한다. 공식 근거는 전사를 보강할 수 있지만 명확한 영상 발언을 바꾸지 않는다. 검색어에는 공개 제품명·버전·일반화한 주장만 사용하고 STT/OCR 원문이나 개인·내부 식별 정보는 보내지 않는다.

공식 문서를 사용했다면 최종 문서 맨 아래 `외부 근거 확인`에서 `전사·OCR 보강 근거`와 `영상 내용과 상충하는 근거`를 구분하고 timestamp, 조사 목적, 결과, 확인일과 링크를 남긴다. 상충하더라도 영상 본문은 그대로 유지한다.

## 7. Codex 모드 수동 실행

Skill을 쓰지 않고 Codex 모드를 수동으로 실행할 수도 있다.

```bash
LLM_PROVIDER=codex python scripts/process_file.py "~/remind/회의.mov"
```

이 모드는 전사와 OCR을 끝낸 뒤 `~/minutes/jobs/<job_id>/codex_minutes_input.md`를 만들고 멈춘다. Codex가 해당 파일을 읽어 같은 job 폴더에 `minutes.md`를 작성한 뒤 다음 명령으로 최종 output 폴더에 정리한다.

```bash
python scripts/archive_job.py "<job-directory>"
```

## 8. 자동 감시 실행

처리할 영상 또는 녹음 파일이 `~/remind`에 저장되도록 설정한 뒤 watcher를 실행한다.

```bash
python scripts/watch_recordings.py
```

watcher는 `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`, `.ogg` 파일을 감지한다. 파일 크기와 수정 시간이 일정 시간 변하지 않으면 저장이 끝난 것으로 보고 처리한다.

## 9. 결과물 위치

최종 결과는 원본 파일명에 포함된 촬영 날짜 기준으로 정리된다. 날짜가 없으면 원본 수정 날짜를 사용하고, 폴더명과 파일명은 완성 문서의 H1 제목에서 만든다.

```text
~/minutes/output/YYYY-MM-DD/
  내용-기반-제목/
    YYYY-MM-DD_내용-기반-제목.mov 또는 YYYY-MM-DD_내용-기반-제목.m4a
    YYYY-MM-DD_내용-기반-제목.md
    YYYY-MM-DD_내용-기반-제목.docx
    snapshots/
      snapshot_0001_00-00-00.jpg
```

최종 폴더에는 전달용 미디어, Markdown, DOCX, 의미 있는 snapshot만 둔다. transcript, OCR, `speaker_attribution_report.json`, 로그와 상태 파일은 재작업을 위해 `~/minutes/jobs/<job_id>/`에 24시간 남긴다. `~/remind` 바로 아래에서 처리한 입력 미디어는 성공 시 최종 output으로 이동해 원래 위치에 중복본을 남기지 않는다. 다른 위치에서 지정한 입력은 원본을 보존한다. 실패 작업과 Codex 입력 대기 작업은 기간과 관계없이 job에 보존된다.

`COMPLETED_JOB_RETENTION_HOURS=24`가 기본값이다. 기간이 지난 완료 job은 다음 처리나 아카이브 시 최종 미디어·Markdown과 기록된 DOCX·Snapshot을 검증한 뒤 자동 삭제된다. `jobs/index.json`과 `.process.lock`은 유지된다. 수동 명령은 dry-run 결과를 보여주며, `--apply`를 명시한 경우에만 만료된 job 폴더 전체를 삭제한다.

```bash
python scripts/cleanup_completed_jobs.py
python scripts/cleanup_completed_jobs.py --apply
```

## 10. CPU 부하 조정

기본 설정은 전체 처리 우선순위를 Utility로 낮추고, 동시에 한 작업만 실행하며,
ffmpeg/Tesseract 스레드와 duty cycle을 제한한다. CPU 백분율 설정은 외부 프로세스의
평균 부하를 낮추는 근사값이며 정밀한 hard cap은 아니다. 자동 처리의 Silero 검증은
ONNX Runtime 단일 스레드이며 ECAPA 단계는 없다.

```env
PROCESS_QOS=utility
PROCESS_NICE=10
AUDIO_CPU_LIMIT_PERCENT=60
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=80
OCR_FFMPEG_THREADS=1
OCR_WORKERS=5
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=10
```

`OCR_WORKERS`는 서로 다른 프레임을 병렬 처리하는 프로세스 수이고,
`OCR_TESSERACT_THREAD_LIMIT`은 각 프로세스 내부의 OpenMP 스레드 수다. 현재 11-core
M3 Pro 로컬 설정은 `OCR_WORKERS=5`, 내부 스레드 1을 사용한다. 이 값은 해당 장비에서
수행한 비교 실험 결과를 `.env`에 명시한 것이며 스킬이나 런타임이 CPU 사용률을 보고
worker 수를 자동 조정하지 않는다. 다른 Mac은 공개 기본값 1에서 별도로 측정해 명시값을
정한다. 병렬 완료 순서와 무관하게 OCR 결과와 Snapshot은 원래 타임스탬프 순서로 저장된다.

팬 소음이나 순간 부하가 크면 `.env`에서 OCR 간격을 늘리거나 대기 시간을 추가한다.

```env
OCR_FRAME_INTERVAL_SECONDS=20
OCR_FRAME_PAUSE_SECONDS=0.2
OCR_TESSERACT_NICE=15
```

MLX 전사는 GPU/Metal을 사용하므로 CPU 백분율 제한 대상이 아니다. 전사 부하가 크면 `WHISPER_MODEL=mlx-community/whisper-medium`처럼 더 작은 모델로 낮춘다. `PROCESS_QOS=background`나 `maintenance`는 더 보수적이지만 전체 처리 시간이 크게 늘 수 있다.

`process_metrics.json`에서 STT·OCR 단계별 wall time과 CPU time을 확인한다. 자동 작업의
단계 목록에 `diarize` 또는 `attribute_speakers`가 있으면 evidence-only 정책 위반이다.
반대로 `extract_audio`, `load_audio`, `transcribe`, `validate_speech_activity`는 필수 단계다.
evidence-only는 오디오 전사를 끄는 설정이 아니라 별도 음향 화자분리를 끄는 설정이다.
전사에 사용한 `audio.wav`는 재생성 가능한 임시파일이므로 전사 성공 뒤 삭제한다.

## 10. 보안과 외부 전송

원본 영상/녹음, 추출 음성, 전사 파일, OCR 이미지, snapshot, 최종 문서는 기본적으로 로컬 `~/minutes` 아래에 저장된다.

외부 전송은 선택한 LLM provider에 최종 문서 생성을 요청할 때 발생한다. `LLM_PROVIDER=openai`이면 전사/OCR 텍스트가 OpenAI API로 전송되고, `LLM_PROVIDER=oci`이면 설정한 OCI GenAI endpoint로 전송된다.

`.env`, `.venv/`, `.omx/`, job/output 산출물, 영상/녹음/음성/자막/DOCX 파일은 `.gitignore`로 제외된다. API 키는 GitHub에 올리지 말고 환경변수, `.env`, 또는 macOS Keychain을 사용한다.
