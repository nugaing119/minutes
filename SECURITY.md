# 보안 정책과 화자 근거 처리 경계

기준일: 2026-07-15

이 프로젝트는 원본 영상·음성, STT, OCR, Snapshot을 로컬에서 처리한다. 설정한 LLM
provider를 제외하면 영상에서 추출한 데이터가 외부로 전송되지 않아야 한다.
`OFFICIAL_SOURCE_VERIFICATION`을 명시적으로 활성화한 경우에만 최신 공식 문서 확인을
위한 최소한의 일반화된 웹 검색을 추가로 허용한다.

## 최신 공식 문서 검색 경계

`OFFICIAL_SOURCE_VERIFICATION=auto`는 로컬 음성 문맥·STT·OCR·Snapshot을 교차
확인한 뒤에도 표현, 고유명사, 버전 또는 의미가 모호하거나 충돌할 때만 공개 공식 자료를
확인한다. `required`는 최신성에 민감한 주장을 더 폭넓게 확인하지만, 어느 모드도 영상의
명확한 발언을 현재 공식 정보로 덮어쓸 수 없다.

- 검색어에는 공개 제품명, 공개 버전, 일반화한 정책 또는 API 이름만 사용한다.
- STT/OCR 원문 문장, Snapshot, 참석자 이름, 이메일, 내부 프로젝트명, 계정·테넌시·티켓
  식별자와 비밀정보를 검색 서비스로 보내지 않는다.
- 공식 vendor/project 문서, 공식 release note, service announcement, 표준 원문과 upstream
  security advisory를 우선한다.
- 녹화 당시 발언과 현재 공식 상태를 별도 사실로 보존한다. 공식 문서를 사용한 보강 또는
  상충 근거는 최종 문서 맨 아래 별도 섹션에 기록한다.
- `official_sources.json`에 확인 시각, 공식 URL, publisher, 게시·갱신일, 판단과 문서 반영
  방식을 기록하고 `privacy.raw_transcript_or_ocr_sent=false`를 명시한다.
- 공식 자료가 없거나 서로 충돌하면 `not_found`, `partially_verified`, `contradicted`로
  남기며 확정 사실처럼 쓰지 않는다.

`OFFICIAL_SOURCE_VERIFICATION=off`가 clone 직후 기본값이다. 이 프로젝트의 로컬 설정은
`auto`다. 공식 자료를 실제로 사용했는데 최종 부록에 근거와 링크가 없거나, 외부 자료로
영상 내용을 다시 쓴 경우 strict 보관 감사를 통과하지 못한다.

## Codex fresh-context 경계

`scripts/run_fresh_codex_job.py`는 전처리를 지시한 긴 대화의 재해석 비용을 줄이기 위한
문맥 격리 장치다. 콘텐츠와 delivery를 별도 `codex exec --ephemeral` 세션으로 실행하며 어느
최초 prompt에도 전체 STT, OCR 또는 Snapshot을 복사하지 않고 job 경로와 정책만 넣는다.
콘텐츠 세션만 로컬 근거 청크를 읽고 원문 언어 `minutes.md`와 `content_freeze.json`을 만든다.
명시한 목표 언어가 감지 언어와 다를 때만 그 사이에 저추론 번역 세션 하나를 추가한다. 번역
prompt에는 동결 Markdown만 포함하고 원시 근거·감사·품질 검토 파일을 주지 않으며, 도구 호출과
재요약·재분석·추가 모델 검토를 금지한다. 결과는 `translation_manifest.json`으로 구조·보호값과
원문/번역 SHA-256을 묶는다. delivery 세션은 freeze, 검증된 최종 Markdown, blueprint, DOCX와
페이지 렌더만 읽으며 원시 근거 파일 접근을 금지한다.
content와 delivery의 운영 규칙은 compact prompt에 선주입하며 worker가 전체 `SKILL.md`나
reference를 다시 열지 못하게 한다. launcher는 command output과 file-change diff를 모두
model-visible output으로 계측한다. 한 항목이 20KB를 넘거나 금지한 지침 파일 읽기가 감지되면
해당 Codex process를 즉시 종료하고 위반 명령의 제한된 preview와 SHA-256만 job-local handoff에
남긴다.
20KB를 넘은 결과를 잘라 모델에 제공하거나 작업을 계속하지 않는다. evidence chunk는 part 전체를
정확히 한 번만 읽으며 같은 part 경로의 두 번째 명령도 즉시 종료한다. 이로써 누적 문맥 재전송과
API prompt-cache 압력을 줄이면서 근거 누락은 content freeze에서 실패하도록 유지한다.
Documents plugin은 fresh worker에서만 비활성화한다. 설치된 renderer 파일은 삭제·변경하지
않고 `render_docx_checked.py`가 직접 선택하므로 Word 렌더 검증 경로는 유지된다.
`fresh_codex_handoff.json`에는 근거 파일의 크기·SHA-256과 Snapshot 수,
`parent_conversation_inherited=false`, `raw_evidence_embedded_in_handoff=false`를 기록한다.
launcher는 각 세션의 `codex exec --json` stdout을 권한 0600인 단계별 JSONL로
분리해 스트리밍하고 stderr도 별도 제한 파일로 보존한다. 콘솔에는 단계 완료, 제한된 오류
요약과 token 사용량만 표시한다. handoff에는 단계별 event 크기·해시, phase checkpoint,
tool/item 수와 `turn.completed.usage`를 기록한다.

macOS Codex seatbelt 안에서는 중첩 Codex app-server 초기화가 운영체제 정책으로 차단되므로
launcher 명령 자체는 처음부터 sandbox escalation이 필요하다. 재사용 권한은
`./scripts/run_fresh_codex_job.py`의 정확한 prefix로 제한한다. 이 outer launcher는 설정된
jobs root의 직계 job만 허용하며, 새 worker에는 다시 `workspace-write`를 적용하고 repo와
설정된 `MINUTES_HOME`만 쓰기 경로로 전달한다. child sandbox를 해제하지 않는다.

이 경계는 Codex provider로부터 데이터를 숨기는 보안 기능이 아니다. 콘텐츠 Codex 세션은 문서
생성을 위해 로컬 job의 전체 STT·OCR과 필요한 Snapshot을 도구로 읽으므로, 그 내용은 사용자가
선택한 Codex LLM provider에 노출될 수 있다. translation과 delivery 세션에는 그 근거를 다시 제공하지 않는다.
다만 다른 외부 서비스로 원문을 넘기지 않으며,
공식 자료 검색에는 앞 절의 일반화된 검색어 제한을 그대로 적용한다. 실행은 ephemeral이라
세션 기록을 새로 영구 저장하지 않지만, 로컬 job과 최종 산출물의 기존 보존 정책은 유지한다.

## 자동 처리의 화자 정책

`scripts/process_file.py`가 허용하는 값은 `SPEAKER_ATTRIBUTION_MODE=off|evidence`뿐이다.
기본 `evidence` 모드는 Whisper 구간 타임스탬프 STT, 시간별 OCR, 필요한 소수 Snapshot만
최종 LLM에 전달한다. 화면 근거가 없으면 자기소개, 직접 호명과 응답, 발언 인계 같은 명시적
STT 근거만 사용할 수 있다. 근거가 부족하거나 충돌하면 `화자 미상`으로 남기며, 화자명을
확정하지 못했다는 이유로 발언 내용을 삭제하지 않는다.

Silero VAD는 발화 존재 검증에만 사용한다. VAD 확률은 사람을 식별하거나 발화를 사람별로
군집화할 수 없으며, 화자 이름·번호를 만드는 근거로 사용해서는 안 된다. ECAPA,
SpeechBrain, pyannote 또는 다른 로컬 음향 화자분리는 표준 경로와 독립 실험 경로 모두에서
실행하지 않는다. `audio`, `hybrid`, `SPEAKER_ATTRIBUTION_REQUIRED=true`, 강제 화자 수와
자동 화자분리 재시도는 설정 및 실행 경계에서 거부한다.

`speech_activity.json`은 명백한 비음성 구간과 겹친 STT 구간을 검토 대상으로 표시할 수
있지만 원문을 삭제·수정하지 않는다. 자동 job의 `process_metrics.json`에 `diarize` 또는
`attribute_speakers` 단계가 있으면 정책 위반이다.

## Community-1 gated 모델 거버넌스

`pyannote.audio` Python 라이브러리의 오픈소스 라이선스와
`pyannote/speaker-diarization-community-1` 모델 저장소의 gated 접근 조건은 별개로 관리한다.
이 프로젝트는 Community-1을 기본 처리에 사용하거나 자동 다운로드하지 않는다. 현재
`SPEAKER_ATTRIBUTION_MODE`도 `off|evidence`만 허용하므로 governance 검사가 준비 상태를
반환하더라도 자동 화자분리 단계가 활성화되지는 않는다.

회사에서 검토할 때는 개인 계정 대신 회사 관리 Hugging Face 계정으로 조건을 수락하고 다음
승인 artifact를 `COMMUNITY1_APPROVAL_PATH`에 둔다.

- 정확한 모델 ID와 운영 계약상 검증할 모델 라이선스
- 수락 당시 gated 조건 본문의 SHA-256과 화면 캡처 경로
- timezone이 포함된 수락 시각, 회사 소유자, 승인 용도, 공식 모델 URL
- 수락한 Hugging Face 사용자 식별자는 승인 원본에만 두고 일반 상태 출력에는 노출하지 않음

승인 뒤 별도 provisioning 절차가 회사 관리 저장소에 모델을 반입해야 한다. 런타임은 다음이
있는 `COMMUNITY1_MODEL_DIR`만 인정한다.

- `main`, `latest`, `HEAD`가 아닌 immutable revision
- 승인 artifact SHA-256과 일치하는 `model_manifest.json`
- 모델 카드와 attribution 파일
- mirror에 포함한 모든 파일의 상대 경로·크기·SHA-256

검사는 경로 탈출, 누락·변조 파일, token/secret 유사 필드, 승인 해시 불일치를 거부한다.
job 환경에서는 `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_TOKEN`을 전달하지 않고
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `PYANNOTE_METRICS_ENABLED=0`을 강제한다.

```bash
.venv/bin/python -m scripts.community1_governance \
  --approval ~/minutes/governance/pyannote-community1-approval.json \
  --model-dir ~/minutes/models/pyannote-community1
```

이 명령은 읽기 전용이며 모델 다운로드나 네트워크 호출을 하지 않는다. 승인 artifact가 없거나
유효하지 않으면 `disabled_by_governance`, 승인됐지만 해시 검증된 mirror가 없으면
`skipped_unavailable`, 두 조건이 모두 맞을 때만 `ready_offline`을 반환한다.

공식 기준은 [pyannote.audio MIT 라이선스](https://github.com/pyannote/pyannote-audio/blob/main/LICENSE)와
[Community-1 모델 카드·gated 조건](https://huggingface.co/pyannote/speaker-diarization-community-1/blob/main/README.md)을
각각 확인한다. 모델 카드는 Community-1을 CC BY 4.0으로 표시하며, gated 조건은 세부정보 제공과
pyannote 관련 안내 이메일 가능성을 별도로 명시한다. 조건 변경 가능성 때문에 수락 당시 본문과
해시를 승인 artifact에 고정한다.

## 허용된 Silero ONNX 구성

표준 처리에는 다음 최소 구성만 허용한다.

| 구성요소 | 고정값 | 라이선스 | 용도 |
| --- | --- | --- | --- |
| Silero VAD | `6.2.1` | MIT | 발화 존재 검증 |
| ONNX Runtime | `1.27.0` | MIT | CPU 단일 스레드 ONNX 추론 |

`~/minutes/models/silero-vad-6.2.1/`에는 아래 두 일반 파일만 허용한다. 디렉터리와 파일
심볼릭 링크, 추가 파일, 크기·해시·manifest 불일치를 모두 거부한다.

| 파일 | 크기 | SHA-256 |
| --- | ---: | --- |
| `silero_vad.onnx` | 2,327,524 bytes | `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` |
| `manifest.json` | 가변 | `scripts/vad_security.py`의 canonical manifest와 필드별 비교 |

모델 원본은 PyPI의 고정 wheel `silero_vad-6.2.1-py3-none-any.whl`에서 정확히
`silero_vad/data/silero_vad.onnx` 한 파일만 추출한다. wheel 크기는 9,146,242 bytes,
SHA-256은 `09de93c4d874bb19c53e62a47dd38be5f163cedad2b5599583231f2a84ef79cb`로
고정한다.

## 로딩과 오프라인 정책

- `torch.hub.load()`와 Silero 저장소의 Python 코드를 실행하지 않는다.
- `.pkl`, `.joblib`, `.ckpt`, `.pt`, Python, YAML 또는 사용자 업로드 모델을 로드하지 않는다.
- 런타임은 검증한 ONNX 파일을 `CPUExecutionProvider`로만 열며 intra-op과 inter-op을 각각
  1 thread로 고정하고 spinning을 끈다.
- 모델 준비 단계에서 wheel과 ONNX의 크기·SHA-256을 모두 검증하고, staging 디렉터리에서
  실제 ONNX 추론을 성공시킨 뒤 최종 경로로 원자적으로 이동한다.
- 런타임에는 downloader, 모델 저장소 ID, 토큰 입력 또는 네트워크 호출 경로가 없다.
- 모델 또는 ONNX Runtime이 없거나 무결성 검사에 실패하면 VAD만 명시적으로 `skipped`로
  기록한다. STT, OCR과 문서 생성은 계속하며 조용한 fallback은 허용하지 않는다.

모델은 영상마다 다운로드하지 않는다. 새 환경에서 한 번 준비한 후 모든 job이 같은 검증된
파일을 재사용한다.

```bash
source .venv/bin/activate
python scripts/prepare_vad_model.py
python scripts/prepare_vad_model.py --status
```

첫 번째 명령의 최초 실행만 고정된 PyPI HTTPS URL에 접속한다. 영상·음성·STT·OCR은 읽거나
전송하지 않는다. 이후 처리와 `--status`는 네트워크 없이 동작한다.

Whisper 모델 캐시도 영상별로 생성되지 않는다. `mlx-whisper`가 사용하는 Hugging Face
캐시는 모델 blob을 공유한다. `HF_HUB_OFFLINE=1`이면 캐시에 없는 모델을 다운로드하거나
리비전을 확인하지 않으며 영상·음성 자체를 Hugging Face로 전송하지 않는다. 더 강한
재현성이 필요하면 검토한 MLX Whisper Snapshot을 로컬 디렉터리로 보관하고
`WHISPER_MODEL`에 그 경로를 지정한다.

PyTorch는 현재 `mlx-whisper==0.4.3`의 선언된 의존성이므로 환경에 유지한다. VAD나
화자분리 용도로 사용하지 않으며, 이 프로젝트는 PyTorch checkpoint를 로드하지 않는다.

## 패키지 무결성과 취약점 점검

현재 Python 환경의 알려진 취약점을 점검하려면 다음을 실행한다. 이 명령은 패키지
메타데이터를 취약점 서비스와 비교할 때만 네트워크를 사용하며 영상 데이터는 읽지 않는다.

```bash
pip install -r requirements-security.txt
mkdir -p security-reports
pip-audit --local
pip-audit --local --format cyclonedx-json --output security-reports/python-sbom.cdx.json
```

FFmpeg와 Tesseract는 Python SBOM에 포함되지 않으므로 Homebrew 설치 버전과 보안 업데이트를
별도로 확인한다.

```bash
brew outdated ffmpeg tesseract tesseract-lang
brew list --versions ffmpeg tesseract tesseract-lang
```

Silero VAD에는 공개 SECURITY 정책이 없다. 공개 advisory가 없다는 사실을 독립 보안 검증으로
해석하지 않는다. 모델과 runtime을 갱신할 때는 공식 릴리스·라이선스·보안 자료를 다시
확인하고, wheel 및 ONNX 크기와 해시를 새로 기록한 뒤 보안·기능 회귀 테스트를 통과시킨다.

- [Silero VAD v6.2.1](https://github.com/snakers4/silero-vad/releases/tag/v6.2.1)
- [Silero VAD security policy 상태](https://github.com/snakers4/silero-vad/security/policy)
- [ONNX Runtime thread 관리](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)
