# minutes — 영상 내용 문서화

영상 또는 녹음 파일을 Mac에서 원문 언어로 로컬 전사하고, 실제 내용을 분석해 설정한 최종 언어의 Markdown과 DOCX를 생성하는 도구다. 강의, 기술 발표, 인터뷰, 토론, 업무 협의, 데모 등 콘텐츠 유형에 맞춰 제목과 섹션을 구성하며 `회의록` 형식을 고정하지 않는다.

기본 입력 위치는 `~/remind`다. 작업 파일과 결과물 기본 저장 위치는 `~/minutes`다. repo를 어디에 clone해도 사용자 홈 디렉터리 기준으로 바로 실행할 수 있다. 수동 실행할 때는 `~/remind` 밖에 있는 영상이나 녹음 파일도 경로만 지정하면 처리할 수 있다.

처음 설치하는 사용자는 [INSTALL_USAGE.md](INSTALL_USAGE.md)를 따라가면 된다.

## Codex Skill로 실행

Codex를 함께 쓸 때는 이 방식을 권장한다. 아래 빠른 시작으로 repo를 clone하고 패키지를 설치한 뒤, repo root에서 포함된 skill 파일을 Codex skill 폴더로 복사한다.

```bash
mkdir -p ~/.codex/skills/minutes
cp -R codex/skills/minutes/. ~/.codex/skills/minutes/
python3 scripts/run_codex.py
```

설치되는 `agents/openai.yaml`은 `allow_implicit_invocation: false`로 설정되어 있다. 따라서
사용자 전역 skill 폴더에 설치해도 다른 프로젝트 세션의 모델 문맥에 자동 주입되지 않으며,
항상 `$minutes`를 명시한 요청에서만 활성화된다.

`$minutes`는 로컬 ffmpeg·MLX Whisper·OCR 전처리와 근거·내용 감사를 담당한다. 최종 Word는
`finalize_docx.py`가 Codex에 번들된 Documents renderer를 호출하고 delivery 세션이 모든 페이지를
검증한다. 매 작업마다 39KB짜리 Documents `SKILL.md`를 다시 읽지는 않는다. 이 저장소에서는
`minutes` skill만 복사하며 Documents renderer가 없는 Codex 설치는 먼저 업데이트해야 한다.

`run_codex.py`는 현재 shell 환경변수, `.env`, 기본값 순서로 `MINUTES_HOME`과 `RECORDINGS_INBOX`를 해석하고 두 경로를 Codex `--add-dir`에 전달한다. 사용자 홈 경로를 저장소에 하드코딩하지 않으며, 폴더를 옮기면 `.env` 값만 변경하면 된다. 실행된 Codex에서 `$minutes`로 영상 또는 녹음 파일 경로를 지정한다.

```text
$minutes Codex 모드로 "~/remind/2026-06-18 회의.mov" 내용 정리해줘
$minutes Codex 모드로 "~/remind/2026-06-18 회의.m4a" 내용 정리해줘
$minutes "~/Desktop/customer-call.mov" 내용에 맞는 문서로 정리해줘
$minutes "~/remind/demo.mp4" CPU와 소요시간도 측정해줘
```

`Codex 모드`는 오디오 추출·전사·OCR을 로컬 처리한 뒤, 기존 대화와 분리된 content
`codex exec --ephemeral` 세션이 job의 비중첩 근거 청크를 한 번씩 읽어 화자 판단, 콘텐츠
유형, 제목, 섹션을 정하고 원문 언어 `minutes.md`를 감사·동결한다. 목표 언어가 다를 때만
동결 Markdown을 받는 translation 세션을 한 번 추가하고, 마지막 delivery 세션이 `$documents`로
Word를 렌더링해 모든 페이지를 확인한 뒤 최종 output을 정리한다. 각 handoff prompt에는
원시 근거를 복사하지 않는다.

## 빠른 시작

```bash
git clone https://github.com/nugaing119/minutes.git
cd minutes

brew install ffmpeg
brew install tesseract
brew install tesseract-lang

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.env .env
python scripts/init_dirs.py
```

OpenAI API를 쓰는 경우 `.env`에 `OPENAI_API_KEY`와 `OPENAI_MODEL`을 넣거나, 기존 shell 환경변수에 같은 값을 설정한다.

```bash
python scripts/process_file.py "~/remind/회의영상.mov"
python scripts/process_file.py "~/remind/회의녹음.m4a"
```

Skill 없이 Codex 모드를 수동으로 실행할 수도 있다. 전사와 OCR이 끝나
`codex_minutes_input.md`가 생성되면, 현재 대화에서 원문을 읽지 말고 준비된 job을 새
Codex 세션에 넘긴다.

```bash
LLM_PROVIDER=codex python scripts/process_file.py "~/remind/회의녹음.m4a"
```

```bash
./scripts/run_fresh_codex_job.py "<job-directory>"
```

실행 전 계약만 확인하려면 `--dry-run`을 붙인다. 이 출력은 프롬프트·근거 본문을 표시하지 않고
바이트·SHA-256·단계·명령 요약만 8KB 이하로 출력한다.

콘텐츠 세션은 schema-v3의 주관적 품질 항목만 작성한다. 해시·청크 집합·문서 신호는
`content_freeze.py`가 계산한다. delivery 세션은 `finalize_docx.py` 한 명령으로 생성·렌더·구조
QA를 수행하고, 전 페이지 확인 뒤 차단 결함이 있을 때만 기본 1회 수정·재렌더링한다. 짧은
마지막 페이지나 단일 목차 페이지의 여백 같은 비차단 경고만으로는 재작성하지 않는다.

일반 실행은 현재 Codex UI 추론 설정과 무관하게 `high`를 기본으로 사용한다. 비교가 필요할
때만 `--reasoning-effort xhigh` 또는 `max`를 명시한다.
`run_fresh_codex_job.py`는 전체 STT·OCR을 prompt에 축약해 넣지 않는다. 콘텐츠 세션은
`evidence_chunks.json`의 15KB 이하 비중첩 part를 파일별로 순서대로 한 번씩 읽어 전체 입력을 복원하며 원본
`codex_minutes_input.md`를 겹치는 범위로 반복해 읽지 않는다. 원시 `evidence_coverage.json` 대신 bounded
`evidence_coverage_summary.json`을 사용하고, 대형 전처리 JSON 대신
`worker_runtime_summary.json`의 검증 필드만 읽는다. 콘텐츠 검증이 통과하면 Markdown과 감사
산출물을 `content_freeze.json`에 해시로 고정하고, 두 번째 delivery 세션은 STT/OCR 없이 동결된
Markdown·blueprint·Word 렌더만 읽는다. delivery 재시도는 유효한 freeze를 재사용하므로 전체
근거를 다시 읽지 않는다. 정상 미디어 작업에서는 저장소 전체 회귀 테스트나 검증기 소스
탐색을 실행하지 않고 콘텐츠·DOCX·아카이브 게이트만 실행한다.
manifest의 `start_line`/`end_line`은 분할 전 원본 좌표이며 part 내부 범위가 아니다. worker는 각
part 전체를 한 명령으로 읽어야 하며 같은 part 경로가 두 번째 명령에 나타나면 즉시 실패한다.
compact prompt에는 ledger·inventory·blueprint·audit·quality-review의 정확한 enum과 필드를 넣고,
절대 `.venv/bin/python` 경로로 freeze를 한 번 실행한다. content는 50회, delivery는 25회 이하의
도구 왕복을 비용 목표로 기록하되 근거 감사나 전 페이지 QA를 생략하는 품질 상한으로 사용하지 않는다.
`fresh_codex_handoff.json`에는 핵심 입력 파일
해시와 Snapshot/raw-frame 디렉터리의 개수·총 바이트·결합 manifest SHA-256,
`parent_conversation_inherited=false`,
`raw_evidence_embedded_in_handoff=false`, 콘텐츠/delivery 단계별 시간·토큰·도구 호출과
집계 `context_efficiency`를 기록한다. 현재 `.env`의
`CONTENT_AUDIT_MODE=strict`에서는 작성 전 inventory와 작성 후 audit가 통과해야 보관된다.
부모가 macOS Codex seatbelt 안에서 실행 중이면 이 launcher 명령 자체만 처음부터 escalation해
실행해야 한다. launcher가 시작하는 실제 worker는 별도의 `workspace-write` 샌드박스에서 repo와
설정된 `MINUTES_HOME`만 쓸 수 있다.

## 설치

```bash
brew install ffmpeg
brew install tesseract
brew install tesseract-lang

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp config.example.env .env
```

OCI GenAI provider를 사용할 때만 OCI SDK를 추가로 설치한다.

```bash
pip install -r requirements-oci.txt
```

자동 영상 처리에는 별도 화자분리 패키지가 필요하지 않다. 오디오는 MLX Whisper STT에
사용되며, Silero ONNX는 발화 존재 검증만 수행한다. ECAPA 임베딩과 음성 군집화는 없다.

최초 한 번 고정된 Silero ONNX 모델을 준비한다. 준비 단계만 PyPI에서 해시가 고정된
wheel을 받아 ONNX 파일 하나를 추출하며 실제 처리 중에는 네트워크를 사용하지 않는다.

```bash
python scripts/prepare_vad_model.py
python scripts/prepare_vad_model.py --status
```

## 설정

기본값은 입력 폴더 `~/remind`, 작업/결과 폴더 `~/minutes`다.

```env
MINUTES_HOME=~/minutes
RECORDINGS_INBOX=~/remind
WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
HF_HUB_OFFLINE=1
WHISPER_DEVICE=gpu
LANGUAGE=auto
OUTPUT_LANGUAGE=auto
CONTENT_AUDIT_MODE=off
OFFICIAL_SOURCE_VERIFICATION=off
SPEAKER_ATTRIBUTION_MODE=evidence
SPEAKER_ATTRIBUTION_REQUIRED=false
SPEECH_ACTIVITY_VALIDATION_ENABLED=true
COMMUNITY1_APPROVAL_PATH=~/minutes/governance/pyannote-community1-approval.json
COMMUNITY1_MODEL_DIR=~/minutes/models/pyannote-community1
HF_HUB_DISABLE_TELEMETRY=1
PROCESS_QOS=utility
PROCESS_NICE=10
DOCX_ENABLED=true
AUDIO_SAMPLE_RATE=16000
AUDIO_FFMPEG_THREADS=1
AUDIO_CPU_LIMIT_PERCENT=60
AUDIO_CPU_LIMIT_PERIOD_SECONDS=0.2
AUDIO_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_ENABLED=true
OCR_FRAME_INTERVAL_SECONDS=5
OCR_LANGUAGES=auto
OCR_FFMPEG_THREADS=4
OCR_WORKERS=5
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=0
OCR_FRAME_PAUSE_SECONDS=0
OCR_VISUAL_DEDUPE_ENABLED=true
OCR_VISUAL_DEDUPE_IGNORE_BOTTOM_RATIO=0.18
OCR_VISUAL_DEDUPE_IGNORE_RIGHT_RATIO=0.20
OCR_VISUAL_DEDUPE_MAX_MEAN_DELTA=6.0
OCR_MAX_SNAPSHOT_GAP_SECONDS=120
OCR_VISUAL_ONLY_MIN_MEAN_DELTA=12.0
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=0
OCR_FRAME_EXTRACT_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_FRAME_EXTRACT_CPU_LIMIT_FALLBACK_BURST_CORES=1.5
OCR_SIGNATURE_CPU_LIMIT_PERCENT=0
OCR_SIGNATURE_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_SIGNATURE_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_TESSERACT_CPU_LIMIT_PERCENT=0
OCR_TESSERACT_CPU_LIMIT_PERIOD_SECONDS=0.2
OCR_TESSERACT_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=false
CLEANUP_JOB_MEDIA_AFTER_ARCHIVE=true
COMPLETED_JOB_RETENTION_HOURS=0
```

`LANGUAGE=auto`는 STT 입력 언어 자동 감지를 의미하며 전사는 감지한 원문 언어를 유지한다. `OCR_LANGUAGES=auto`는 영어 전사에 `eng`, 한국어 전사에 `kor+eng`을 선택해 화면 텍스트도 번역하지 않고 근거로 보존한다. `OUTPUT_LANGUAGE=auto`는 최종 문서도 원문 언어를 유지한다. 한국어 변환이 필요할 때만 `OUTPUT_LANGUAGE=ko`, 영어를 명시하려면 `OUTPUT_LANGUAGE=en`을 사용한다. 영어와 한국어 모두 오디오 추출·STT 1회와 영상 OCR 1회라는 같은 처리 순서를 사용하며 로컬 음성 화자분리는 실행하지 않는다. 목표 언어가 원문과 다를 때도 콘텐츠 분석·감사는 원문 언어의 `minutes.md`에서 한 번만 수행하고 해시로 동결한다. 그 뒤 원시 근거를 읽지 않는 저추론 번역 전용 세션이 Markdown 구조와 값은 유지한 채 `minutes.translated.md`를 한 번 만들며, 재요약·재분석·추가 모델 검토는 하지 않는다. `WHISPER_DEVICE=gpu`는 MLX 전사 단계에서 Apple Silicon GPU/Metal 경로를 명시적으로 사용한다.

### Whisper 모델 캐시

`mlx-whisper`는 같은 `WHISPER_MODEL`을 영상마다 다시 저장하지 않는다. Hugging Face 캐시는 실제 파일을 `blobs/`에 내용 해시 기준으로 한 번 저장하고, `snapshots/<revision>/`에서는 해당 blob을 가리키는 링크를 사용한다. 같은 모델과 같은 파일은 모든 영상에서 재사용된다. 모델을 바꾸거나 기존 저장소의 새 리비전에서 가중치가 실제로 변경된 경우에만 새 blob이 추가될 수 있다. 캐시 위치 기본값은 `~/.cache/huggingface/hub`이며 `HF_HUB_CACHE`로 변경할 수 있다.

`HF_HUB_OFFLINE=1`이면 처리 중 Hugging Face HTTP 요청과 최신 리비전 확인을 하지 않고 기존 캐시만 사용하므로 영상 처리 때 캐시가 증가하지 않는다. 아직 모델이 캐시되지 않은 새 PC에서는 한 번만 네트워크를 허용해 모델을 준비한 뒤 다시 오프라인으로 전환해야 한다. 리비전 폴더가 여러 개 보여도 동일한 blob을 가리키면 가중치 용량이 중복된 것은 아니다. 캐시는 자동 삭제하지 않는다. 다른 모델이나 과거 리비전 삭제는 실행 중인 설정을 확인한 뒤 Hugging Face 캐시 관리 기능으로 명시적으로 수행한다.

### API prompt cache와 디스크 캐시 구분

`cached_input_tokens`는 로컬 디스크 용량이 아니라 API가 반복 입력 문맥을 재사용한 누적 token
계정이다. 비율이 높으면서 `tool_count`도 높으면 같은 긴 문맥을 여러 도구 왕복에서 다시 보낸
것이므로, 로컬 Whisper/Hugging Face 캐시를 삭제해도 줄지 않는다. 오히려 API prompt cache가
없으면 같은 실행의 uncached 입력 비용이 커진다. `context_efficiency`, 단계별 `tool_count`,
`duplicate_evidence_chunk_read_count`를 함께 보고 왕복 자체를 줄인다.

### 내용 보존 감사와 최신 공식 문서 확인

Codex 모드에서 축약 누락과 의미 변형을 막으려면 다음을 설정한다.

```env
LLM_PROVIDER=codex
CONTENT_AUDIT_MODE=strict
OFFICIAL_SOURCE_VERIFICATION=auto
```

`strict`는 최종 문서 작성 전에 `content_inventory.json`, 작성 후 `content_audit.json`을 요구한다. 정책·날짜·버전·수치·범위·조건·예외·부정 표현·제한·위험·질의응답과 STT/OCR 충돌을 필수 근거로 추적하며, 누락이나 qualifier 변경이 있으면 `archive_job.py`가 보관을 거부한다. 감사 파일은 부모의 완료 평가가 끝날 때까지 job에만 남고 최종 output에는 복사되지 않는다.

최종 문서에는 글자 수, token 수, 페이지 수, bullet 수, section 수의 하드 상한을 두지 않는다. 핵심 요약은 간결하게 작성하되 본문은 필수 근거가 모두 들어갈 만큼 충분히 작성한다. 인사·말버릇·의미가 완전히 같은 반복만 길이 축약 대상으로 본다.

원문이 한 context에 들어가지 않을 정도로 길면 시간 구간별로 읽어 하나의 누적 inventory에 항목을 추가한다. 구간별 서술 요약을 만든 뒤 다시 요약하는 방식은 사용하지 않는다. context 한계는 읽기 순서만 나누며 최종 문서의 길이나 필수 내용 보존 범위를 줄이지 않는다.

`OFFICIAL_SOURCE_VERIFICATION=auto`는 먼저 음성 문맥·시간별 STT·OCR·Snapshot을 교차 확인하고, 표현·고유명사·버전 또는 의미가 여전히 모호하거나 충돌할 때만 최신 공식 문서·공식 release note·service announcement·표준 원문·upstream 보안 권고를 조사한다. 공식 문서는 전사를 보강할 수 있지만 명확한 영상 발언을 바꾸지 않는다.

공식 문서를 사용한 경우 최종 문서 맨 아래 `외부 근거 확인` 섹션을 만들고 `전사·OCR 보강 근거`와 `영상 내용과 상충하는 근거`를 구분한다. 영상 내용과 timestamp, 조사 목적, 공식 확인 결과, 차이 또는 보강 표현, 확인일과 링크를 남기며 그 뒤에는 다른 H2 섹션을 두지 않는다. 웹 검색에는 공개 제품명·버전·일반화한 정책 검색어만 사용하고 STT/OCR 원문, 고객명, 참석자명, 내부 식별자와 비밀정보를 보내지 않는다.

전사 단계는 `ffmpeg`로 16kHz mono PCM을 한 번 추출해 메모리에서
`mlx-whisper`에 전달한다. `AUDIO_FFMPEG_THREADS=1`은 입력 디코더와 출력 PCM
인코더 스레드를 제한한다. `PROCESS_QOS=utility`, `PROCESS_NICE=10`은 CLI 전체와
하위 프로세스의 우선순위를 낮추며, 작업 잠금은 watcher와 수동 실행을 합쳐 무거운
작업을 한 번에 하나만 실행한다.

### 근거 기반 화자 식별

`SPEAKER_ATTRIBUTION_MODE=evidence`는 Whisper의 구간 타임스탬프 STT, 시간별 OCR,
필요한 소수 Snapshot을 최종 LLM이 교차 확인하게 한다. 오디오 추출과 STT는 유지하지만
별도의 로컬 음성 화자분리는 화면 근거가 충분하거나 부족한 경우 모두 실행하지 않는다.
Silero ONNX 검증은 STT가 명백한 무음 위에 생성됐는지만 표시하며 화자를 나누거나
이름을 추정하지 않는다. `audio`와 `hybrid` 값은 자동 경로에서 거부된다.

이름이 명시된 자막·이름표, 자기소개, 직접 호명과 응답, 발언 인계처럼 실제 영상에
존재하는 근거만 사용한다. 참가자 목록, 화면 공유자 표기, 특정 서비스·색상·테두리·화면
배치를 화자 증거로 단정하지 않는다. 화면 근거가 없거나 충돌하면 STT의 명시적 근거만
사용하고, 그것도 부족하면 `화자 미상`으로 남긴다. 화자명을 확정하지 못해도 발언 내용은
생략하지 않는다. `SPEAKER_ATTRIBUTION_REQUIRED=true`는 근거 없는 강제 식별을 유도하므로
거부된다.

```env
SPEAKER_ATTRIBUTION_MODE=evidence
SPEAKER_ATTRIBUTION_REQUIRED=false
SPEECH_ACTIVITY_VALIDATION_ENABLED=true
```

SpeechBrain·ECAPA·scikit-learn·torchaudio 기반 실험 코드는 제거됐다. 남은 Silero ONNX의
목적·해시·오프라인 경계는 [SECURITY.md](SECURITY.md)에 기록한다.

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

최종 언어는 `OUTPUT_LANGUAGE`로 제어한다. `auto`는 원문 언어 유지, `ko`는 한국어, `en`은 영어다. 목표 언어가 원문과 다르면 검증·동결된 원문 언어 완성본을 번역 전용 세션에서 한 번 번역하고, 구조·보호값·해시 검증 후 그 결과만 Word에 사용한다.

## 보안과 외부 전송

기본 처리 파일은 로컬에 저장된다. 원본 영상/녹음, 추출 음성, 전사 JSON/TXT/SRT, OCR 이미지, snapshot, 최종 문서는 기본적으로 `~/minutes` 아래에 생성된다.

외부 전송이 발생하는 지점은 다음으로 한정한다.

- `mlx-whisper` 모델은 신뢰한 네트워크에서 오프라인 모드를 잠시 해제한 최초 준비 시에만 Hugging Face 캐시로 다운로드한다. 실제 영상 처리의 `HF_HUB_OFFLINE=1` 상태에서는 다운로드나 최신 리비전 확인을 하지 않으며 영상이나 녹음 파일을 Hugging Face로 업로드하지 않는다.
- 표준 `process_file.py`는 로컬의 해시 검증된 Silero ONNX만 발화 존재 검증에 사용한다. ECAPA·SpeechBrain 모델은 다운로드하거나 로드하지 않는다.
- Community-1은 회사 관리 계정의 gated 조건 수락 기록과 조건 캡처·해시, 승인자·용도,
  immutable revision과 전체 파일 해시가 있는 사내 오프라인 mirror가 모두 검증된 경우에만
  준비 상태가 된다. job 중 자동 다운로드와 Hugging Face token 전달은 금지하며 현재 자동
  화자 판단 경로에는 연결하지 않는다.
- `LLM_PROVIDER=openai`이면 최종 문서 생성을 위해 전사 텍스트와 OCR 텍스트가 OpenAI API로 전송된다.
- `LLM_PROVIDER=oci`이면 전사 텍스트와 OCR 텍스트가 설정한 OCI GenAI endpoint로 전송된다.
- `LLM_PROVIDER=codex`이면 전처리 스크립트는 `codex_minutes_input.md`를 로컬 job 폴더에
  만들고 멈춘다. `$minutes`는 이어서 `run_fresh_codex_job.py`로 기존 대화를 상속하지 않는
  ephemeral Codex 세션을 실행한다. Codex provider가 job 근거를 읽는 시점 외에 다른
  서비스로 STT/OCR을 전송하지 않는다.
- `OFFICIAL_SOURCE_VERIFICATION=auto|required`이면 Codex가 필요한 최신 공식 근거를 찾기 위해 웹 검색을 사용할 수 있다. `auto`는 로컬 근거가 모호하거나 충돌할 때만 검색한다. 검색 요청에는 공개 제품명·버전·일반화한 주장만 포함하며 원문 STT/OCR이나 개인·내부 식별 정보는 포함하지 않는다. 확인한 URL과 결과는 로컬 `official_sources.json`과 최종 문서 맨 아래의 근거 섹션에 기록한다.

GitHub에 올릴 때는 `.env`, `.venv/`, `.omx/`, job/output 산출물, 영상/녹음/음성/자막/DOCX 파일이 `.gitignore`에 의해 제외된다. API 키는 repo에 저장하지 말고 환경변수, `.env`, 또는 macOS Keychain을 사용한다.

자동 처리의 화자 판단은 로컬 음성 모델을 사용하지 않는다. Silero 결과는 무음 전사 검토 표시일 뿐 화자 근거가 아니다. 모델의 라이선스·해시·보안 경계는 [SECURITY.md](SECURITY.md)에 기록한다. 기본 STT인 `mlx-community/whisper-large-v3-turbo`는 MLX 변환 모델이므로 upstream `openai/whisper-large-v3-turbo`의 라이선스와 모델 카드도 함께 확인한다.

Community-1 사전 조건은 다운로드 없이 읽기 전용으로 검사한다.

```bash
.venv/bin/python -m scripts.community1_governance \
  --approval ~/minutes/governance/pyannote-community1-approval.json \
  --model-dir ~/minutes/models/pyannote-community1
```

여기서 비활성화되는 것은 STT 뒤에 다시 음성을 훑는 별도 음향 화자분리뿐이다. `ffmpeg` 음성 추출과 MLX Whisper STT는 항상 유지한다. 추출된 `audio.wav`는 전사가 끝난 뒤 다시 만들 수 있는 임시파일이므로 정리하며, 원본 영상·녹음과 전사 결과는 그대로 보존 정책을 따른다.

OCI SDK는 선택 설치로 분리되어 있다. 기본 설치 경로에는 포함되지 않으며, OCI provider를 실제로 사용할 때 별도로 설치하고 검토한다.

API 인증 없이 Codex가 최종 문서를 직접 작성하게 할 때는 실행 시 provider를 `codex`로 지정한다. 위의 `$minutes` skill 사용을 권장하며, 수동으로 실행할 수도 있다.

```bash
LLM_PROVIDER=codex python scripts/process_file.py ~/remind/meeting_video.mp4
```

이 모드는 전사/OCR을 완료한 뒤 `codex_minutes_input.md`를 만들고 멈춘다. 아래 명령은
원문을 기존 대화에 복사하지 않고 서로 분리된 content/delivery ephemeral Codex 세션에서
`minutes.md`, 감사 파일, Documents 방식으로 최종 검수한 DOCX를 만든 뒤 최종 output 폴더까지
정리한다. worker의 stdout은 JSONL 이벤트 파일로만 보존하고 콘솔에는 단계 완료와 token 사용량만
요약하므로, 임시 산출물 전체나 대형 diff가 반복 출력되지 않는다.

명시한 출력 언어가 감지한 원문과 다르면 두 세션 사이에 번역 전용 ephemeral 세션 하나만
추가된다. content와 delivery는 `high`, 번역은 `low` 추론이 기본이다. 번역 세션은 동결된
`minutes.md`만 받고 도구를 사용하지 않으며, 결과는 `translation_manifest.json`의 구조·보호값·
SHA-256 검사를 통과해야 한다. 언어가 같으면 번역 단계와 비용은 완전히 생략된다.

content와 delivery worker에는 필요한 품질 규칙을 8KB 미만의 compact prompt로 미리 넣는다.
worker가 전체 `SKILL.md`나 reference를 다시 읽는 것은 금지된다. command output과 file-change
diff를 모두 계측하며, 단일 tool output이 20KB를 넘거나 금지된 지침 파일을 열면 해당 phase를
즉시 종료한다. 20KB는 내용을 잘라 계속하는 상한이 아니라 저품질 산출물을 막는 실패형 상한이다.
중복 evidence part 읽기도 즉시 종료한다. 정상 완료한 `fresh_codex_handoff.json`은
`worker_contract_passed=true`, oversized output 0회, forbidden instruction read 0회,
duplicate evidence chunk read 0회를 기록한다.
fresh worker 안에서만 Documents plugin 주입을 끄지만 설치된 `render_docx.py`는 그대로 직접
사용하므로 렌더·전 페이지 검증 기능은 유지된다.

```bash
./scripts/run_fresh_codex_job.py "<job-directory>"
```

OCR은 기본적으로 5초마다 프레임 1장을 추출해 로컬 `tesseract`로 처리한다. `tesseract`가 설치되어 있지 않으면 OCR만 건너뛰고 전사와 최종 문서 생성은 계속 진행한다. 한국어 OCR에는 별도 언어 데이터가 필요하므로 macOS/Homebrew 환경에서는 `brew install tesseract-lang`을 함께 실행한다. 설치 후 `tesseract --list-langs`에 `kor`가 표시되어야 `OCR_LANGUAGES=auto`의 한국어 입력이 `kor+eng`으로 동작한다.

Snapshot은 OCR 텍스트뿐 아니라 큰 화면 전환, 참가자 UI 변화, OCR이 비어 있는 시각 근거와
최대 120초 커버리지 보정 프레임도 저장한다. 따라서 긴 영상에서 텍스트 중복 제거 때문에
근거가 지나치게 성기게 남는 문제를 막는다. `evidence_coverage.json`은 추출한 모든 프레임의
SHA-256, 선택·제외 사유, 선택 Snapshot과의 대응, 최대 간격을 기록한다. 원본 `frames/`와
선별 `snapshots/`는 완료 job 보존 기간 동안 유지하며, 최종 산출물과 근거 해시 검증을 통과한
job 전체를 삭제할 때만 함께 정리한다. 최종 output에는 선별 Snapshot만 복사한다.

`OCR_WORKERS`는 서로 다른 프레임을 동시에 처리하는 Tesseract 프로세스 수다. 각
프로세스는 `OCR_TESSERACT_THREAD_LIMIT=1`을 유지하므로 내부 스레드를 무작정 늘리는
설정과 다르다. 병렬 작업이 끝나도 결과는 타임스탬프 순서로 다시 적용하며 기존 화면·텍스트
중복 제거와 snapshot 번호 순서를 보존한다. 검증된 기본 프로필은 5개 worker를 사용한다.
부하가 큰 장비에서는 `.env`에서 값을 낮춘다. 스킬과
런타임은 CPU 사용률을 보고 worker 수를 자동 증감하지 않는다.

CPU 부하는 전체 작업의 `taskpolicy` Utility QoS·`nice`·단일 작업 잠금과,
ffmpeg/Tesseract의 스레드 및 duty-cycle 제한으로 제어한다.
`AUDIO_CPU_LIMIT_PERCENT`는 macOS 전체 CPU의 정밀한 hard cap이 아니라 해당 외부
프로세스의 평균 부하를 낮추는 근사값이다. OCR 프레임 추출은 측정 결과 duty-cycle cap이
CPU 절감 없이 wall time만 늘려 기본값을 `0`으로 바꿨다. MLX
Whisper는 Apple Silicon GPU/Metal 경로라 이 CPU 백분율 제한 대상이 아니며, 더 낮은
전사 부하가 필요하면 모델 크기를 낮춰야 한다. Silero ONNX 검증은 CPU 단일 스레드로만 실행되며 ECAPA 단계는 없다.

```env
PROCESS_QOS=utility
PROCESS_NICE=10
AUDIO_CPU_LIMIT_PERCENT=60
AUDIO_CPU_LIMIT_PERIOD_SECONDS=0.2
AUDIO_CPU_LIMIT_FALLBACK_BURST_CORES=2.5
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=0
OCR_FRAME_EXTRACT_CPU_LIMIT_FALLBACK_BURST_CORES=1.5
OCR_SIGNATURE_CPU_LIMIT_PERCENT=0
OCR_TESSERACT_CPU_LIMIT_PERCENT=0
OCR_FRAME_INTERVAL_SECONDS=5
OCR_FFMPEG_THREADS=4
OCR_WORKERS=5
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=0
OCR_FRAME_PAUSE_SECONDS=0
OCR_MAX_SNAPSHOT_GAP_SECONDS=120
OCR_VISUAL_ONLY_MIN_MEAN_DELTA=12.0
```

`OCR_FFMPEG_THREADS=4`는 프레임 추출 디코더가 한 코어에 고정되지 않도록 하는
검증된 기본값이다. 동일 영상 앞 10분·5초 간격 JPEG 120장 추출 실측에서 1 thread
16.77초 대비 4 threads 6.34초로 2.64배 빨랐다. `OCR_WORKERS=5`는 현재 11-core M3 Pro에서
확인한 처리량 설정이다. 다른 Mac에서는 같은 값으로 시작하되 부하가 크면 별도로 측정한 뒤
`.env`에서 낮춘다.
`OCR_TESSERACT_THREAD_LIMIT=1`은 각 Tesseract 프로세스의 OpenMP 스레드 사용을 제한한다.
전체 job이 이미 `PROCESS_NICE=10`으로 실행되므로 `OCR_TESSERACT_NICE=0`은 자식에게 같은
우선순위를 상속하고 nice가 20으로 중복 누적되는 문제를 막는다. `OCR_FRAME_PAUSE_SECONDS`는
프레임 OCR 사이에 대기 시간을 넣어 처리 속도 대신 순간 CPU 부하를 낮춘다.

동일 영상·187프레임 A/B에서 프레임 추출 cap 80은 101.74초, cap 0은 50.72초였고
두 실행의 프레임은 byte-identical이었다. Tesseract는 고정 크기 batch 대신 최대
`2 × OCR_WORKERS`만 제출하는 bounded dynamic queue를 사용한다. `screen_text.json`,
`process_metrics.json`, `ocr_benchmark.json`에는 단계별 wall/CPU time, 호출 수, 평균·최대
동시 worker, queue 대기와 선택 결과 해시가 기록된다.

2026-07-15에 31분 12.57초 대상 영상을 현재 로컬 프로필로 재측정한 결과는 다음과 같다.

- 종전: 10초 간격 187프레임, Snapshot 22장, OCR wall 134.96초, CPU 124.41초,
  최대 Snapshot 공백 480초
- 현재: 5초 간격 375프레임, Snapshot 42장, OCR wall 65.917초, CPU 117.54초,
  최대 Snapshot 공백 120초
- 변화: wall 51.2% 단축, CPU 5.5% 감소, 분석 프레임 약 2배, 선별 근거 90.9% 증가
- 현재 단계별 wall: 프레임 추출 50.309초, 화면 서명 3.941초, Tesseract 11.537초,
  결과 적용 0.022초
- Tesseract 후보 42개, 최대 active worker 5, 평균 active worker 4.549,
  process 단위 peak RSS 약 75.1 MiB

선별되지 않은 333프레임도 버리지 않고 완료 job에 해시·제외 사유와 함께 남긴다. 이 실행의
원본 프레임은 약 44.4 MiB, 선별 Snapshot은 약 4.8 MiB이므로 근거 강화의 보존 비용도
명시적으로 확인할 수 있다.

`PROCESS_QOS=background`나 `maintenance`는 더 보수적이지만 처리 시간이 크게 늘 수 있다. 기본 `utility`는 인터랙티브 작업을 방해하지 않으면서 처리량을 지나치게 낮추지 않는 균형값이다. `process_metrics.json`에서 STT·`validate_speech_activity`·OCR 단계별 wall time과 CPU time을 확인할 수 있다. `diarize` 또는 `attribute_speakers` 단계가 나타나면 evidence-only 정책 위반이다.

완료 후 `COMPLETED_JOB_RETENTION_HOURS`가 지난 job은 다음 처리나 아카이브 때 자동 삭제한다.
완료 직후 성능·품질 평가가 끝난 작업의 보조 파일을 남기지 않으려면 로컬 `.env`에서
`COMPLETED_JOB_RETENTION_HOURS=0`으로 설정하고 검증된 해당 job만 정리한다.
삭제 전에는 status에 기록된 최종 미디어·Markdown·DOCX·Snapshot과 job-local
`docx_qa.json`을 검증한다. 전달물은 output 경로 내부, QA는 해당 job 내부인지 확인한다.
실패·Codex 대기·진행 중 job과 `jobs/index.json`, `.process.lock`은 삭제하지 않는다. 수동
명령은 기본적으로 dry-run이며 `--apply`가 있을 때만 만료된 job 폴더 전체를 삭제한다.

```bash
python scripts/cleanup_completed_jobs.py
python scripts/cleanup_completed_jobs.py --apply
```

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

영상이나 오디오 파일이 여러 개 있으면 처리할 파일 경로를 직접 지정한다. 수동 실행은 `~/remind`에 묶이지 않으며, `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`, `.ogg` 파일이면 다른 폴더의 절대 경로나 상대 경로도 사용할 수 있다.

```bash
python scripts/process_file.py ~/remind/meeting_video.mp4
python scripts/process_file.py ~/remind/meeting_audio.m4a
```

예를 들어 `~/remind` 안의 파일 목록을 보고 원하는 파일만 지정할 수 있다.

```bash
ls -lh ~/remind
python scripts/process_file.py "~/remind/2026-06-17 회의 영상.mp4"
```

다른 위치의 파일도 같은 방식으로 처리한다.

```bash
python scripts/process_file.py "~/Desktop/2026-06-18 고객 미팅.mov"
```

결과는 `촬영-날짜_내용-기반-제목` 폴더에 생성해 output 최상위에서도 내용을
바로 파악할 수 있게 한다. 날짜가 없으면 원본 수정 날짜를 사용하고, 내용 제목은 완성
문서의 H1에서 만든다.

```text
~/minutes/output/YYYY-MM-DD_내용-기반-제목/
  YYYY-MM-DD_내용-기반-제목.mp4 또는 YYYY-MM-DD_내용-기반-제목.m4a
  YYYY-MM-DD_내용-기반-제목.md
  YYYY-MM-DD_내용-기반-제목.docx
  snapshots/
    snapshot_0001_00-00-00.jpg
    snapshot_0002_00-00-10.jpg
```

최종 폴더에는 전달용 미디어, Markdown, DOCX, 의미 있는 snapshot만 둔다. `docx_qa.json`,
transcript, OCR, 원본 frame, evidence coverage, 화자 근거 정책 보고서, 로그와 상태 파일은
감사·재처리를 위해 `~/minutes/jobs/<job_id>/`에만 보존한다.

## 자동 감시 실행

```bash
python scripts/watch_recordings.py
```

자동 처리할 영상 또는 녹음 파일이 다음 폴더에 저장되도록 지정한다.

```text
~/remind
```

watcher는 `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`, `.ogg` 파일을 감지한다. 파일 크기와 수정 시간이 일정 시간 변하지 않을 때 저장 완료로 판단하고 처리한다.

## 처리 상태

각 작업은 `~/minutes/jobs/<job_id>/status.json`에 상태를 남긴다.

작업 중간 파일은 같은 job 폴더에 저장된다. `CLEANUP_JOB_MEDIA_AFTER_ARCHIVE=true`이면 STT가 끝난 즉시 재생성 가능한 `audio.wav`를 제거한다. `CLEANUP_JOB_OCR_IMAGES_AFTER_ARCHIVE=false`가 기본이며 원본 `frames/`, 선별 Snapshot, `evidence_coverage.json`은 현재 job의 부모 평가가 끝날 때까지 유지한다. `COMPLETED_JOB_RETENTION_HOURS=0`이 기본이므로 최종 미디어·Markdown·DOCX·Snapshot과 job-local QA·근거 해시가 검증된 이전 완료 job은 다음 처리 또는 아카이브에서 삭제된다. 의도적인 재작업 기간이 필요할 때만 양수 시간을 설정한다. `source.<ext>`는 성공적으로 아카이브된 뒤 최종 output으로 이동한다.

```text
source.<ext>
transcript.json
transcript.txt
transcript.srt
screen_text.json
screen_text.txt
evidence_coverage.json
frames/
snapshots/
minutes.raw.json
minutes.md
minutes.translated.md            # 목표 언어가 원문과 다를 때만
translation_manifest.json        # 목표 언어가 원문과 다를 때만
minutes.draft.docx
minutes.final.docx
docx_qa.json
status.json
process_metrics.json
logs.txt
```

이미 완료된 파일은 fingerprint 기준으로 재처리하지 않는다.
