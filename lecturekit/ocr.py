# -*- coding: utf-8 -*-
"""슬라이드 글자 인식(OCR)과 비슷한 장면 묶기.

측정 결과 메모 (1280x720 강의 슬라이드 1장, 정답 문장 대비 문자 오류율):
  EasyOCR 기본 12.7% · 2배 확대 12.1% · mag_ratio=2 12.7% · beamsearch 56%
  → 설정으로는 한국어 받침 오류(을→올, 를→름, 했→햇)가 거의 줄지 않음.
  그래서 OCR은 '장면 묶기·중복 제거·텍스트 전용 모드'에 쓰고,
  정확한 판독이 필요한 화면은 원본 이미지를 Claude가 직접 읽도록 첨부한다(output.py).
"""
from __future__ import annotations

import gc
import re

import numpy as np

from .common import char_ngrams, jaccard, log, progress, text_key, useful_line

MIN_BOX_CONF = 0.3
MATH_CHARS = "=∫∑Σ∂√±×÷≤≥≈∞πθωλμΩαβγΔ^"


def _group_lines(res):
    """글자 상자들을 위→아래, 왼→오른 순서의 줄로 묶음 → [(줄, 평균 신뢰도)]"""
    items = []
    for box, tx, c in res:
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        items.append((min(ys), max(ys), min(xs), tx, c))
    items.sort(key=lambda r: (r[0], r[2]))
    lines, cur, ref_bottom = [], [], 0.0

    def flush():
        cur.sort()
        lines.append((" ".join(t for _, t, _ in cur), sum(c for _, _, c in cur) / len(cur)))

    for top, bot, x, tx, c in items:
        if cur and (top + bot) / 2 > ref_bottom:
            flush()
            cur = []
        if not cur:
            ref_bottom = bot
        cur.append((x, tx, c))
    if cur:
        flush()
    return lines


def run_ocr(slides, use_gpu: bool, p0: float = 0.80, p1: float = 0.95):
    import easyocr
    from PIL import Image

    log(f"  OCR 모델 로딩 (GPU={use_gpu}) — 처음 실행 시 모델을 내려받습니다")
    reader = easyocr.Reader(["ko", "en"], gpu=use_gpu, verbose=False)
    for k, s in enumerate(slides, 1):
        img = np.array(Image.open(s["image"]).convert("RGB"))[:, :, ::-1].copy()
        res = reader.readtext(img, detail=1, paragraph=False)
        good = [r for r in res if r[2] >= MIN_BOX_CONF]
        text = "\n".join(l for l, _c in _group_lines(good) if useful_line(l))
        lens = [len(tx) for _, tx, _ in res]
        conf = sum(c * l for (_, _, c), l in zip(res, lens)) / max(1, sum(lens))
        nmath = sum(text.count(ch) for ch in MATH_CHARS)
        # OCR이 '='를 '-'로 읽는 등 수식은 잘 틀리므로 괄호·연산 기호가 많은 줄도 수식으로 간주
        nmath += 3 * sum(1 for ln in text.splitlines() if sum(ln.count(c) for c in "()/^_+=") >= 4)
        # 신뢰도가 높은 개별 글자 상자(말풍선·제목 등) — 이미지 첨부 장면의 보조 정보로 사용
        hi = "\n".join(tx for _, tx, c in res if c >= 0.7 and useful_line(tx) and len(text_key(tx)) <= 25)
        s.update(ocr=text, ocr_hi=hi, ocr_conf=round(conf, 3), ocr_chars=len(text_key(text)),
                 math=nmath >= 3, blank=float(img.std()) < 6.0)
        if k % 5 == 0 or k == len(slides):
            progress(p0 + (p1 - p0) * k / len(slides), f"슬라이드 글자 인식 {k}/{len(slides)}")
        if k % 20 == 0 or k == len(slides):
            log(f"  OCR {k}/{len(slides)}")
    del reader
    gc.collect()
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:
        pass


def group_scenes(events, slides, sim: float = 0.42):
    """
    시간상 연달아 나온 슬라이드 중 글자 내용이 거의 같은 것(같은 문서에 강조 표시·말풍선만
    바뀐 장면, 애니메이션 단계 등)을 하나의 '장면 묶음'으로 묶는다.
    대표 이미지는 묶음 안에서 인식된 글자가 가장 많은 프레임(가려진 부분이 가장 적은 모습).
    반환: groups [{gid, slides:[id...], rep:id}], 각 slide에 s['group'] 기록
    """
    by_id = {s["id"]: s for s in slides}
    grams = {s["id"]: char_ngrams(s.get("ocr", "")) for s in slides}
    groups, prev_sid = [], None
    for _t, sid in events:
        s = by_id[sid]
        if "group" in s:  # 재등장: 기존 묶음 유지
            prev_sid = sid
            continue
        p = by_id.get(prev_sid)
        same = (p is not None and "group" in p and s.get("ocr_chars", 0) >= 40 and p.get("ocr_chars", 0) >= 40
                and jaccard(grams[sid], grams[prev_sid]) >= sim)
        if same:
            g = groups[p["group"] - 1]
            g["slides"].append(sid)
        else:
            g = {"gid": len(groups) + 1, "slides": [sid]}
            groups.append(g)
        s["group"] = g["gid"]
        prev_sid = sid
    for s in slides:  # 이벤트에 없는 슬라이드(이론상 없음) 대비
        if "group" not in s:
            groups.append({"gid": len(groups) + 1, "slides": [s["id"]]})
            s["group"] = groups[-1]["gid"]
    for g in groups:
        g["rep"] = max(g["slides"], key=lambda i: (by_id[i].get("ocr_chars", 0), i))
    return groups


def unreliable_score(s) -> float:
    """OCR만으로는 내용을 믿기 어려운 정도 (높을수록 이미지 첨부가 필요, '균형' 모드는 1.0 이상만)
      수식 +3 · 글자가 빽빽한 문서·표(300자 이상) +2 · 글자가 거의 없는 그림·도표 +1
      + OCR 신뢰도가 낮을수록 가산(글자가 많을수록 가중). 자막 한두 줄뿐인 강사 화면은 1 미만."""
    if s.get("blank"):
        return 0.0
    chars, conf = s.get("ocr_chars", 0), s.get("ocr_conf", 1.0)
    sc = 3.0 if s.get("math") else 0.0
    if chars < 15:
        sc += 1.0
    if chars >= 300:
        sc += 2.0
    if chars >= 15 and conf < 0.8:
        sc += (0.8 - conf) * 5 * min(1.0, chars / 200)
    return round(sc, 3)
