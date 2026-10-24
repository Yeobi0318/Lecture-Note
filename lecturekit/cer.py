# -*- coding: utf-8 -*-
"""음성 인식 정확도 비교 (CER = (대치 S + 삭제 D + 삽입 I) / 정답 글자 수 N).

1) draft : 비교 구간의 정답 초안(한 줄에 한 문장, 줄 앞 [시각]) 생성 → 사람이 들으며 고침
2) score : 고친 정답과 여러 전사본을 비교해 CER·용어 재현율 표 + 오류 위치 HTML
띄어쓰기·문장부호·대소문자·군말은 양쪽 모두 제거한 뒤 비교한다.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import numpy as np

from .common import FILLER_RE, TOOL_DIR, hms, log
from .transcript import load_transcript

TS_PREFIX = re.compile(r"^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*")


def normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", FILLER_RE.sub(" ", text).lower())


def read_reference(path: Path) -> str:
    """정답 파일 읽기. 줄 앞의 [00:30:12] 같은 시각 표시는 채점에서 제외"""
    return " ".join(TS_PREFIX.sub("", l) for l in Path(path).read_text(encoding="utf-8-sig").splitlines())


def window_text(path: Path, start: float, end: float, margin: float) -> str:
    """[start-margin, end+margin]과 겹치는 문장들. 타임스탬프가 없는 파일은 전체를 사용
    (정렬 단계에서 정답과 가장 잘 맞는 부분을 자동으로 찾음)."""
    try:
        segs = load_transcript(path, 10 ** 7)
    except RuntimeError:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        if len(normalize(text)) > 20000:
            log(f"[주의] {Path(path).name}: 타임스탬프가 없고 깁니다. 비교 구간 근처만 잘라 저장하면 빠릅니다.")
        return text
    lo, hi = start - margin, end + margin
    return " ".join(s["text"] for s in segs if s["end"] > lo and s["start"] < hi)


def align(ref: str, hyp: str):
    """반(半)전역 정렬: 정답 전체 ↔ 인식 결과의 가장 잘 맞는 부분 문자열.
    인식 결과의 앞뒤 여분은 오류로 세지 않는다. 반환 (S, D, I, ops)"""
    n, m = len(ref), len(hyp)
    if n == 0:
        raise ValueError("정답(reference)이 비어 있습니다.")
    r = np.frombuffer(ref.encode("utf-32-le"), dtype=np.uint32)
    h = np.frombuffer(hyp.encode("utf-32-le"), dtype=np.uint32)
    D = np.empty((n + 1, m + 1), dtype=np.int16 if n < 32000 else np.int32)
    D[0, :] = 0
    ar = np.arange(m + 1, dtype=np.int32)
    for i in range(1, n + 1):
        prev = D[i - 1]
        cost = (h != r[i - 1]).astype(np.int32)
        t = np.empty(m + 1, dtype=np.int32)
        t[0] = prev[0] + 1
        t[1:] = np.minimum(prev[1:] + 1, prev[:-1] + cost)       # 삭제 / 일치·대치
        D[i] = np.minimum.accumulate(t - ar) + ar                  # 삽입: min_k≤j (t[k] + j − k)
    j = int(np.argmin(D[n]))
    i, ops = n, []
    while i > 0:
        if j > 0 and D[i, j] == D[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append(("C" if ref[i - 1] == hyp[j - 1] else "S", ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif D[i, j] == D[i - 1, j] + 1:
            ops.append(("D", ref[i - 1], ""))
            i -= 1
        else:
            ops.append(("I", "", hyp[j - 1]))
            j -= 1
    ops.reverse()
    return (sum(o[0] == "S" for o in ops), sum(o[0] == "D" for o in ops),
            sum(o[0] == "I" for o in ops), ops)


def _terms(path: Path):
    if not path.is_file():
        return []
    return [normalize(l) for l in path.read_text(encoding="utf-8-sig").splitlines()
            if l.strip() and not l.strip().startswith("#") and normalize(l)]


def term_recall(ref: str, hyp: str, terms):
    tot = hit = 0
    for t in terms:
        rc = ref.count(t)
        if rc:
            tot += rc
            hit += min(rc, hyp.count(t))
    return (hit / tot if tot else None), tot


def draft(transcript: Path, start: float, end: float, out: Path) -> Path:
    segs = [s for s in load_transcript(Path(transcript), 10 ** 7) if s["end"] > start and s["start"] < end]
    out.write_text("".join(f"[{hms(s['start'])}] {s['text']}\n" for s in segs), encoding="utf-8")
    log(f"정답 초안: {out}  ({hms(start)}~{hms(end)}, {len(segs)}줄)")
    log("영상을 들으며 틀린 곳을 고친 뒤 저장하세요. 줄 앞 [시각]은 지우지 않아도 됩니다.")
    return out


def score(reference: Path, hyps, start: float, end: float, out: Path,
          margin: float = 20.0, terms_path: Path = TOOL_DIR / "terms.txt"):
    ref = normalize(read_reference(reference))
    terms = _terms(Path(terms_path))
    rows, sections = [], []
    for hp in hyps:
        hyp = normalize(window_text(Path(hp), start, end, margin))
        S, Dn, In, ops = align(ref, hyp)
        cer = (S + Dn + In) / len(ref)
        rec, nterm = term_recall(ref, hyp, terms)
        rows.append((Path(hp).name, cer, S, Dn, In, rec, nterm))
        sections.append(f"<h2>{html.escape(Path(hp).name)} — CER {cer * 100:.2f}%</h2>"
                        f"<div class='diff'>{_render(ops)}</div>")
    log(f"\n구간 {hms(start)} ~ {hms(end)} · 정답 {len(ref):,}자  (CER이 낮을수록 정확)\n")
    for name, cer, S, Dn, In, rec, nterm in rows:
        rs = f"{rec * 100:.0f}% ({nterm}회)" if rec is not None else "-"
        log(f"  {name}\n    CER {cer * 100:.2f}%   대치 {S} · 빠뜨림 {Dn} · 덧붙임 {In}   용어 재현율 {rs}")
    e = html.escape
    trs = "".join(f"<tr><td>{e(n)}</td><td><b>{c * 100:.2f}%</b></td><td>{S}</td><td>{Dn}</td><td>{In}</td>"
                  f"<td>{(f'{r * 100:.0f}% ({k}회)' if r is not None else '-')}</td></tr>"
                  for n, c, S, Dn, In, r, k in rows)
    out.write_text(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>음성 인식 비교</title>
<style>body{{font:15px/1.7 "Malgun Gothic",sans-serif;margin:20px;max-width:1100px}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:6px 10px;text-align:right}}td:first-child{{text-align:left}}
.diff{{word-break:break-all;background:#fafafa;border:1px solid #ddd;padding:12px;border-radius:8px}}
del{{background:#fecaca;color:#991b1b}} ins{{background:#bbf7d0;color:#166534;text-decoration:none}}
.s{{background:#fde68a;color:#92400e}} .note{{color:#666}}</style></head><body>
<h1>음성 인식 비교: {hms(start)} ~ {hms(end)}</h1>
<p class="note">정답 {len(ref):,}자 기준 · 띄어쓰기·문장부호·군말 제외 · CER이 낮을수록 정확</p>
<table><tr><th>파일</th><th>CER</th><th>대치</th><th>빠뜨림</th><th>덧붙임</th><th>용어 재현율</th></tr>{trs}</table>
<p class="note">표시: <del>빠뜨린 글자</del> · <ins>덧붙인 글자</ins> · <span class="s">잘못 인식한 글자</span>(마우스를 올리면 정답)</p>
{''.join(sections)}</body></html>""", encoding="utf-8")
    log(f"\n오류 위치 보기: {out}")
    return rows


def _render(ops) -> str:
    e = html.escape
    out = []
    for kind, a, b in ops:
        if kind == "C":
            out.append(e(a))
        elif kind == "S":
            out.append(f"<span class='s' title='정답: {e(a)}'>{e(b)}</span>")
        elif kind == "D":
            out.append(f"<del>{e(a)}</del>")
        else:
            out.append(f"<ins>{e(b)}</ins>")
    return "".join(out)
