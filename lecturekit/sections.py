# -*- coding: utf-8 -*-
"""구간 나누기: 화면 전환 시각으로 발화를 나누고, 화면 글자 중복을 걸러냄 (출력·AI 분석 공용)."""
from __future__ import annotations

import re
from collections import deque
from difflib import SequenceMatcher

from .common import clean_text, text_key


def build_sections(events, segs, duration):
    """슬라이드 전환 시각으로 구간을 나누고 발화를 배정 (슬라이드가 없으면 5분 단위)"""
    if not events:
        events = [(float(t), None) for t in range(0, int(duration) + 1, 300)]
    elif events[0][0] > 5:
        events = [(0.0, None)] + list(events)
    secs = []
    for k, (t, sid) in enumerate(events):
        end = events[k + 1][0] if k + 1 < len(events) else duration
        secs.append({"start": t, "end": end, "slide": sid, "segs": []})
    j = 0
    for s in segs:
        t = s["start"] + min(1.0, (s["end"] - s["start"]) / 2)  # 발화 시작 시점 기준
        while j + 1 < len(secs) and t >= secs[j + 1]["start"]:
            j += 1
        secs[j]["segs"].append(s)
    return secs


def paragraphs(segs, span=60.0):
    """발화를 약 1분 단위 문단으로 묶음 → [(시작시각, 문단)]"""
    out, cur, t0 = [], [], None
    for s in segs:
        if t0 is None:
            t0 = s["start"]
        cur.append(clean_text(s["text"]))
        if s["end"] - t0 >= span:
            out.append((t0, " ".join(c for c in cur if c)))
            cur, t0 = [], None
    if cur:
        out.append((t0, " ".join(c for c in cur if c)))
    return [(t, p) for t, p in out if p]


class LineDedup:
    """강의 전체에서 '처음 나온 화면 글자 줄'만 통과시킴.
    숫자가 같고 글자가 비슷한 줄(OCR 흔들림·애니메이션 변화)은 같은 줄로 보고,
    숫자가 다르면(R1=10kΩ / R1=20kΩ) 다른 내용으로 남김."""

    def __init__(self):
        self.exact, self.recent, self.words = set(), deque(maxlen=300), set()

    def new_lines(self, text: str):
        out = []
        for line in text.splitlines():
            k = text_key(line)
            if not k or k in self.exact:
                continue
            words = {text_key(w) for w in line.split()} - {""}
            fresh = words - self.words
            self.words.update(words)
            dk = re.sub(r"\D", "", k)
            sims = [SequenceMatcher(None, k, r).ratio() for r in self.recent
                    if abs(len(r) - len(k)) <= 0.4 * max(len(r), len(k)) and re.sub(r"\D", "", r) == dk]
            best = max(sims, default=0.0)
            if best >= 0.8 or (words and len(fresh) / len(words) < 0.5 and best >= 0.6):
                continue
            out.append(line)
            self.exact.add(k)
            self.recent.append(k)
        return out
