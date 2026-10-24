# -*- coding: utf-8 -*-
"""로컬 AI로 강의 전체를 읽고 노트 초안을 쓰며, 캡처할 중요 장면을 고름.

흐름 (작은 모델도 안정적으로 다루도록 나눠서 처리):
  1) 부분별 정리: 전사본을 약 3,500자씩 나눠(화면 전환 경계 기준) 소제목·핵심 내용·표·강조·확인 필요 + 장면별 중요도(0~3)
  2) 화면 확인: 중요도가 높은 후보 장면의 이미지를 AI가 직접 보고 표·도식·정리 화면인지,
     표지·강사 얼굴 같은 장식 화면인지 판정 + 한 줄 설명
  3) 전체 개요: 부분별 정리를 모아 강의 제목·개요·핵심어
결과는 _work/ai_cache.json에 저장해, 같은 영상을 다시 돌리면 바뀐 부분만 다시 묻습니다.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import ai
from .common import hms, log, progress, text_key
from .sections import LineDedup, paragraphs

CHUNK_CHARS = 3500

SYSTEM = """당신은 한국어 강의 영상을 정확하게 정리하는 조교입니다.
규칙:
- 자료에 없는 내용을 지어내지 않습니다. 모르면 비워 둡니다.
- 통계·수치는 인식된 값을 그대로 씁니다(명백한 큰 오류나 논리 모순만 바로잡음).
- 강사의 말실수·논리 불일치는 따로 표시하지 않고 문맥으로 바로잡아 씁니다.
- 음성 인식 오류(동음이의어, 전문용어·영문 약어)는 화면 글자와 문맥으로 바로잡습니다.
- 화면 글자는 OCR 결과라 받침 오류(을→올, 를→름, 했→햇)가 있을 수 있으니 바로잡아 읽습니다.
- 문장은 간결한 평서문(…입니다/…합니다)으로 씁니다."""

CHUNK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "table": {"type": "object", "properties": {
            "caption": {"type": "string"},
            "header": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}},
            "required": ["caption", "header", "rows"]},
        "emphasis": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "array", "items": {"type": "string"}},
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "score": {"type": "integer"}}, "required": ["id", "score"]}},
    },
    "required": ["title", "points", "table", "emphasis", "uncertain", "scenes"],
}

CHUNK_PROMPT = """아래는 강의 "{name}"의 {k}/{n}번째 부분({start}–{end}) 자료입니다.
<화면 G번호 · 시각> 표시는 그 시점에 화면이 바뀌었다는 뜻이며, 그 아래 '화면 글자'는 OCR 결과입니다.
(00:00:00)으로 시작하는 줄은 강의 음성을 받아 적은 것입니다.

할 일 (JSON으로 답):
1. title: 이 부분 내용을 대표하는 소제목 (20자 이내)
2. points: 핵심 내용 4~10개. 각 항목은 완결된 한 문장. 정의·조건·수치·예시·절차를 빠뜨리지 말 것. 핵심 용어는 **굵게**.
3. table: 유형 비교·단계·질문과 의도처럼 표가 더 읽기 쉬운 내용이 있을 때만 표로 정리(행 2개 이상). 없으면 header와 rows를 빈 배열로.
4. emphasis: 강사가 강조·반복한 말, 과제·시험 관련 언급 (없으면 빈 배열)
5. uncertain: 인식 오류가 의심되어 원본 확인이 필요한 곳을 "시각 — 내용" 형식으로 (없으면 빈 배열)
6. scenes: 자료에 나온 모든 화면(G번호)마다 노트에 캡처 이미지로 넣을 가치를 0~3점으로:
   3 = 표·도식·그래프·정리 슬라이드처럼 화면을 봐야 이해되는 핵심 내용
   2 = 중요한 정의·목록·예시 문서가 화면에 있음
   1 = 말로 충분하고 화면은 보조
   0 = 표지·제목뿐인 화면·강사 얼굴·인사·전환 화면·장식

자료:
{body}"""

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["표", "도식", "그래프", "정리", "본문", "예시문서", "퀴즈", "제목", "인물", "기타"]},
        "score": {"type": "integer"},
        "caption": {"type": "string"},
    },
    "required": ["kind", "score", "caption"],
}

VISION_PROMPT = """이 이미지는 강의 "{name}"의 {t} 시점 화면입니다.{hint}
강의 노트에 캡처 이미지로 넣을 가치를 냉정하게 판단해 JSON으로 답하세요.
- kind: 화면 종류 (표 / 도식 / 그래프 / 정리 / 본문 / 예시문서 / 퀴즈 / 제목 / 인물 / 기타)
- score: 0~10점
   9~10 = 표·도식·요약 정리처럼 강의 핵심을 한 화면에 담아, 노트에 꼭 넣어야 함
   6~8  = 정의·목록·예시 문서 등 유용한 정보가 화면의 대부분을 차지함
   3~5  = 짧은 문구 몇 개뿐이거나 강사가 화면 대부분을 차지해, 말로 설명해도 충분함
   0~2  = 표지·단원 제목뿐인 화면·강사만 나온 화면·O/X 퀴즈·전환 화면
- caption: 화면 내용을 한 줄(40자 이내)로 요약"""

OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "overview": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "emphasis": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "overview", "keywords", "emphasis"],
}

OVERVIEW_PROMPT = """아래는 강의 영상 "{name}"(길이 {dur})을 부분별로 정리한 것입니다.
JSON으로 답하세요.
- title: 강의 제목 (영상 이름과 내용을 참고해 자연스럽게)
- overview: 강의 전체 개요 3~5문장. 강의의 목표, 큰 흐름(무엇을 다루는지), 핵심 메시지를 담을 것.
- keywords: 핵심어 5~10개
- emphasis: 아래 '강조 후보'를 바탕으로, 강사가 강조·반복한 핵심과 과제·시험 관련 언급을 5~10개의 완결된 문장으로.
  같은 내용은 하나로 합치고, 단어만 있는 항목은 뜻이 통하는 문장으로 풀어 쓸 것.

부분별 정리:
{body}

강조 후보:
{emph}"""


# ───────── 캐시 ─────────

class _Cache:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.d = {}

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha1("\x1f".join(map(str, parts)).encode("utf-8")).hexdigest()

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")


# ───────── 자료 조각 만들기 ─────────

def _items(sections, by_id):
    """시간순 자료 조각: ('scene', t, gid, 글자) / ('speech', t, 문단)"""
    dedup, seen, items = LineDedup(), set(), []
    for sec in sections:
        s = by_id.get(sec["slide"])
        if s:
            g = s["group"]
            if g not in seen:
                seen.add(g)
                lines = dedup.new_lines(s.get("ocr", ""))
                items.append(("scene", sec["start"], g, " / ".join(lines)))
        for t, p in paragraphs(sec["segs"]):
            items.append(("speech", t, p))
    return items


def _chunks(items, duration):
    """약 CHUNK_CHARS자씩, 가능하면 화면 전환 직전에서 나눔"""
    chunks, cur, size = [], [], 0
    for it in items:
        n = len(it[-1]) + 30
        if cur and size + n > CHUNK_CHARS and (it[0] == "scene" or size + n > CHUNK_CHARS * 1.3):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += n
    if cur:
        if chunks and size < CHUNK_CHARS * 0.25:  # 너무 짧은 마지막 조각은 앞에 붙임
            chunks[-1] += cur
        else:
            chunks.append(cur)
    out = []
    for k, c in enumerate(chunks):
        end = chunks[k + 1][0][1] if k + 1 < len(chunks) else duration
        out.append({"start": c[0][1], "end": end, "items": c})
    return out


def _render(items) -> str:
    lines = []
    for it in items:
        if it[0] == "scene":
            _, t, g, txt = it
            lines.append(f"<화면 G{g} · {hms(t)}>")
            lines.append(f"화면 글자: {txt}" if txt else "화면 글자 없음 (그림·사진·강사 화면일 수 있음)")
        else:
            lines.append(f"({hms(it[1])}) {it[2]}")
    return "\n".join(lines)


def _clean_table(tb):
    if not isinstance(tb, dict):
        return None
    head = [_fix(h) for h in tb.get("header") or []]
    rows = [[_fix(c) for c in r] for r in tb.get("rows") or [] if isinstance(r, list)]
    if len(head) < 2 or len(rows) < 2:
        return None
    rows = [(r + [""] * len(head))[:len(head)] for r in rows if any(r)]
    return {"caption": _fix(tb.get("caption", "")), "header": head, "rows": rows}


_UNIT_SPACE = re.compile(r"(\d) (가지|개|초|분|년|월|일|회|차시|차|명|점|단계|세|시간|번|배|장|위|%)"
                         r"(?=[^가-힣]|$|이내|이상|이하|정도|분량|동안|부터|까지|으로|에서|이|의|에|을|를|은|는|로|와|과|만|씩|째|도|간)")


def _fix(t: str) -> str:
    """AI가 '6 가지', '40 초'처럼 숫자와 단위 사이에 넣는 띄어쓰기 제거"""
    return _UNIT_SPACE.sub(r"\1\2", str(t).strip())


def _strs(v, limit=20):
    return [_fix(x) for x in (v or []) if str(x).strip()][:limit]


# ───────── 본 처리 ─────────

def analyze(video_name: str, duration: float, sections, slides, groups, work: Path, model: str,
            vision: bool = True, max_images: int = 12, p0: float = 0.55, p1: float = 0.95) -> dict:
    ai.ensure_server()
    if not ai.has_model(model):
        raise ai.AIError(f"로컬 AI 모델 {model}이(가) 없습니다. [로컬 AI 설치] 버튼으로 받으세요.")
    cache = _Cache(work / "ai_cache.json")
    by_id = {s["id"]: s for s in slides}
    gids = {g["gid"] for g in groups}
    chunks = _chunks(_items(sections, by_id), duration)
    n = len(chunks)
    log(f"  로컬 AI({model})가 강의를 {n}부분으로 나눠 읽습니다")
    pm = p0 + (p1 - p0) * 0.65  # 진행률: 부분 정리 65%, 화면 확인 25%, 개요 10%
    pv = p0 + (p1 - p0) * 0.90

    # 1) 부분별 정리
    text_score, chunk_of = {}, {}
    for k, c in enumerate(chunks, 1):
        progress(p0 + (pm - p0) * (k - 1) / max(1, n), f"로컬 AI 정리 {k}/{n}")
        prompt = CHUNK_PROMPT.format(name=video_name, k=k, n=n, start=hms(c["start"]), end=hms(c["end"]),
                                     body=_render(c["items"]))
        key = _Cache.key("chunk", model, prompt)
        d = cache.get(key)
        if d is None:
            try:
                d = ai.chat_json(model, prompt, CHUNK_SCHEMA, system=SYSTEM, num_predict=2500)
            except ai.AIError as ex:
                log(f"  [주의] {k}부분 정리 실패 — 건너뜀: {ex}")
                d = {}
            if d:
                cache.put(key, d)
        c.update(title=_fix(d.get("title", "")) or f"{hms(c['start'])} 부분",
                 points=_strs(d.get("points"), 14), table=_clean_table(d.get("table")),
                 emphasis=_strs(d.get("emphasis"), 8), uncertain=_strs(d.get("uncertain"), 8))
        in_chunk = {it[2] for it in c["items"] if it[0] == "scene"}
        for g in in_chunk:
            chunk_of[g] = k - 1
        for sc in d.get("scenes") or []:
            try:
                g = int(str(sc.get("id", "")).strip().lstrip("Gg"))
                v = max(0, min(3, int(sc.get("score", 0))))
            except (ValueError, TypeError, AttributeError):
                continue
            if g in in_chunk:
                text_score[g] = max(text_score.get(g, 0), v)
        log(f"  [{k}/{n}] {hms(c['start'])} {c['title']}  (핵심 {len(c['points'])}개)")

    # 2) 후보 장면 이미지를 AI가 직접 확인
    scenes = {}
    for g in groups:
        s = by_id[g["rep"]]
        if s.get("blank"):
            continue
        scenes[g["gid"]] = {"gid": g["gid"], "rep": g["rep"], "t": s["t_first"], "chunk": chunk_of.get(g["gid"], 0),
                            "chars": s.get("ocr_chars", 0),
                            "text": text_score.get(g["gid"], 1 if s.get("ocr_chars", 0) >= 40 else 0)}
    cands = sorted((x for x in scenes.values() if x["text"] >= 2), key=lambda x: (-x["text"], -x["chars"]))
    cands = cands[:max(3 * max_images, 10)]
    if vision and cands:
        log(f"  중요 후보 장면 {len(cands)}개를 AI가 직접 보고 확인합니다")
        for k, x in enumerate(cands, 1):
            progress(pm + (pv - pm) * (k - 1) / len(cands), f"화면 확인 {k}/{len(cands)}")
            s = by_id[x["rep"]]
            ocr = " / ".join(s.get("ocr", "").splitlines()[:6])
            hint = f"\n참고용 OCR(오류 가능): {ocr[:300]}" if ocr else ""
            img = Path(s["image"])
            prompt = VISION_PROMPT.format(name=video_name, t=hms(x["t"]), hint=hint)
            key = _Cache.key("vision", model, prompt, img.stat().st_size)
            d = cache.get(key)
            if d is None:
                try:
                    d = ai.chat_json(model, prompt, VISION_SCHEMA, images=[img], num_predict=300)
                    cache.put(key, d)
                except ai.AIError as ex:
                    log(f"  [주의] 화면 확인 실패(G{x['gid']}): {ex}")
                    d = {}
            try:
                x["vision"] = max(0, min(10, int(d.get("score"))))
            except (TypeError, ValueError):
                pass
            x["kind"] = str(d.get("kind", ""))
            x["caption"] = _fix(d.get("caption", ""))
    for x in scenes.values():
        x["final"] = _final_score(x, by_id[x["rep"]])

    # 3) 전체 개요
    progress(pv, "로컬 AI 전체 개요 작성")
    body = "\n".join(f"[{hms(c['start'])}] {c['title']}: " + " ".join(c["points"])[:600] for c in chunks)[:7000]
    emph = "\n".join(f"- {e}" for c in chunks for e in c["emphasis"][:3])[:4000] or "(없음)"
    prompt = OVERVIEW_PROMPT.format(name=video_name, dur=hms(duration), body=body, emph=emph)
    key = _Cache.key("overview", model, prompt)
    ov = cache.get(key)
    if ov is None:
        try:
            ov = ai.chat_json(model, prompt, OVERVIEW_SCHEMA, system=SYSTEM, num_predict=1200)
            cache.put(key, ov)
        except ai.AIError as ex:
            log(f"  [주의] 개요 작성 실패: {ex}")
            ov = {}
    progress(p1, "로컬 AI 정리 완료")
    return {
        "model": model,
        "title": _fix(ov.get("title", "")) or Path(video_name).stem,
        "overview": _strs(ov.get("overview"), 8),
        "keywords": _strs(ov.get("keywords"), 12),
        "emphasis": _strs(ov.get("emphasis"), 12),
        "n_chunks": n,
        "chunks": [{**{k: c[k] for k in ("start", "end", "title", "points", "table", "emphasis", "uncertain")},
                    "source": _render(c["items"])} for c in chunks],
        "scenes": {g: x for g, x in scenes.items() if g in gids},
    }


def _final_score(x, slide) -> float:
    """0~10점: 텍스트 판단(0~3) 40% + 이미지 판단(0~10) 60% + 화면 글자량 가산 − 제목·인물 감점.
    이미지를 확인하지 않은 장면은 텍스트 판단만으로 (보수적으로) 계산."""
    t10 = x["text"] / 3 * 10
    sc = 0.4 * t10 + 0.6 * x["vision"] if "vision" in x else 0.8 * t10
    chars = slide.get("ocr_chars", 0)
    sc += 1.5 if chars >= 300 else 1.0 if chars >= 150 else 0.0
    if x.get("kind") in ("제목", "인물"):
        sc -= 3
    return round(sc, 2)


def choose_by_ai(result: dict, groups, max_images: int, min_score: float = 6.0):
    """점수 min_score 이상 장면 중 상위 max_images개 → 대표 슬라이드 id 목록(시간순).
    한 부분에 몰리지 않도록 부분마다 상한(전체의 약 1.5배를 부분 수로 나눈 값)을 둠."""
    n = max(1, result.get("n_chunks") or len(result.get("chunks", [])) or 1)
    quota = max(2, -(-max_images * 3 // (2 * n)))
    per, picked = {}, []
    for x in sorted((x for x in result["scenes"].values() if x["final"] >= min_score),
                    key=lambda x: (-x["final"], -x.get("chars", 0))):
        c = x.get("chunk", 0)
        if per.get(c, 0) >= quota:
            continue
        per[c] = per.get(c, 0) + 1
        picked.append(x)
        if len(picked) >= max_images:
            break
    return [x["rep"] for x in sorted(picked, key=lambda x: x["t"])]


def dedupe_points(chunks):
    """부분 경계에서 같은 문장이 반복되면 뒤쪽을 제거"""
    seen = set()
    for c in chunks:
        keep = []
        for p in c["points"]:
            k = text_key(p)
            if k and k not in seen:
                seen.add(k)
                keep.append(p)
        c["points"] = keep
