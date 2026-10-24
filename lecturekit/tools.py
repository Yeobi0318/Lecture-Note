# -*- coding: utf-8 -*-
"""보조 기능: 음성 전처리 비교, 외부 서비스용 오디오 추출."""
from __future__ import annotations

import html
from pathlib import Path

from .common import TOOL_DIR, hms, log, progress
from .speech import AUDIO_PRESETS, PRESET_DESC, Transcriber, export_m4a, extract_audio, load_terms


def compare_presets(video: Path, out_dir: Path, start_min: float, length_min: float = 5.0,
                    model: str = "large-v3", terms: str = str(TOOL_DIR / "terms.txt")) -> Path:
    """영상 일부(기본 5분)를 전처리 3종으로 각각 인식해 나란히 비교하는 HTML 생성"""
    out_dir.mkdir(parents=True, exist_ok=True)
    tr = Transcriber(model, "int8_float16", load_terms(terms))
    cols = {}
    for k, name in enumerate(AUDIO_PRESETS, 0):
        wav = out_dir / f"compare_{name}.wav"
        log(f"  [{name}] {PRESET_DESC[name]} — 인식 중…")
        extract_audio(video, wav, name, start=start_min * 60, length=length_min * 60)
        cols[name] = tr(wav, k / 3, (k + 1) / 3)
        wav.unlink(missing_ok=True)
    tr.close()
    e = html.escape
    tds = "".join("<td>" + "".join(f"<p><b>{hms(s['start'] + start_min * 60)}</b> {e(s['text'])}</p>"
                                   for s in segs) + "</td>" for segs in cols.values())
    ths = "".join(f"<th>{n}<br><small>{e(PRESET_DESC[n])}</small></th>" for n in cols)
    path = out_dir / "compare_audio.html"
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>음성 전처리 비교</title>"
        "<style>body{font:15px/1.6 'Malgun Gothic',sans-serif;margin:16px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;vertical-align:top;padding:8px;width:33%}small{color:#888;font-weight:400}</style>"
        f"<h1>음성 전처리 비교 ({hms(start_min * 60)}부터 {length_min:g}분)</h1>"
        "<p>전문용어와 조사가 가장 정확한 열을 골라, 다음 분석 때 '음성 전처리'에서 선택하세요.</p>"
        f"<table><tr>{ths}</tr><tr>{tds}</tr></table>", encoding="utf-8")
    progress(1.0, "완료")
    log(f"\n비교 결과: {path}")
    return path


def export_audio(video: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{video.stem}_audio.m4a"
    export_m4a(video, out)
    log(f"오디오 추출 완료: {out}")
    return out
