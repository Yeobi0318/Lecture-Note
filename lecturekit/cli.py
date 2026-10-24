# -*- coding: utf-8 -*-
"""명령줄 실행 (GUI가 내부적으로 사용. 직접 쓸 때: python -m lecturekit.cli 명령 ...)

  run 영상1 [영상2 ...] [--transcript 전사본] [--no-external] [--no-ai] [--ai-model qwen3.5:4b]
                         [--max-images 12] [--word] [--pdf] [--redo-stt] ...
  check
  sysinfo                        PC 사양 분석 + 로컬 AI 모델 추천
  ai-setup [--model 이름|--auto] 로컬 AI(Ollama) 설치 + 모델 내려받기 (--auto: PC에 맞는 모델)
  review-md 문서.md [--model ...] 로컬 AI로 아무 .md 파일의 오탈자·모순 검토 → _검토본.md
  ai-status [--model ...]        로컬 AI 상태 한 줄 출력
  export 노트.md [--no-word] [--no-pdf]   마크다운 → Word/PDF
  audio 영상                     외부 서비스 업로드용 .m4a 추출
  presets 영상 --start 30        음성 전처리 3종 비교
  cer-draft 전사본 --start 30:00 --end 40:00 [--out 파일]
  cer-score 정답 전사본1 [전사본2 ...] --start 30:00 --end 40:00 [--out 파일]
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .common import TOOL_DIR, log, parse_clock


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lecturekit", description="강의 영상 정리 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="영상 분석")
    r.add_argument("videos", nargs="+")
    r.add_argument("--transcript", help="전사본(클로바노트 .txt/.docx, .srt) 직접 지정 (영상 1개일 때)")
    r.add_argument("--no-external", action="store_true", help="같은 이름의 전사본이 있어도 내 PC에서 음성 인식")
    r.add_argument("--no-ai", action="store_true", help="로컬 AI를 쓰지 않음 (규칙으로 장면 선택, 노트 없음)")
    r.add_argument("--ai-model", default="qwen3.5:4b")
    r.add_argument("--no-vision", action="store_true", help="후보 장면 이미지를 AI가 직접 확인하지 않음 (빠름)")
    r.add_argument("--no-fix-transcript", action="store_true", help="로컬 AI 전사본 교정 생략")
    r.add_argument("--no-review", action="store_true", help="로컬 AI 노트 검토 생략")
    r.add_argument("--max-images", type=int, default=12)
    r.add_argument("--word", action="store_true", help="노트를 Word(.docx)로도 저장")
    r.add_argument("--pdf", action="store_true", help="노트를 PDF로도 저장")
    r.add_argument("--redo-stt", action="store_true")
    r.add_argument("--preset", default="light", choices=["none", "light", "denoise"])
    r.add_argument("--model", default="large-v3")
    r.add_argument("--no-slides", action="store_true")
    r.add_argument("--sensitivity", default="normal", choices=["low", "normal", "high"])
    r.add_argument("--crop", help="x,y,w,h (0~1 비율)")
    r.add_argument("--out-root")

    sub.add_parser("check", help="설치·GPU 점검")
    sub.add_parser("sysinfo", help="PC 사양 분석 + 모델 추천")
    g = sub.add_parser("ai-setup", help="로컬 AI 설치 + 모델 내려받기")
    g.add_argument("--model", default=None)
    g.add_argument("--auto", action="store_true", help="PC 사양에 맞는 모델 자동 선택")
    m = sub.add_parser("review-md", help="로컬 AI로 .md 파일 검토·교정")
    m.add_argument("md")
    m.add_argument("--model", default="qwen3.5:4b")
    g = sub.add_parser("ai-status", help="로컬 AI 상태")
    g.add_argument("--model", default="qwen3.5:4b")
    x = sub.add_parser("export", help="마크다운 노트 → Word/PDF")
    x.add_argument("md")
    x.add_argument("--no-word", action="store_true")
    x.add_argument("--no-pdf", action="store_true")
    a = sub.add_parser("audio", help="업로드용 오디오 추출")
    a.add_argument("video")
    a.add_argument("--out-root")
    p = sub.add_parser("presets", help="음성 전처리 비교")
    p.add_argument("video")
    p.add_argument("--start", type=float, required=True, help="시작 지점(분)")
    p.add_argument("--length", type=float, default=5.0)
    p.add_argument("--out-root")
    d = sub.add_parser("cer-draft", help="정답 초안 만들기")
    d.add_argument("transcript")
    d.add_argument("--start", required=True)
    d.add_argument("--end", required=True)
    d.add_argument("--out")
    s = sub.add_parser("cer-score", help="정답과 비교해 CER 계산")
    s.add_argument("reference")
    s.add_argument("hyps", nargs="+")
    s.add_argument("--start", required=True)
    s.add_argument("--end", required=True)
    s.add_argument("--out")
    args = ap.parse_args(argv)

    try:
        return _dispatch(args)
    except Exception as ex:
        _report(ex)
        return 1


EXPECTED = (ValueError, FileNotFoundError, RuntimeError)  # AIError도 RuntimeError


def _report(ex, name=""):
    """예상된 오류는 메시지만, 예상 못 한 오류는 추적 정보까지 출력"""
    log(f"[오류] {name + ': ' if name else ''}{ex}")
    if not isinstance(ex, EXPECTED):
        log(traceback.format_exc(limit=4))


def _clock(v: str, name: str) -> float:
    t = parse_clock(v)
    if t is None:
        raise ValueError(f"{name} 시각 형식이 잘못되었습니다: {v} (예: 30:00 또는 1:05:00)")
    return t


def _dispatch(args) -> int:
    if args.cmd == "check":
        from .check import check

        return check()

    if args.cmd == "run":
        from .pipeline import Options, process_video

        crop = None
        if args.crop:
            crop = tuple(float(v) for v in args.crop.split(","))
            if len(crop) != 4 or not all(0 <= v <= 1 for v in crop):
                raise ValueError("분석 영역 형식: x,y,폭,높이 (각 0~1). 예: 0,0,0.75,1")
        if args.transcript and len(args.videos) > 1:
            raise ValueError("--transcript는 영상 1개일 때만 쓸 수 있습니다.")
        opts = Options(use_external=not args.no_external, transcript=args.transcript,
                       redo_stt=args.redo_stt, audio_preset=args.preset, model=args.model,
                       slides=not args.no_slides, sensitivity=args.sensitivity, crop=crop,
                       out_root=args.out_root, use_ai=not args.no_ai, ai_model=args.ai_model,
                       vision=not args.no_vision, max_images=max(1, args.max_images),
                       fix_transcript=not args.no_fix_transcript, review_note=not args.no_review,
                       word=args.word, pdf=args.pdf)
        failed = 0
        for k, v in enumerate(args.videos, 1):
            log(f"\n===== [{k}/{len(args.videos)}] {Path(v).name} =====")
            try:
                res = process_video(Path(v), opts)
                print("@@R " + json.dumps(res, ensure_ascii=False), flush=True)
            except Exception as ex:
                failed += 1
                _report(ex, Path(v).name)
        return 1 if failed else 0

    if args.cmd == "sysinfo":
        from .sysinfo import analyze_pc, recommend, report

        pc = analyze_pc()
        log(report(pc))
        print("@@SYS " + json.dumps({"pc": pc, "recommend": recommend(pc)[0]}, ensure_ascii=False), flush=True)
        return 0

    if args.cmd == "ai-setup":
        from .ai import setup

        model = args.model
        if args.auto or not model:
            from .sysinfo import analyze_pc, recommend, report

            pc = analyze_pc()
            log(report(pc) + "\n")
            model = recommend(pc)[0]
        setup(model)
        print("@@MODEL " + model, flush=True)
        return 0

    if args.cmd == "review-md":
        from .review import review_markdown

        md = Path(args.md)
        if not md.is_file():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {md}")
        out, rep, n = review_markdown(md, args.model)
        print("@@F " + json.dumps([str(out), str(rep)], ensure_ascii=False), flush=True)
        return 0

    if args.cmd == "ai-status":
        from .ai import status

        code, msg = status(args.model)
        print(f"@@AI {code} {msg}", flush=True)
        return 0

    if args.cmd == "export":
        from .docs import export

        md = Path(args.md)
        if not md.is_file():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {md}")
        made = export(md, not args.no_word, not args.no_pdf)
        print("@@F " + json.dumps([str(p) for p in made], ensure_ascii=False), flush=True)
        return 0

    if args.cmd == "audio":
        from .tools import export_audio

        v = Path(args.video)
        export_audio(v, Path(args.out_root) if args.out_root else v.parent)
        return 0

    if args.cmd == "presets":
        from .tools import compare_presets

        v = Path(args.video)
        out = (Path(args.out_root) if args.out_root else v.parent) / f"{v.stem}_notes"
        compare_presets(v, out, args.start, args.length)
        return 0

    if args.cmd == "cer-draft":
        from .cer import draft

        t = Path(args.transcript)
        draft(t, _clock(args.start, "시작"), _clock(args.end, "끝"),
              Path(args.out) if args.out else t.with_name("reference_draft.txt"))
        return 0

    if args.cmd == "cer-score":
        from .cer import score

        ref = Path(args.reference)
        score(ref, args.hyps, _clock(args.start, "시작"), _clock(args.end, "끝"),
              Path(args.out) if args.out else ref.with_name("cer_report.html"))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
