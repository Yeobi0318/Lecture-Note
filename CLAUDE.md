# Lecture Note (lecturekit) 개발 메모 (Claude Code용)

강의 영상 → 로컬 AI 정리 노트(.md/.docx/.pdf) + 중요 장면 캡처(attach) + Claude 위임용 자료. 사용자용 설명은 README.md.

## 작업 원칙 (반드시 지킬 것)
- 사용자 대상 문구(GUI, README, 결과 파일)는 **한국어**. 사용자는 명령줄에 익숙하지 않다고 가정 → 사용법은 GUI 클릭 기준.
- 사용법은 프로그램 안(`gui.py`의 `HELP_TEXT`)과 `README.md`에 항상 최신으로. **수정하면 둘 다 갱신.**
- 검증하지 않은 것은 "검증 못 함"이라고 밝힘.
- 실행 파일은 **`START.bat` 하나만**. `.bat`는 **ASCII 영문 + CRLF** (한글 넣으면 cmd 인코딩 문제, `.gitattributes`로 CRLF 고정).
- 사용자가 결과 폴더의 `attach\`에 자기 파일을 저장할 수 있음 → **attach를 통째로 지우지 말 것** (`output._clean_attach`는 `NN_*.jpg`만 지움).
- 노트 규칙: 통계 수치는 인식된 대로(큰 오류·논리 모순만 예외), 강사 말실수·불일치는 표시 없이 문맥으로 바로잡음. `analyze.SYSTEM`, `review` 프롬프트, `output.PROMPT`에 반영.
- 배포: 버전을 올리면 GitHub Release에 설명과 배포 zip(`tools/make_release.py`)을 함께 올림.
- 이 PC 전용 정보(사용자 환경·경로)는 `CLAUDE.local.md`(공개 안 함)에 있음.

## 개발 PC에서 측정한 환경
RTX 2060 6GB 노트북, Windows 11, Python 3.11.9, torch 2.6.0+cu124. Ollama `qwen3.5:4b`(Q4_K_M, 3.3GB)가 VRAM에 전부 적재(num_ctx 8192에서 GPU 약 4.9GB).

## 구조
```
lecturekit/
  gui.py         tkinter GUI. 분석은 `python -u -m lecturekit.cli ...` 하위 프로세스로 실행
                 출력 규약: @@P <0~1> <설명> 진행률 · @@R <json> 영상 1개 결과 · @@F <json> export가 만든 파일
  cli.py         run, check, sysinfo, ai-setup [--auto], ai-status, review-md, export, audio, presets, cer-draft, cer-score
  pipeline.py    Options, process_video(): 화면(_screen, 캐시) → 전사본 → 전사본 교정 → 로컬 AI 노트 → 노트 검토 → 결과 → Word/PDF
  slides.py      화면 전환 감지(160x90 흑백 비교), 대표 프레임 t_last
  ocr.py         EasyOCR, 장면 묶기 group_scenes, unreliable_score(AI 없을 때 장면 선택 규칙)
  transcript.py  SRT/VTT/타임스탬프 TXT/클로바노트 TXT·DOCX 읽기, 같은 이름 전사본 찾기(영상이름_audio.* 포함)
  speech.py      faster-whisper (클로바노트 전사본이 없을 때만)
  ai.py          Ollama HTTP 클라이언트(urllib만 사용): 설치(OllamaSetup.exe /VERYSILENT), pull, chat(think=False, JSON schema)
  analyze.py     로컬 AI: 부분별 정리(≈3,500자) → 후보 장면 이미지 확인 → 전체 개요. _work/ai_cache.json 캐시
  review.py      로컬 AI 검토·교정: 전사본 교정, 노트 검토(원문 대조 + 전체 일관성), 아무 .md 검토. 검토_기록.md
  sysinfo.py     PC 사양(GPU·VRAM·RAM·CPU·디스크) 분석, 모델별 적합도, 추천 (MODELS 표가 모델 목록의 원본)
  output.py      attach 복사, 노트.md, Claude용_자료.md, review.html, result.json
tools/make_release.py  배포 zip(커밋된 파일만, 0644) + GitHub Release 업로드 (Git Credential Manager 토큰)
  docs.py        md → docx(python-docx) / pdf(Edge headless 인쇄, 실패 시 Word COM)
  sections.py    구간 나누기·문단·화면 글자 중복 제거 (output·analyze 공용)
  cer.py tools.py check.py common.py
```
결과 폴더 `<영상>_notes\`: `<영상>_노트.md/.docx/.pdf`, `검토_기록.md`, `attach\NN_hh-mm-ss.jpg`, `Claude용_자료.md`, `review.html`, `transcript.srt`, `result.json`, `_work\`(slides\, screen.json, ai_cache.json, ai_result.json).

## 핵심 설계 결정과 근거
| 결정 | 근거 |
|---|---|
| 로컬 AI = Ollama + qwen3.5:4b | 6GB VRAM에 전부 올라가고 이미지 입력 가능. 측정: 300토큰 답 약 2초(모델 적재 후), 첫 적재 포함 첫 응답 약 90초. 한국어 비교 벤치마크는 찾지 못함 |
| AI 작업을 작은 조각으로 | 4B 모델은 긴 입력에서 내용 누락. 3,500자 조각별 JSON(소제목·핵심·표·강조·확인 필요·장면 점수) → 코드가 노트를 조립. 표는 JSON 구조로 받아 깨지지 않게 함 |
| 장면 선택 = 텍스트 점수 + 이미지 점수 | 조각 정리 때 장면별 0~3점 → 2점 이상 후보(최대 3×max_images)만 AI가 이미지를 보고 0~10점·종류. 최종 = 0.4×텍스트(10점 환산) + 0.6×이미지 + 화면 글자량 가산(150자 +1, 300자 +1.5) − 제목·인물 3점. 6점 이상 중 상위 max_images, 부분별 상한(⌈1.5×max/부분 수⌉). 처음엔 0~3점 척도였는데 거의 모두 2~3점에 몰려 동점→앞쪽 장면만 뽑히고 핵심 정리 슬라이드가 빠졌음(실측) |
| 강조 내용 | 부분별 `emphasis`를 그대로 모으면 단어 조각 45개가 됨(실측) → 개요 단계에서 AI가 5~10문장으로 합침 |
| 숫자·단위 띄어쓰기 | qwen이 "6 가지", "40 초"처럼 씀 → `analyze._fix`로 교정(뒤에 한글 단어가 이어지면 그대로: "3 개선") |
| 검토·교정 = 수정 제안만 받기 | 4B 모델에 글을 다시 쓰게 하면 멀쩡한 문장까지 바꿈(실측: "잘 봤으려나?"→"잘 봤어?", "없습니다"→"없어요", 맞는 "평이한"→"평범한", "平易한" 한자 변환). → AI는 (줄, 원래 구절, 고친 구절, 이유)만 제안. 모델이 줄 번호를 비우고 문장 전체를 주는 경우가 많아(실측) 프로그램이 원문에서 줄을 찾고 단어 단위로 쪼갬 |
| 검토 필터 (`review._ok_edit`) | 띄어쓰기·문장부호·어미만 바뀌면 거부, 한자 거부, 단어 추가·삭제 거부, 길이 0.6~1.6배 밖이면 거부(내용 잘라 내기 방지), 여러 곳 고치면 거부. 근거 없으면 숫자 변경 거부·자모 유사도 0.6 미만 거부(잘못 들음·오타만). 모든 수정은 검토_기록.md에 |
| 근거 판정 (`_evidence`) | 고친 쪽에 새로 들어간 **내용어가 모두** 근거에 있고, 원래 표현은 근거에 없을 때만. 흔한 기능어(있/하/때문/경우…)는 근거로 안 침. 노트 검토의 근거는 **음성 전사 문장만**(OCR은 오타가 많아 제외 — 실측: OCR 오타 '되엇는지'를 노트로 옮김). 근거 없는 수정이라도 원래 단어가 원문에 그대로 있으면 거부('STARR'→'STAR' 방지) |
| 재심사 질문 | "원래 글이 확실히 틀렸나? 애매하면 false" → 맞는 오타 수정까지 전부 거부(실측). "원래 단어가 사전에 없는 철자이고 고친 것이 올바른 철자인가?"로 바꿈 → 오타는 통과, 멀쩡한 단어 교체는 거부. 시험: 비젼·접합성·역랑·기범 수정, 평이한 유지, 재무재표는 놓침 |
| 전사본은 근거 있는 수정만 | 재심사를 거쳐도 클로바노트 전사본에서는 맞는 말을 틀리게 바꿈(실측: '수해'→'재해', 'STARR'→'STAR', '충실'→'집중'). 클로바노트는 원래 정확 → `evidence_only=True`. 노트 검토는 재심사 유지(노트에 옮겨진 OCR 오타 '노력올 하석습니까' 등을 바로잡는 효과가 큼) |
| PC 사양별 모델 | 필요 VRAM ≈ 모델 크기×1.15+1.3GB (RTX 2060·4b 실측 4.9GB). GPU에 전부 들어가는 가장 큰 모델 추천, 없으면 2b(CPU). setup.bat은 `ai-setup --auto` |
| GPU 순서 | Whisper·EasyOCR이 끝나고 메모리를 비운 뒤 Ollama 사용, 끝나면 `keep_alive=0`으로 내림 |
| 화면 분석 캐시 | `_work/screen.json` (영상 크기·수정시각·민감도·영역이 같으면 재사용) → AI 설정만 바꿔 다시 돌릴 때 빠름 |
| 화면 글자 정확도는 OCR이 아니라 이미지 첨부로 | 실측: EasyOCR 한국어 CER 약 12.7%, 설정 조정으로 받침 오류가 줄지 않음. Claude는 같은 이미지를 정확히 읽음 |
| 장면 묶기 `group_scenes(sim=0.42)` | 같은 문서에 강조만 바뀐 경우 유사도 0.45~0.93, 다른 문서는 0.15 이하 (실측) |
| 전환 감지 `change=0.01`, `stable=0.004` | 같은 화면 0%, 글자만 다른 전환 2~3% (합성 영상 실측) |
| 클로바노트 타임스탬프 | 줄 맨 앞(`[00:05] …`) 또는 짧은 줄 끝(`Attendees 1 00:05`, `참석자 1 00:05`)만 인정. `오전/AM` 시각, 날짜, 본문 속 시각은 제외 |
| PDF = Edge headless | Windows 11 기본 탑재, 한글 글꼴·표·그림 안정. `--user-data-dir` 임시 폴더로 실행 중인 Edge와 충돌 방지 |
| ffmpeg | 모든 호출에 `-nostdin`, `stdin=DEVNULL`, Windows `CREATE_NO_WINDOW` |
| setup.bat | winget 사용 안 함(사용자 PC 실패 이력). torch+torchvision을 cu124 인덱스에서 함께 설치. 마지막에 `cli ai-setup`(실패해도 계속) |

## 검증 상태 (2026-10-24, 개발 PC에서 직접 실행)
- Ollama 자동 설치·모델 내려받기·응답: 성공.
- 60분 실제 강의(취업 교육 강의) + 클로바노트 전사본 전체 실행: 화면 130장 → 장면 103개, AI 10부분 정리,
  후보 36장 이미지 확인 → 캡처 12장, 노트·Word·PDF(14쪽) 생성. **처음 실행 9.2분**, 캐시 재사용 시 0.5~1.4분.
  캡처 12장은 질의응답 유형·기본 질문 목록·Q3~Q4 정리·STARR 표·구조화 답변 예시·경험면접 요약 등 (퀴즈·단원 제목 제외됨).
- 클로바노트 TXT(`Attendees 1 00:05`) 108개 타임스탬프 전부 인식, DOCX 형식·cp949 TXT는 만든 시험 파일로 확인.
- 다시 분석해도 attach의 사용자 파일 보존 확인.
- GUI 실행·한글 표시·로컬 AI 상태 표시: 화면 캡처로 확인.
- 검증 못 함: 새 PC에서 `START.bat` 처음부터 설치(이 PC는 .venv가 이미 있음), qwen3.5:9b 속도, 클로바노트 .docx 실제 파일(형식 추정으로 구현).

## 개발·테스트
```bat
.venv\Scripts\python.exe -m lecturekit.cli run "영상.mp4" --word --pdf --out-root "다른폴더"
.venv\Scripts\python.exe -m lecturekit.cli export "노트.md"
.venv\Scripts\python.exe -m lecturekit.cli ai-status
.venv\Scripts\python.exe -m lecturekit.gui
```
다시 돌리면 화면 분석(`screen.json`)과 AI 답(`ai_cache.json`)을 재사용하므로 출력 로직만 빠르게 시험할 수 있음.
배포: `python tools/make_release.py v3.1.0 --notes 릴리스설명.md` (zip은 dist\, 저장소 Yeobi0318/Lecture-Note).

## 다음 작업 후보
1. 사용자가 여러 강의로 써 본 뒤 노트 품질 피드백 반영 (조각 크기, 프롬프트, 캡처 임계값 2.0)
2. 여러 영상의 노트를 하나로 합치기, 남은 시간 표시
3. (선택) OCR 엔진 대안(PaddleOCR 등) 측정 — 12.7%보다 확실히 낮을 때만 교체
