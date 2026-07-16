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

`$minutes`는 로컬 전처리와 근거·내용 감사를 담당한다. `finalize_docx.py`는 번들된
`codex/skills/minutes/assets/minutes-word-template.docx`의 표지·목차·본문 슬롯을 채운 뒤 Codex에
번들된 Documents renderer로 Word를 렌더링하고 delivery 세션이 전 페이지를 검증한다. 매 job마다
Documents `SKILL.md` 전문을 읽지 않으며, 이 저장소에서는 `minutes`만 복사한다. Documents
renderer가 없는 Codex 설치는 먼저 업데이트한다.

`run_codex.py`는 현재 shell 환경변수, `.env`, 기본값 순서로 `MINUTES_HOME`과 `RECORDINGS_INBOX`를 해석하고 두 경로를 Codex `--add-dir`에 전달한다. 경로를 변경할 때는 저장소 파일을 수정하지 않고 각 사용자의 `.env`만 바꾸면 된다. 실행된 Codex에서 `$minutes`로 영상 또는 녹음 파일 경로를 지정한다.

```text
$minutes Codex 모드로 "~/remind/2026-06-18 회의.mov" 내용 정리해줘
$minutes Codex 모드로 "~/remind/2026-06-18 회의.m4a" 내용 정리해줘
$minutes "~/Desktop/customer-call.mov" 내용에 맞는 문서로 정리해줘
$minutes "~/remind/demo.mp4" CPU와 소요시간도 측정해줘
```

`Codex 모드`는 오디오 추출·전사·OCR을 로컬 처리한 뒤, 기존 대화와 분리된 content
`codex exec --ephemeral` 세션이 job의 비중첩 근거 청크를 한 번씩 읽어 화자 판단과 실제
내용에 맞는 제목·섹션을 정하고 원문 언어 `minutes.md`를 감사·동결한다. 목표 언어가 다를
때만 번역 세션 하나를 추가하며, delivery 세션이 `$documents`로 Word를 렌더링·전 페이지
검증한 다음 최종 output 폴더를 정리한다. handoff prompt에는
원문을 넣지 않고 job 경로·출력 정책·해당 작업의 짧은 추가 요청만 넣는다.

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

`OUTPUT_LANGUAGE=auto`이면 영어 영상은 영어, 한국어 영상은 한국어로 최종 작성한다. 한국어 변환이 필요할 때만 `OUTPUT_LANGUAGE=ko`, 영어를 강제하려면 `OUTPUT_LANGUAGE=en`을 지정한다. 영어와 한국어 모두 오디오 추출·STT 1회와 영상 OCR 1회로 처리하며 로컬 음성 화자분리는 실행하지 않는다. STT, OCR, 누적 inventory와 콘텐츠 감사는 원문 언어를 유지한다. 명시한 목표 언어가 원문과 다를 때만 동결된 완성 Markdown을 저추론 번역 전용 세션에서 한 번 번역하며, 원시 근거 재독·재요약·재분석·추가 모델 검토는 하지 않는다.

Whisper 모델 캐시는 영상마다 새로 생기지 않는다. 같은 모델 파일은 Hugging Face `blobs/`에 한 번 저장되고 리비전 Snapshot이 공유한다. `HF_HUB_OFFLINE=1`인 실제 처리에서는 기존 캐시만 사용하므로 캐시가 늘지 않는다. 새 PC에서는 최초 한 번 모델을 준비해야 하며, 모델이나 실제 가중치 리비전을 바꾼 경우에만 추가 blob이 생길 수 있다.

내용을 억지로 줄이지 않고 원문 대비 감사를 필수화하려면 Codex 모드에 다음 설정을 사용한다.

```env
LLM_PROVIDER=codex
CONTENT_AUDIT_MODE=strict
OFFICIAL_SOURCE_VERIFICATION=auto
```

최종 문서에는 글자·token·페이지·bullet·section 수의 상한을 두지 않는다. `strict` 모드는 `content_inventory.json`에서 정책·수치·조건·예외·질의응답·근거 충돌을 먼저 목록화하고, `content_audit.json`에서 완성본과 다시 대조한다. 필수 항목 누락, 의미 강도 변경, 공개되지 않은 근거 충돌이 있으면 보관을 중단한다.

원문이 한 context보다 길면 시간 구간별로 순차 처리해 같은 inventory에 누적한다. 구간별 서술 요약을 다시 종합하는 손실형 처리는 사용하지 않으며, context 한계 때문에 최종 문서 길이나 필수 내용을 줄이지 않는다.

공식 문서 확인은 먼저 음성 문맥·시간별 STT·OCR·Snapshot을 교차 확인한 뒤에도 모호하거나 충돌하는 내용과 외부에서 확인 가능한 제품 지원·버전·출시·EOL·정책·보안·API 주장이 미해결일 때 수행한다. 발표자 설명이나 추정도 qualifier를 보존한 채 확인하며, 사내 결정과 POC 측정값은 로컬 추가 검증 대상으로 남긴다. 공식 근거는 전사를 보강할 수 있지만 명확한 영상 발언을 바꾸지 않는다. 검색어에는 공개 제품명·버전·일반화한 주장만 사용하고 STT/OCR 원문이나 개인·내부 식별 정보를 보내지 않는다.

공식 확인 모드가 켜진 최종 문서는 `추가 검증이 필요한 항목`과 `외부 근거 확인`을 마지막 두 H2로 항상 둔다. 항목이 없으면 섹션을 없애지 않고 구체적인 사유와 확인일을 적는다. `외부 근거 확인`에는 영상 우선 원칙과 원문 STT/OCR·개인·내부 식별정보 비전송 사실을 표시한다. 공식 문서를 사용했다면 `전사·OCR 보강 근거`와 `영상 내용과 상충하는 근거`를 구분하고 timestamp, 조사 목적, 결과, 확인일과 링크를 남긴다. 상충하더라도 영상 본문은 그대로 유지하며 그 뒤에 다른 H2를 추가하지 않는다.

## 7. Codex 모드 수동 실행

Skill을 쓰지 않고 Codex 모드를 수동으로 실행할 수도 있다.

```bash
LLM_PROVIDER=codex python scripts/process_file.py "~/remind/회의.mov"
```

이 모드는 전사와 OCR을 끝낸 뒤
`~/minutes/jobs/<job_id>/codex_minutes_input.md`를 만들고 멈춘다. 현재 대화에서 원문을
읽거나 축약하지 말고, 출력된 job 경로를 새 Codex 세션에 넘긴다.

```bash
./scripts/run_fresh_codex_job.py "<job-directory>"
```

`--dry-run`은 전체 프롬프트나 근거 목록을 콘솔에 인쇄하지 않는다. 단계, 실행 명령,
프롬프트 바이트·SHA-256, 근거 개수만 담은 8KB 이하 요약을 출력한다.

일반 작업은 `high` 추론 수준이 기본이며 현재 Codex UI가 `max`여도 별도로 적용된다.

작업별 추가 요청이 설정에 포함되지 않았다면 짧게 별도 전달할 수 있다.

```bash
./scripts/run_fresh_codex_job.py \
  "<job-directory>" \
  --request "CPU와 전체 소요시간도 보고"
```

콘텐츠 세션은 전체 STT·OCR을 최대 500줄·15KB 이하의 비중첩 청크로 나누고, 청크마다
별도 bounded read를 한 번씩 실행한다. 콘텐츠 통과 후 `content_freeze.json`으로 원문 언어
Markdown과 감사 산출물을 잠근다. 목표 언어가 다르면 그 사이에 `low` 추론의 도구 없는 번역
세션 하나가 동결 Markdown만 받아 `minutes.translated.md`를 만들고
`translation_manifest.json`의 구조·보호값·SHA-256 검사를 통과한다. 언어가 같으면 번역 세션은
생략된다. delivery 세션은 STT/OCR 없이 검증된 최종 Markdown과 Word 렌더만 읽는다. 대형 전처리 JSON은
`worker_runtime_summary.json`의 제한된 검증 필드로 대체한다. 정상 미디어 작업에서는 전체
저장소 테스트와 검증기 구현 탐색을 생략하고 콘텐츠·DOCX·아카이브 게이트만 실행한다.
`fresh_codex_handoff.json`에는 원문 파일의 크기·SHA-256과 Snapshot 수, 콘텐츠/번역/delivery
단계별 시간·토큰·도구 출력량을 기록하지만 prompt 자체에는 원문을 복사하지 않는다.
청크 manifest의 줄 번호는 원본 좌표이므로 part 파일에는 적용하지 않는다. 각 part 전체를 정확히
한 번 읽고, 같은 part의 두 번째 명령은 launcher가 종료한다. prompt에 정확한 sidecar schema를
선주입하고 freeze·translation·DOCX 명령에는 저장소의 절대 `.venv/bin/python` 경로를 사용한다.
content 50회와 delivery 18회는 도구 왕복 비용 목표이며 감사나 전 페이지 검증을 줄이는 상한이 아니다.
`CONTENT_AUDIT_MODE=strict`이면 `content_inventory.json`과 `content_audit.json`이
통과해야만 보관되므로 새 세션 전환 때문에 근거 범위가 줄어들지 않는다.
새 quality contract는 필수 inventory 항목마다 핵심 사실, 조건·예외, 위험·제한, 영향,
실행·결정을 검사한다. 긴 녹화 대비 본문이 비정상적으로 희박하면
`LOW_INFORMATION_DENSITY`를 내고 실패한 `target_section_ids`만 한 번 보강한다. 화면 이미지는
독자 가치가 있는 핵심 3-5개를 먼저 계획하며 연속 대형 이미지와 문서 끝 이미지 배치를 동결 전에
차단한다. 문서와 섹션에는 글자 수 상한이 없으며 밀도 검사는 최소 충실도만 요구한다.
`finalize_docx.py`는 템플릿을 채운 뒤 최초 전 페이지 렌더를 한 번 수행한다. 마지막 페이지의
자연스러운 여백은 `NATURAL_FINAL_PAGE_WHITESPACE` 경고일 뿐 재작성·재렌더링 사유가 아니다.

각 worker는 `codex exec --json`의 JSONL을 단계별 job-local 이벤트 파일에만 저장한다.
콘솔에는 단계 완료, 오류의 제한된 요약과
최종 token 사용량만 표시하므로 임시 산출물 전문이나 대형 diff가 반복 출력되지 않는다.
마지막 JSON 이벤트 이후 10분이면 heartbeat 경고를 표시하고 15분이면 exit code 80으로 종료한다.
이 경우 로컬 STT/OCR이 아니라 Codex 모델·CLI 스트림이 멈춘 것으로 기록되며, 무한 대기하지 않는다.
delivery 실패 후 재실행하면 유효한 freeze와 번역 manifest를 재사용해 콘텐츠 근거와 번역을
다시 실행하지 않는다.
`evidence_coverage.json`은 추출한 모든 영상 프레임의 해시,
선택·제외 사유, Snapshot 대응과 최대 근거 간격을 기록한다.

content와 delivery의 품질 계약은 launcher가 각각 9KB 미만 prompt에 미리 넣는다. worker는
전체 `SKILL.md`나 reference를 열지 않는다. 단일 command/read output이 24KB를 넘거나 지침 파일
읽기가 감지되면 launcher가 해당 phase를 즉시 종료한다. file-change diff는 별도 계측하지만 문서
내용이나 파일 크기를 제한하지 않는다. handoff의
`worker_contract_passed`, `oversized_tool_output_count`, `forbidden_instruction_read_count`,
`duplicate_evidence_chunk_read_count`로
실행별 준수 여부를 확인한다.
Documents plugin 자동 주입은 fresh worker에서만 비활성화하고, `finalize_docx.py`는 설치된
Documents `render_docx.py`를 계속 사용한다.

부모 Codex가 macOS seatbelt 안에서 실행 중이면 launcher가 다시 Codex를 초기화할 수 없으므로
`./scripts/run_fresh_codex_job.py` 명령만 처음부터 escalation해 실행한다. 재사용 승인을
설정한다면 이 정확한 launcher prefix로 한정한다. 새 worker 자체는 `workspace-write`로 다시
격리되며 repo와 설정된 `MINUTES_HOME` 외의 쓰기 경로를 받지 않는다.

## 8. 자동 감시 실행

처리할 영상 또는 녹음 파일이 `~/remind`에 저장되도록 설정한 뒤 watcher를 실행한다.

```bash
python scripts/watch_recordings.py
```

watcher는 `.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`, `.ogg` 파일을 감지한다. 파일 크기와 수정 시간이 일정 시간 변하지 않으면 저장이 끝난 것으로 보고 처리한다.

## 9. 결과물 위치

최종 결과는 `촬영-날짜_내용-기반-제목` 폴더로 정리해 output 최상위에서도
내용을 바로 파악할 수 있게 한다. 날짜가 없으면 원본 수정 날짜를 사용하고, 내용
제목은 완성 문서의 H1에서 만든다.

```text
~/minutes/output/YYYY-MM-DD_내용-기반-제목/
  YYYY-MM-DD_내용-기반-제목.mov 또는 YYYY-MM-DD_내용-기반-제목.m4a
  YYYY-MM-DD_내용-기반-제목.md
  YYYY-MM-DD_내용-기반-제목.docx
  snapshots/
    snapshot_0001_00-00-00.jpg
```

최종 폴더에는 전달용 미디어, Markdown, DOCX, 의미 있는 snapshot만 둔다. `docx_qa.json`,
transcript, OCR, 원본 `frames/`, `evidence_coverage.json`, `speaker_attribution_report.json`,
로그와 상태 파일은 부모의 성능·품질 평가가 끝날 때까지 `~/minutes/jobs/<job_id>/`에 남긴다. `~/remind`
바로 아래에서 처리한 입력 미디어는 성공 시 최종 output으로 이동해 원래 위치에 중복본을
남기지 않는다. 다른 위치에서 지정한 입력은 원본을 보존한다. 실패 작업과 Codex 입력 대기
작업은 기간과 관계없이 job에 보존된다.

`COMPLETED_JOB_RETENTION_HOURS=0`이 기본값이다. 현재 완료한 job은 해당 아카이브 정리에서
제외되어 부모 평가에 사용할 수 있고, 이전 완료 job은 다음 처리나
아카이브 시 최종 미디어·Markdown·DOCX·Snapshot과 job-local `docx_qa.json`, 근거 해시를
검증한 뒤 자동 삭제된다. `jobs/index.json`과 `.process.lock`은 유지된다. 수동 명령은 dry-run 결과를
보여주며, `--apply`를 명시한 경우에만 만료된 job 폴더 전체를 삭제한다.
의도적인 재작업 창이 필요할 때만 양수 시간을 설정한다.

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
OCR_FRAME_INTERVAL_SECONDS=5
OCR_FRAME_EXTRACT_CPU_LIMIT_PERCENT=0
OCR_FFMPEG_THREADS=2
OCR_WORKERS=3
OCR_TESSERACT_THREAD_LIMIT=1
OCR_TESSERACT_NICE=0
OCR_PRESTART_COOLDOWN_SECONDS=20
OCR_MAX_SNAPSHOT_GAP_SECONDS=120
```

현재 M3 Pro의 저발열 기본값은 `OCR_FFMPEG_THREADS=2`, `OCR_WORKERS=3`이다.
두 값은 각각 FFmpeg 디코더와 프레임별 Tesseract 병렬도를 독립적으로 제한한다.
`OCR_PRESTART_COOLDOWN_SECONDS=20`은 MLX Whisper GPU/Metal 작업과 OCR CPU 작업 사이에
냉각 구간을 기록 가능한 별도 단계로 넣는다.

`OCR_WORKERS`는 서로 다른 프레임을 병렬 처리하는 프로세스 수이고,
`OCR_TESSERACT_THREAD_LIMIT`은 각 프로세스 내부의 OpenMP 스레드 수다. 30분 49초 영상의
동일 370프레임 실측에서 기존 4 workers·FFmpeg 4 threads는 104.08초·평균 CPU 346.0%,
새 3 workers·FFmpeg 2 threads는 115.15초·평균 CPU 215.3%였다. Snapshot 57개와 선택
타임스탬프는 동일했고, 평균 CPU는 37.8% 줄었다.
이 값은 `.env`에 명시하며 스킬이나 런타임이 CPU 사용률을 보고 worker 수를 자동 조정하지
않는다. 다른 Mac은 같은 값으로 시작하되 부하가 크면 별도로 측정해 낮춘다. 고정 batch가
아니라 최대 `2 × OCR_WORKERS`의 bounded dynamic queue로 다음 프레임을
즉시 공급하며, 병렬 완료 순서와 무관하게 OCR 결과와 Snapshot은 원래 타임스탬프 순서로
저장된다. 전체 job의 `PROCESS_NICE=10`을 자식이 상속하므로 Tesseract의 추가 nice 값은
기본 0이다.

팬 소음이나 순간 부하가 계속 크면 증거 프레임 간격은 유지한 채 병렬도와 냉각 시간만 더
보수적으로 바꾼다. 이 설정은 Snapshot 선택 기준이나 문서 근거량을 줄이지 않는다.

```env
OCR_WORKERS=2
OCR_FFMPEG_THREADS=2
OCR_PRESTART_COOLDOWN_SECONDS=30
```

MLX 전사는 GPU/Metal을 사용하므로 CPU 백분율 제한 대상이 아니다. 전사 부하가 크면 `WHISPER_MODEL=mlx-community/whisper-medium`처럼 더 작은 모델로 낮춘다. `PROCESS_QOS=background`나 `maintenance`는 더 보수적이지만 전체 처리 시간이 크게 늘 수 있다.

`process_metrics.json`에서 STT·OCR 단계별 wall time과 CPU time을 확인한다. 자동 작업의
단계 목록에 `diarize` 또는 `attribute_speakers`가 있으면 evidence-only 정책 위반이다.
반대로 `extract_audio`, `load_audio`, `transcribe`, `validate_speech_activity`는 필수 단계다.
evidence-only는 오디오 전사를 끄는 설정이 아니라 별도 음향 화자분리를 끄는 설정이다.
전사에 사용한 `audio.wav`는 재생성 가능한 임시파일이므로 전사 성공 뒤 삭제한다.

동일 영상을 OCR만 재현 측정하려면 새 빈 job 디렉터리를 지정한다. 결과 JSON에는 단계별
wall/CPU time, 동시 worker, queue 대기, 프레임·Snapshot 수와 해시가 기록된다.

```bash
nice -n 10 .venv/bin/python -m scripts.benchmark_ocr \
  "/absolute/path/to/video.mov" /private/tmp/minutes-ocr-benchmark \
  --workers 3 --ffmpeg-threads 2 --frame-interval 5 \
  --frame-extract-cap 0 --tesseract-nice 0
```

과거 31분 12.57초 처리량 검증에서는 종전 10초 간격 187프레임·22 Snapshot·134.96초에서,
현재 5초 간격 375프레임·42 Snapshot·65.917초로 바뀌었다. 최대 Snapshot 공백은
480초에서 정책 상한 120초로 줄었고 현재 실행의 CPU time은 117.54초였다. 전체 375프레임은
`evidence_coverage.json`에서 선택·제외 사유와 해시로 추적한다.

## 10. 보안과 외부 전송

원본 영상/녹음, 추출 음성, 전사 파일, OCR 이미지, snapshot, 최종 문서는 기본적으로 로컬 `~/minutes` 아래에 저장된다.

외부 전송은 선택한 LLM provider에 최종 문서 생성을 요청할 때 발생한다. `LLM_PROVIDER=openai`이면 전사/OCR 텍스트가 OpenAI API로 전송되고, `LLM_PROVIDER=oci`이면 설정한 OCI GenAI endpoint로 전송된다.

`.env`, `.venv/`, `.omx/`, job/output 산출물, 영상/녹음/음성/자막/DOCX 파일은 `.gitignore`로 제외된다. API 키는 GitHub에 올리지 말고 환경변수, `.env`, 또는 macOS Keychain을 사용한다.

`pyannote/speaker-diarization-community-1`은 기본 처리에서 비활성화되어 있다. 회사 관리
계정의 gated 조건 수락 기록과 캡처, 승인자·용도, 승인 파일 해시가 있고 사내 오프라인
mirror의 immutable revision·모델 카드·attribution·전체 파일 해시가 일치할 때만 준비 상태가
된다. 다음 검사는 읽기 전용이며 다운로드나 네트워크 호출을 하지 않는다. Hugging Face token은
소스, 승인 파일, 모델 manifest와 job 환경에 두지 않는다.

```bash
.venv/bin/python -m scripts.community1_governance \
  --approval ~/minutes/governance/pyannote-community1-approval.json \
  --model-dir ~/minutes/models/pyannote-community1
```
