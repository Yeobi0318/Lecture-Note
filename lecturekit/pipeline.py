# -*- coding: utf-8 -*-
"""영상 1개 처리 흐름: 화면 감지 → 전사본(클로바노트 또는 음성 인식) → OCR·장면 묶기
→ 로컬 AI 노트·중요 장면 선택 → attach + 노트.md (+ Word/PDF) + Claude용 자료."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .common import TOOL_DIR, gpu_available, hms, log, progress, video_duration
from .transcript import find_external_transcript, load_transcript, write_transcript

SENSITIVITY = {"low": 0.02, "normal": 0.01, "high": 0.005}  # 슬라이드 전환 민감도 → 바뀐 픽셀 비율


@dataclass
class Options:
    use_external: bool = True            # 클로바노트 등 전사본 사용 (영상과 같은 이름이거나 transcript로 지정)
    transcript: str | None = None        # 전사본 파일 직접 지정
    redo_stt: bool = False               # 이전 음성 인식 결과가 있어도 다시 인식
    audio_preset: str = "light"
    model: str = "large-v3"              # 음성 인식(Whisper) 모델
    compute_type: str = "int8_float16"
    terms: str = str(TOOL_DIR / "terms.txt")
    slides: bool = True                  # False: 슬라이드 없는 영상(카메라 촬영)
    sensitivity: str = "normal"
    crop: tuple | None = None            # 화면 분석 영역 (x, y, w, h 비율)
    interval: float = 2.0
    out_root: str | None = None          # None: 영상 옆에 '<영상이름>_notes'
    use_ai: bool = True                  # 로컬 AI로 노트 작성·중요 장면 선택
    ai_model: str = "qwen3.5:4b"
    vision: bool = True                  # 후보 장면 이미지를 AI가 직접 확인
    fix_transcript: bool = True          # 로컬 AI로 전사본의 인식 오류·오탈자 교정
    review_note: bool = True             # 로컬 AI로 노트를 원문과 대조해 오류·모순 검토
    max_images: int = 12                 # 캡처(첨부) 최대 장수
    word: bool = False                   # 노트를 Word로도 저장
    pdf: bool = False                    # 노트를 PDF로도 저장
    extra: dict = field(default_factory=dict)


def out_dir_for(video: Path, opts: Options) -> Path:
    root = Path(opts.out_root) if opts.out_root else video.parent
    return root / f"{video.stem}_notes"


def process_video(video: Path, opts: Options) -> dict:
    from .output import (choose_heuristic, copy_attachments, remove_old_outputs, save_result, write_claude_md,
                         write_note_md, write_review)
    from .sections import build_sections

    video = Path(video).resolve()
    if not video.is_file():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {video}")
    out = out_dir_for(video, opts)
    work = out / "_work"
    work.mkdir(parents=True, exist_ok=True)
    T0 = time.time()
    duration = video_duration(video)
    log(f"영상: {video.name} ({hms(duration)})  →  결과 폴더: {out}")
    try:  # 6GB GPU: 남아 있는 로컬 AI 모델을 내려 OCR·음성 인식에 메모리를 비워 줌
        from .ai import unload_all

        unload_all()
    except Exception:
        pass

    # 1) 화면 + OCR (같은 설정으로 이미 분석했으면 재사용)
    slides, events, groups = _screen(video, work, duration, opts)

    # 2) 전사본
    log("[2/6] 전사본")
    segs, source = _get_transcript(video, out, duration, opts)
    progress(0.45, "전사본 준비 완료")

    from . import ai
    ai_ok = False
    if opts.use_ai:
        try:
            ai.ensure_server()
            ai_ok = ai.has_model(opts.ai_model)
            if not ai_ok:
                log(f"[주의] 로컬 AI 모델 {opts.ai_model}이(가) 없습니다. [PC 분석·로컬 AI 설치]에서 받으세요.")
        except ai.AIError as ex:
            log(f"[주의] 로컬 AI를 쓰지 못했습니다: {ex}")
        if not ai_ok:
            log("  → AI 없이 규칙으로 장면을 고르고, 노트·검토는 만들지 않습니다.")

    # 3) 전사본 교정
    fixes, fix_issues = [], []
    if ai_ok and opts.fix_transcript:
        log("[3/6] 로컬 AI 전사본 검토·교정")
        from .review import correct_transcript

        segs, fixes, fix_issues = correct_transcript(segs, video.name, slides, events, opts.terms, work, opts.ai_model)
        write_transcript(segs, out)  # 교정본 (원본: 클로바노트 파일 또는 _work 안의 transcript_raw.srt)
    progress(0.60, "전사본 검토 완료")

    # 4) 로컬 AI 노트
    ai_res, chosen, captions = None, [], {}
    note_fixes, note_issues = [], []
    if ai_ok:
        log("[4/6] 로컬 AI 정리")
        from .analyze import analyze, choose_by_ai

        try:
            ai_res = analyze(video.name, duration, build_sections(events, segs, duration), slides, groups,
                             work, opts.ai_model, opts.vision, opts.max_images, p0=0.60, p1=0.88)
            chosen = choose_by_ai(ai_res, groups, opts.max_images)
            captions = {g: x.get("caption", "") for g, x in ai_res["scenes"].items()}
            log(f"  → 중요 장면 {len(chosen)}장 선택 (후보 장면 {len(groups)}개 중)")
            if opts.review_note:
                log("  노트 검토: 원문과 대조해 오탈자·사실 오류·앞뒤 모순 확인")
                from .review import review_note

                note_fixes, note_issues = review_note(ai_res, [c["source"] for c in ai_res["chunks"]],
                                                      video.name, work, opts.ai_model)
            ai_res["review_issues"] = [f"{x['where']} — {x['issue']}" if x["where"] else x["issue"]
                                       for x in fix_issues + note_issues]
            (work / "ai_result.json").write_text(json.dumps(ai_res, ensure_ascii=False, indent=1), encoding="utf-8")
        except ai.AIError as ex:
            log(f"[주의] 로컬 AI 정리 실패: {ex}\n  → 규칙으로 장면만 고릅니다.")
            ai_res = None
        finally:
            ai.unload(opts.ai_model)
    elif not opts.use_ai:
        log("[4/6] 로컬 AI 사용 안 함 — 규칙으로 장면 선택")
    if ai_res is None and slides:
        chosen = choose_heuristic(slides, groups, opts.max_images)

    # 5) 결과물
    log("[5/6] 결과 파일 만들기")
    progress(0.96, "결과 파일 만들기")
    remove_old_outputs(out)
    attached, n_parts = copy_attachments(out, slides, chosen, captions)
    note = write_note_md(out, video, duration, ai_res, attached) if ai_res else None
    claude_md, n_blocks = write_claude_md(out, video, slides, events, segs, duration, attached, n_parts)
    write_review(out, video, slides, events, segs, duration, attached)
    review_log = None
    if fixes or fix_issues or note_fixes or note_issues:
        from .review import render_log

        review_log = out / "검토_기록.md"
        review_log.write_text(render_log(f"{video.stem} — 로컬 AI 검토 기록",
                                         [("전사본 교정", fixes, fix_issues), ("노트 검토", note_fixes, note_issues)],
                                         opts.ai_model), encoding="utf-8")

    # 6) Word / PDF
    made = []
    if note and (opts.word or opts.pdf):
        log("[6/6] Word·PDF 만들기")
        from .docs import export

        try:
            made = export(note, opts.word, opts.pdf)
        except Exception as ex:
            log(f"[주의] 문서 변환 실패: {ex}")

    claude_chars = len(claude_md.read_text(encoding="utf-8"))
    result = {
        "video": str(video), "out_dir": str(out), "duration": duration,
        "note": str(note) if note else None,
        "docx": next((str(p) for p in made if p.suffix == ".docx"), None),
        "pdf": next((str(p) for p in made if p.suffix == ".pdf"), None),
        "ai_model": opts.ai_model if ai_res else None,
        "transcript_source": source, "transcript_fixes": len(fixes), "note_fixes": len(note_fixes),
        "review_issues": len(fix_issues) + len(note_issues), "review_log": str(review_log) if review_log else None, "slides": len(slides), "scenes": len(groups), "blocks": n_blocks,
        "images": len(attached), "parts": n_parts, "image_tokens": sum(a["tokens"] for a in attached),
        "claude_md": str(claude_md), "claude_chars": claude_chars,
        "text_tokens_range": [claude_chars // 2, claude_chars],
        "minutes": round((time.time() - T0) / 60, 1),
    }
    save_result(out, result)
    progress(1.0, "완료")
    log(f"\n완료 ✅  {video.name}  ({result['minutes']}분)")
    if note:
        log(f"  노트: {Path(note).name}  · 캡처 {len(attached)}장")
    if review_log:
        log(f"  검토: 전사본 {len(fixes)}곳·노트 {len(note_fixes)}곳 수정, 확인 필요 {result['review_issues']}곳 → {review_log.name}")
    log(f"  Claude에 맡길 때: {claude_md.name} + attach 이미지 {len(attached)}장 "
        f"(예상 토큰 글자 {claude_chars // 2:,}~{claude_chars:,} + 이미지 {result['image_tokens']:,})")
    return result


def _screen(video: Path, work: Path, duration: float, opts: Options):
    """화면 전환 감지 + 슬라이드 OCR + 장면 묶기. 결과는 _work\\screen.json에 저장해 재사용."""
    if not opts.slides:
        log("[1/6] 슬라이드 없는 영상 — 화면 분석 생략 (5분 단위로 정리)")
        return [], [], []
    st = video.stat()
    key = [st.st_size, int(st.st_mtime), opts.interval, opts.sensitivity, list(opts.crop or [])]
    cache = work / "screen.json"
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("key") == key and all(Path(s["image"]).is_file() for s in d["slides"]):
            log(f"[1/6] 이전 화면 분석 결과 재사용 (화면 {len(d['slides'])}장, 장면 {len(d['groups'])}개)")
            progress(0.35, "화면 분석 재사용")
            return d["slides"], [tuple(e) for e in d["events"]], d["groups"]
    except Exception:
        pass

    from .ocr import group_scenes, run_ocr
    from .slides import detect_slides, save_slide_images

    log("[1/6] 화면 전환 감지")
    slides, events = detect_slides(video, duration, opts.interval, SENSITIVITY[opts.sensitivity], 0.004,
                                   opts.crop, p0=0.0, p1=0.18)
    log(f"  → 화면 {len(slides)}장, 전환 {len(events)}회")
    save_slide_images(video, slides, work, duration, p0=0.18, p1=0.22)
    log("  화면 글자 인식(OCR)")
    run_ocr(slides, gpu_available(), p0=0.22, p1=0.35)
    groups = group_scenes(events, slides)
    log(f"  → 비슷한 화면을 묶어 장면 {len(groups)}개")
    for s in slides:
        s["image"] = str(s["image"])
    cache.write_text(json.dumps({"key": key, "slides": slides, "events": events, "groups": groups},
                                ensure_ascii=False), encoding="utf-8")
    return slides, events, groups


def _get_transcript(video: Path, out: Path, duration: float, opts: Options):
    """→ (발화 목록, 출처 설명)"""
    ext = Path(opts.transcript) if opts.transcript else (find_external_transcript(video) if opts.use_external else None)
    if ext:
        if not ext.is_file():
            raise FileNotFoundError(f"전사본 파일을 찾을 수 없습니다: {ext}")
        segs = load_transcript(ext, duration)
        log(f"  클로바노트(외부) 전사본 사용: {ext.name} ({len(segs)}문장)")
        write_transcript(segs, out)
        return segs, f"외부 전사본 {ext.name}"
    if opts.use_external:
        log("  [주의] 클로바노트 전사본이 없어 내 PC에서 음성 인식을 합니다.\n"
            "  (영상과 같은 폴더에 같은 이름의 .txt/.docx를 두거나 [전사본 지정]으로 고르세요)")
    raw = out / "_work" / "transcript_raw.srt"  # 교정 전 음성 인식 원본
    if raw.exists() and not opts.redo_stt:
        log("  이전 음성 인식 결과 재사용 — 새로 인식하려면 '음성 인식 다시 하기' 선택")
        return load_transcript(raw, duration), "이전 음성 인식 재사용"
    from .speech import Transcriber, extract_audio, load_terms

    wav = out / "_work" / "audio_16k.wav"
    extract_audio(video, wav, opts.audio_preset)
    tr = Transcriber(opts.model, opts.compute_type, load_terms(opts.terms))
    segs = tr(wav, 0.35, 0.45)
    tr.close()
    wav.unlink(missing_ok=True)
    write_transcript(segs, out)
    write_transcript(segs, out / "_work", name="transcript_raw.srt")
    return segs, f"내 PC 음성 인식 ({opts.model})"
