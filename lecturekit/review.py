# -*- coding: utf-8 -*-
"""로컬 AI 검토·교정: 전사본(클로바노트 등)과 노트(.md)의 오탈자·인식 오류·논리 모순·사실 오류.

안전장치 — 작은 모델(4B)은 오류만이 아니라 멀쩡한 문장까지 "다듬으려" 합니다(실측: "잘 봤으려나?"→"잘 봤어?",
"없습니다"→"없어요", 맞는 단어 "평이한"→"평범한"). 그래서
  ① AI에게는 "몇 번 줄의 어떤 구절(original)을 무엇(corrected)으로, 왜"만 답하게 하고(글 전체를 다시 쓰지 않음),
  ② 길이·변경량 제한과 어미·말투만 바꾸는 수정 거부를 프로그램이 먼저 걸러 내며,
  ③ 남은 수정은 근거(화면 글자·전문용어·원문)에 새 표기가 있으면 채택하고, 없으면 AI에게 한 번 더
     "원래 글이 확실히 틀렸고 고친 것이 확실히 맞는가"를 심사받아 통과한 것만 적용합니다.
  ④ 원문에 그 구절이 그대로 있을 때만 한 곳을 바꾸고, 바꾼 내역은 모두 기록합니다.
  1) 전사본 교정: 약 2,500자씩, 그 시간대 화면 글자(슬라이드 용어 표기 근거)와 전문용어 목록을 함께 줌
  2) 노트 검토: 부분별로 노트 항목을 원문(전사본·화면 글자)과 대조 → 이어서 노트 전체의 앞뒤 모순 확인
  3) 아무 .md 검토: Claude가 써 준 정리본 등 (문서 목차를 맥락으로 함께 줌)
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from . import ai
from .analyze import SYSTEM, _Cache, _fix
from .common import hms, log, progress, text_key

KINDS = ["오탈자", "인식 오류", "용어", "논리 모순", "사실 오류", "기타"]

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "original": {"type": "string"}, "corrected": {"type": "string"},
            "kind": {"type": "string", "enum": KINDS}, "reason": {"type": "string"}},
            "required": ["id", "original", "corrected", "kind", "reason"]}},
        "issues": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "issue": {"type": "string"}}, "required": ["id", "issue"]}},
    },
    "required": ["edits", "issues"],
}

RULES = """찾는 법: 줄마다 단어를 하나씩 읽으며, 사전에 없는 철자(예: 마게팅→마케팅, 전락 수립→전략 수립, 기범→기법,
플랫홈→플랫폼, 접합성→적합성)와 문맥에 맞지 않는 단어를 빠짐없이 찾으세요. 한 줄에 여러 개가 있을 수 있습니다.

답하는 법:
- edits: 고칠 곳마다 id(줄 번호), original(그 줄에 실제로 있는 그대로의, 틀린 단어 1~3개만. 문장 전체 금지),
  corrected(고친 단어), kind({kinds}), reason(짧은 이유).
- 말투·어미(습니다/어요)·어순·문장부호를 바꾸거나 더 자연스럽게 다듬는 것은 수정이 아닙니다. 하지 마세요.
- issues: 틀린 것 같지만 자료만으로는 고칠 수 없어 사람이 원본을 확인해야 하는 곳 (id, issue).
- 고칠 곳이 없으면 edits와 issues를 빈 배열로 답하세요. 확신이 없으면 고치지 말고 issues에 적으세요."""

TRANSCRIPT_PROMPT = """아래는 강의 "{name}"의 음성을 받아 적은 전사본 일부({start}–{end})입니다.
음성 인식 오류와 오탈자를 찾아 고칠 곳만 알려 주세요.

고칠 것: 잘못 들은 단어(동음이의어·비슷한 발음), 전문용어·영문 약어 표기, 오탈자, 명백히 잘못 붙거나 띄운 곳,
         문맥상 명백히 틀린 숫자·이름, 앞뒤 문맥과 모순되는 말실수.
고치지 말 것: 말투·어순·구어체, 맞는 표현을 더 매끄럽게 바꾸기, 요약·삭제·추가.
참고 자료의 화면 글자는 용어 표기의 근거입니다(단, OCR이라 받침 오류가 있을 수 있음).

{rules}

참고 — 전문용어: {terms}
참고 — 이 시간대 화면 글자: {screen}

전사본 (줄 번호: 내용):
{body}"""

NOTE_PROMPT = """아래는 강의 "{name}"의 한 부분({start}–{end})을 로컬 AI가 정리한 노트 항목과, 그 근거가 된 원문입니다.
노트 항목을 원문과 대조해 검토하고 고칠 곳만 알려 주세요.

찾을 것: 오탈자, 원문과 다른 내용(사실 오류), 노트 항목끼리 서로 모순되는 내용, 원문의 인식 오류가 그대로 옮겨진 곳,
         숫자·이름이 원문과 다른 곳.
지킬 것: 통계 수치는 원문에 인식된 그대로 둡니다(큰 오류나 논리 모순만 예외). 강사의 말실수는 문맥으로 바로잡은 쪽이 맞습니다.
         문장을 더 길게 다시 쓰거나 내용을 추가하지 마세요.

{rules}

노트 항목 (줄 번호: 내용):
{items}

원문:
{source}"""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"}, "ok": {"type": "boolean"}}, "required": ["n", "ok"]}}},
    "required": ["results"],
}

VERIFY_PROMPT = """다른 검토자가 아래 글의 철자 오류를 고치자고 제안했습니다. 각 제안을 심사하세요.
질문: "원래 단어가 국어사전에 없는 잘못된 철자(오타·잘못 들어 적은 말)이고, 고친 것이 그 올바른 철자인가?"
- 예 → ok=true   (예: 마게팅→마케팅, 비젼→비전, 중요함니다→중요합니다, 플랫홈→플랫폼)
- 원래 단어가 사전에 있는 올바른 단어라서 다른 단어로 바꾸는 것이면 → ok=false   (예: 평이한→평범한, 전파→전달)
- 말투·어미·띄어쓰기만 바꾸거나, 숫자·이름을 바꾸거나, 뜻이 달라지면 → ok=false

글:
{context}

제안된 수정:
{items}

모든 수정 번호(n)마다 ok를 답하세요."""

CONSISTENCY_PROMPT = """아래는 강의 "{name}" 노트 전체의 소제목과 핵심 항목입니다.
노트 전체를 처음부터 끝까지 보고, 서로 다른 부분끼리 모순되는 내용(같은 개념의 정의·숫자·단계 수가 다름 등)과
용어 표기가 일관되지 않은 곳만 찾아 고칠 곳을 알려 주세요. 각 부분 안의 문장 다듬기는 하지 마세요.

{rules}

노트 (줄 번호: 내용):
{items}"""

MD_PROMPT = """아래는 문서 "{name}"의 일부입니다. 오탈자, 문법 오류, 앞뒤 논리 모순, 명백한 사실 오류를 찾아 고칠 곳만 알려 주세요.
문서 전체 목차를 참고해 다른 부분과 모순되는지도 확인하세요.
지킬 것: 문체와 마크다운 서식(#, -, |, ** 등)은 그대로 두고, 내용을 추가·삭제하거나 더 길게 다시 쓰지 마세요.
통계 수치는 명백한 큰 오류가 아니면 그대로 둡니다.

{rules}

문서 목차: {outline}

본문 (줄 번호: 내용):
{body}"""


# ───────── 공통: 수정 요청과 적용 ─────────

def _ask(model: str, prompt: str, cache: _Cache) -> dict:
    key = _Cache.key("review", model, prompt)
    d = cache.get(key)
    if d is None:
        try:
            d = ai.chat_json(model, prompt, EDIT_SCHEMA, system=SYSTEM, num_predict=2000)
        except ai.AIError as ex:
            log(f"  [주의] 검토 요청 실패 — 건너뜀: {ex}")
            return {"edits": [], "issues": []}
        cache.put(key, d)
    return d


_ENDING = re.compile(r"(습니다|니다|어요|아요|에요|예요|해요|세요|요|다|네|지|까|죠|어|아|야|군|나|니|냐|래|게|자)[.?!…]*$")


def _ok_edit(original: str, corrected: str, max_len: int, evidence: bool = False) -> bool:
    """길이·변경량 제한, 띄어쓰기·어미만 바꾸는 수정 거부 (근거 자료에 있는 표기로 바꾸는 수정은 변경량 제한 완화)"""
    o, c = original.strip(), corrected.strip()
    if not o or not c or o == c or len(o) > max_len:
        return False
    if len(c) < len(o) * 0.6 or len(c) > len(o) * 1.6 + 6:  # 내용을 잘라 내거나 크게 덧붙이는 수정 거부
        return False
    if text_key(o) == text_key(c) and not re.search(r"\d", o):  # 띄어쓰기·문장부호만 바뀜(숫자 붙이기는 허용)
        return False
    sm = SequenceMatcher(None, o, c)
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
    if changed > (max(10, len(o)) if evidence else min(10, max(4, len(o) * 0.35))):  # 여러 단어를 바꾸면 다시 쓰기
        return False
    if not evidence and re.findall(r"\d+", o) != re.findall(r"\d+", c):  # 숫자는 근거가 있을 때만 바꿈 (수치는 인식된 대로)
        return False
    if not evidence and sounds_alike(o, c) < 0.6:  # 근거 없이 뜻을 바꾸는 수정은 거부 (잘못 들음·오타만 허용)
        return False
    regions = sum(1 for op in sm.get_opcodes() if op[0] != "equal")
    if regions > (3 if evidence else 2) or (not evidence and len(o) > 15 and regions > 1):  # 긴 구절 여러 곳을 고치면 다시 쓰기
        return False
    pre = 0
    for a, b in zip(o, c):
        if a != b:
            break
        pre += 1
    if pre >= len(o.rstrip(".?!… ")) - 4 and _ENDING.search(o) and _ENDING.search(c):  # 어미만 바뀜
        return False
    return True


_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def _jamo(t: str) -> str:
    out = []
    for ch in text_key(t):
        k = ord(ch) - 0xAC00
        if 0 <= k < 11172:
            out += [_CHO[k // 588], _JUNG[k % 588 // 28], _JONG[k % 28].strip()]
        else:
            out.append(ch)
    return "".join(out)


def sounds_alike(a: str, b: str) -> float:
    """자모 단위 유사도 (잘못 들음·오타는 보통 0.6 이상: 마게팅/마케팅 0.92, 숫자만 어필한/항목만 언급한 0.4대)"""
    return SequenceMatcher(None, _jamo(a), _jamo(b)).ratio()


# 어디에나 나오는 말은 근거가 되지 못함 (예: '있습니다'가 원문에 있다고 문장을 늘리면 안 됨)
_COMMON = re.compile(r"^(있|없|하|되|이|그|저|것|수|때|더|잘|또|및|등|이라|라고|에서|에게|으로|부터|까지|때문|통해|위해|대해|"
                     r"경우|정도|부분|내용|무엇|어떤|여러|모든|다양|구체|중요|필요)")


def _words(t: str):
    return [text_key(w) for w in re.findall(r"[0-9A-Za-z가-힣]+", t) if len(text_key(w)) >= 2]


def _content_words(t: str):
    return [w for w in _words(t) if not _COMMON.match(w)]


def _evidence(original: str, corrected: str, ref_keys: str) -> bool:
    """근거 있는 수정: 고친 쪽에 새로 들어간 단어가 모두 근거 자료에 있고, 원래 표현은 근거 자료에 없음
    (원래 표현도 근거에 있으면 틀린 것이 아님 — 예: '인성 관련 질문'을 다른 말로 바꾸는 것 방지)"""
    if not ref_keys:
        return False
    okeys = text_key(original)
    new = [w for w in _content_words(corrected) if w not in okeys]
    if not new or not all(w in ref_keys for w in new):
        return False
    old = [w for w in _words(original) if w not in text_key(corrected)]
    return not old or not all(w in ref_keys for w in old)


def _verify(model: str, cands, context: str, cache: _Cache) -> set:
    """제안된 수정을 AI가 한 번 더 심사 → 통과한 후보 번호 집합"""
    items = "\n".join(f"{n}) [줄 {e['id']}] \"{e['core'][0]}\" → \"{e['core'][1]}\" "
                      f"(문맥: \"{e['original']}\" → \"{e['corrected']}\", 이유: {e['reason']})"
                      for n, e in enumerate(cands, 1))
    prompt = VERIFY_PROMPT.format(context=context[:4500], items=items)
    key = _Cache.key("verify", model, prompt)
    d = cache.get(key)
    if d is None:
        try:
            d = ai.chat_json(model, prompt, VERIFY_SCHEMA, system=SYSTEM, num_predict=800)
        except ai.AIError:
            return set()
        cache.put(key, d)
    ok = set()
    for r in d.get("results") or []:
        try:
            if r.get("ok") is True:
                ok.add(int(r.get("n")))
        except (TypeError, ValueError, AttributeError):
            pass
    return ok


_HANJA = re.compile(r"[\u3400-\u9fff]")
_PARTICLE = re.compile(r"(?<=[\uac00-\ud7a3]) (\uc5d0\uc11c|\uc5d0\uac8c|\uc73c\ub85c|\uc5d0|\uc744|\ub97c|\uc740|\ub294|\uc758)(?=[\s.,!?]|$)")  # '\uae30\uc220\ub825 \uc5d0' \u2192 '\uae30\uc220\ub825\uc5d0'


def _find_line(lines: dict, i: str, o: str):
    """제안이 가리키는 줄: 줄 번호가 맞으면 그 줄, 아니면(모델이 번호를 비우는 경우가 많음) 원문 구절이 있는 줄"""
    if i in lines and o in lines[i]:
        return i
    hits = [k for k, v in lines.items() if o and o in v]
    return hits[0] if len(hits) == 1 else None


def _split_edit(o: str, c: str):
    """문장 단위 제안을 단어 단위 수정으로 쪼갬 → [(핵심 원래, 핵심 고침, 앞뒤 한 단어 포함 원래, 포함 고침)].
    단어를 넣기만 하거나 지우기만 하는 변경은 버림(내용 추가·삭제 금지)."""
    ow, cw = o.split(), c.split()
    if len(cw) == 1 and len(ow) > 2:  # 원래는 문장, 고친 것은 단어 하나 → 발음이 가장 비슷한 단어를 찾아 그 부분만
        best = max(((sounds_alike(w[:k], c), i, k) for i, w in enumerate(ow) for k in range(1, len(w) + 1)),
                   default=(0, 0, 0))
        sim, i, k = best
        if sim < 0.6:
            return []
        w = ow[i]
        a, b = max(0, i - 1), min(len(ow), i + 2)
        return [(w[:k], c, " ".join(ow[a:b]), " ".join(ow[a:i] + [c + w[k:]] + ow[i + 1:b]))]
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ow, cw, autojunk=False).get_opcodes():
        if tag != "replace":
            continue
        a, b = max(0, i1 - 1), min(len(ow), i2 + 1)
        ctx_o = " ".join(ow[a:b])
        ctx_c = " ".join(ow[a:i1] + cw[j1:j2] + ow[i2:b])
        out.append((" ".join(ow[i1:i2]), " ".join(cw[j1:j2]), ctx_o, ctx_c))
    return out


def check_and_apply(model: str, d: dict, lines: dict, cache: _Cache, context: str, ref: str = "",
                    where=None, max_len: int = 25, evidence_only: bool = False) -> tuple[list, list]:
    """AI가 제안한 수정(d)을 단어 단위로 쪼개고 거른 뒤(_ok_edit) → 근거 확인 또는 재심사 → 통과한 것만 lines에 적용.
    evidence_only: 근거 있는 수정만 (재심사로 통과시키지 않음)
    → (적용된 수정 기록, 확인 필요 목록)"""
    ref_keys = text_key(ref)
    cands, seen = [], set()
    for e in d.get("edits") or []:
        if not isinstance(e, dict):
            continue
        o, c = str(e.get("original", "")).strip(), _PARTICLE.sub(r"\1", _fix(e.get("corrected", "")))
        i = _find_line(lines, str(e.get("id", "")).strip().rstrip(".:"), o)
        if i is None or not o or o == c:
            continue
        for core_o, core_c, ctx_o, ctx_c in _split_edit(o, c):
            if _HANJA.search(core_c) or (i, ctx_o) in seen or ctx_o not in lines[i]:
                continue
            ev = _evidence(core_o, core_c, ref_keys)
            if not ev and len(text_key(core_o)) >= 2 and text_key(core_o) in ref_keys:
                continue  # 원래 단어가 원문·화면 글자에 그대로 있으면 오타가 아님 (예: 'STARR'→'STAR' 방지)
            if _ok_edit(core_o, core_c, max_len, ev):
                seen.add((i, ctx_o))
                cands.append({"id": i, "original": ctx_o, "corrected": ctx_c, "core": (core_o, core_c),
                              "kind": str(e.get("kind", "기타")), "reason": str(e.get("reason", "")).strip(),
                              "evidence": ev})
    need = [e for e in cands if not e["evidence"]]
    passed = _verify(model, need, context, cache) if need and not evidence_only else set()
    done = []
    for e in cands:
        if not e["evidence"] and (need.index(e) + 1) not in passed:
            continue
        if e["original"] not in lines[e["id"]]:  # 앞선 수정과 겹침
            continue
        lines[e["id"]] = lines[e["id"]].replace(e["original"], e["corrected"], 1)
        done.append({"id": e["id"], "where": where(e["id"]) if where else e["id"], "original": e["original"],
                     "corrected": e["corrected"], "kind": e["kind"],
                     "reason": e["reason"] + (" (근거 자료에 있음)" if e["evidence"] else " (재심사 통과)")})
    issues = []
    for x in d.get("issues") or []:
        if isinstance(x, dict) and str(x.get("issue", "")).strip():
            i = str(x.get("id", "")).strip()
            issues.append({"id": i, "where": where(i) if where and i in lines else "",
                           "issue": str(x["issue"]).strip()})
    return done, issues


def _numbered(lines: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in lines.items())


# ───────── 1) 전사본 교정 ─────────

def load_glossary(terms_path) -> str:
    p = Path(terms_path) if terms_path else None
    if not p or not p.is_file():
        return "(없음)"
    t = [l.strip() for l in p.read_text(encoding="utf-8-sig").splitlines() if l.strip() and not l.startswith("#")]
    return ", ".join(t)[:500] or "(없음)"


def _screen_text(slides, events, t0, t1, limit=1000) -> str:
    by_id = {s["id"]: s for s in slides}
    seen, out = set(), []
    for k, (t, sid) in enumerate(events):
        end = events[k + 1][0] if k + 1 < len(events) else t + 600
        if end < t0 - 30 or t > t1 + 30:
            continue
        for line in by_id[sid].get("ocr", "").splitlines():
            if text_key(line) and text_key(line) not in seen:
                seen.add(text_key(line))
                out.append(line.strip())
    return (" / ".join(out))[:limit] or "(없음)"


def correct_transcript(segs, video_name: str, slides, events, terms_path, work: Path, model: str,
                       p0: float = 0.45, p1: float = 0.60, chunk_chars: int = 2500):
    """→ (고친 발화 목록, 수정 기록, 확인 필요 목록). 원본 segs는 바꾸지 않음."""
    ai.ensure_server()
    cache = _Cache(work / "ai_cache.json")
    out = [dict(s) for s in segs]
    chunks, cur, size = [], [], 0
    for i, s in enumerate(out):
        if cur and size + len(s["text"]) > chunk_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(s["text"]) + 8
    if cur:
        chunks.append(cur)
    terms = load_glossary(terms_path)
    log(f"  로컬 AI가 전사본을 {len(chunks)}부분으로 나눠 검토합니다")
    done_all, issues_all = [], []
    for k, idx in enumerate(chunks, 1):
        progress(p0 + (p1 - p0) * (k - 1) / len(chunks), f"전사본 검토 {k}/{len(chunks)}")
        t0, t1 = out[idx[0]]["start"], out[idx[-1]]["end"]
        lines = {str(i + 1): out[i]["text"] for i in idx}
        prompt = TRANSCRIPT_PROMPT.format(name=video_name, start=hms(t0), end=hms(t1), rules=RULES.format(kinds="/".join(KINDS)),
                                          terms=terms, screen=_screen_text(slides, events, t0, t1),
                                          body=_numbered(lines))
        screen = _screen_text(slides, events, t0, t1)
        done, issues = check_and_apply(model, _ask(model, prompt, cache), lines, cache,
                                       context=_numbered(lines), ref=f"{terms} {screen}",
                                       where=lambda i: hms(out[int(i) - 1]["start"]), max_len=25,
                                       # 클로바노트는 원래 정확해 근거 없는 수정은 득보다 실이 큼(실측: 맞는 '수해'→'재해',
                                       # 'STARR'→'STAR'). 화면 글자·전문용어에 근거가 있는 수정만 적용
                                       evidence_only=True)
        for i, txt in lines.items():
            out[int(i) - 1]["text"] = txt
        done_all += done
        issues_all += issues
    log(f"  → 전사본 {len(done_all)}곳 교정, 확인 필요 {len(issues_all)}곳")
    return out, done_all, issues_all


# ───────── 2) 노트 검토 ─────────

def _note_lines(chunks, sel=None):
    """노트 구조 → {'부분-항목': 글} (t=소제목, 1.. = 핵심 항목, r.c = 표 칸)"""
    lines = {}
    for ci, c in enumerate(chunks, 1):
        if sel is not None and ci != sel:
            continue
        lines[f"{ci}-t"] = c["title"]
        for pi, p in enumerate(c["points"], 1):
            lines[f"{ci}-{pi}"] = p
        if c.get("table") and sel is not None:
            tb = c["table"]
            for ri, row in enumerate([tb["header"]] + tb["rows"]):
                for k, cell in enumerate(row):
                    lines[f"{ci}-표{ri}.{k}"] = cell
    return lines


def _put_back(chunks, lines):
    for key, val in lines.items():
        ci, _, rest = key.partition("-")
        c = chunks[int(ci) - 1]
        if rest == "t":
            c["title"] = val
        elif rest.startswith("표"):
            r, k = (int(x) for x in rest[1:].split("."))
            tb = c["table"]
            (tb["header"] if r == 0 else tb["rows"][r - 1])[k] = val
        else:
            c["points"][int(rest) - 1] = val


def review_note(res: dict, chunk_sources: list[str], video_name: str, work: Path, model: str,
                p0: float = 0.88, p1: float = 0.96):
    """AI 노트(res['chunks'])를 원문과 대조해 고침 (res를 직접 바꿈) → (수정 기록, 확인 필요 목록)"""
    cache = _Cache(work / "ai_cache.json")
    chunks = res["chunks"]
    rules = RULES.format(kinds="/".join(KINDS))
    done_all, issues_all = [], []

    def where(i):
        ci = int(i.split("-")[0])
        c = chunks[ci - 1]
        return f"2-{ci}. {c['title'][:20]} ({hms(c['start'])})"

    n = len(chunks)
    for ci in range(1, n + 1):
        progress(p0 + (p1 - p0) * 0.8 * (ci - 1) / n, f"노트 검토 {ci}/{n}")
        c = chunks[ci - 1]
        lines = _note_lines(chunks, ci)
        prompt = NOTE_PROMPT.format(name=video_name, start=hms(c["start"]), end=hms(c["end"]), rules=rules,
                                    items=_numbered(lines), source=chunk_sources[ci - 1][:3800])
        src = chunk_sources[ci - 1][:3800]
        speech = "\n".join(l for l in chunk_sources[ci - 1].splitlines() if l.startswith("("))  # 화면 글자(OCR)는 근거에서 제외
        done, issues = check_and_apply(model, _ask(model, prompt, cache), lines, cache,
                                       context=f"노트 항목:\n{_numbered(lines)}\n\n원문:\n{src[:2500]}", ref=speech,
                                       where=where, max_len=60)
        _put_back(chunks, lines)
        done_all += done
        issues_all += issues
    # 노트 전체의 앞뒤 모순
    progress(p0 + (p1 - p0) * 0.85, "노트 전체 일관성 검토")
    lines = _note_lines(chunks)
    body = _numbered(lines)
    if len(body) > 7000:  # 너무 길면 항목을 앞부분만 (소제목은 모두 유지)
        lines = {k: (v if k.endswith("-t") else v[:120]) for k, v in lines.items()}
        body = _numbered(lines)[:7500]
    prompt = CONSISTENCY_PROMPT.format(name=video_name, rules=rules, items=body)
    full = _note_lines(chunks)
    done, issues = check_and_apply(model, _ask(model, prompt, cache), full, cache, context=body, where=where,
                                   max_len=60)
    _put_back(chunks, full)
    done_all += done
    issues_all += issues
    progress(p1, "노트 검토 완료")
    log(f"  → 노트 {len(done_all)}곳 수정, 확인 필요 {len(issues_all)}곳")
    return done_all, issues_all


# ───────── 3) 아무 .md 파일 검토 ─────────

def review_markdown(md_path: Path, model: str, chunk_chars: int = 2500) -> tuple[Path, Path, int]:
    """→ (검토본 .md, 검토 기록 .md, 수정 수). 원본 파일은 그대로 둠."""
    md_path = Path(md_path)
    ai.ensure_server()
    if not ai.has_model(model):
        raise ai.AIError(f"로컬 AI 모델 {model}이(가) 없습니다. [PC 분석·로컬 AI 설치]에서 받으세요.")
    cache = _Cache(md_path.parent / "_work" / "ai_cache.json") if (md_path.parent / "_work").is_dir() \
        else _Cache(Path(md_path.parent) / f".{md_path.stem}_ai_cache.json")
    raw = md_path.read_text(encoding="utf-8-sig").split("\n")
    outline = " / ".join(l.lstrip("#").strip() for l in raw if l.startswith("#"))[:1200] or "(없음)"
    # 그림·표 구분선·코드·빈 줄은 검토 대상에서 제외
    targets = [i for i, l in enumerate(raw) if l.strip() and not re.match(r"^\s*(!\[|```|\|?\s*:?-{3,}|---\s*$)", l)]
    chunks, cur, size = [], [], 0
    for i in targets:
        if cur and size + len(raw[i]) > chunk_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(raw[i]) + 6
    if cur:
        chunks.append(cur)
    rules = RULES.format(kinds="/".join(KINDS))
    log(f"  로컬 AI({model})가 문서를 {len(chunks)}부분으로 나눠 검토합니다")
    done_all, issues_all = [], []
    for k, idx in enumerate(chunks, 1):
        progress((k - 1) / max(1, len(chunks)), f"문서 검토 {k}/{len(chunks)}")
        lines = {str(i + 1): raw[i] for i in idx}
        prompt = MD_PROMPT.format(name=md_path.name, rules=rules, outline=outline, body=_numbered(lines))
        done, issues = check_and_apply(model, _ask(model, prompt, cache), lines, cache,
                                       context=f"목차: {outline}\n\n{_numbered(lines)}", where=lambda i: f"{i}번째 줄",
                                       max_len=60)
        for i, txt in lines.items():
            raw[int(i) - 1] = txt
        done_all += done
        issues_all += issues
    ai.unload(model)
    out = md_path.with_name(f"{md_path.stem}_검토본.md")
    out.write_text("\n".join(raw), encoding="utf-8")
    rep = md_path.with_name(f"{md_path.stem}_검토기록.md")
    rep.write_text(render_log(f"{md_path.name} 검토 기록", [("문서", done_all, issues_all)], model), encoding="utf-8")
    progress(1.0, "완료")
    log(f"  → {len(done_all)}곳 수정, 확인 필요 {len(issues_all)}곳\n  검토본: {out.name}\n  기록: {rep.name}")
    return out, rep, len(done_all)


# ───────── 기록 ─────────

def _cell(t: str) -> str:
    return str(t).replace("|", "\\|").replace("\n", " ")


def render_log(title: str, parts, model: str) -> str:
    """parts: [(이름, 수정 기록, 확인 필요 목록)] → 마크다운"""
    md = [f"# {title}", "", f"> 로컬 AI({model})가 찾아 고친 곳입니다. 잘못 고친 곳이 있으면 이 기록을 보고 되돌리세요.", ""]
    for name, done, issues in parts:
        md += [f"## {name}: 수정 {len(done)}곳 · 확인 필요 {len(issues)}곳", ""]
        if done:
            md += ["| 위치 | 원래 | 고친 것 | 종류 | 이유 |", "|---|---|---|---|---|"]
            md += [f"| {_cell(d['where'])} | {_cell(d['original'])} | {_cell(d['corrected'])} | {_cell(d['kind'])} | "
                   f"{_cell(d['reason'])} |" for d in done]
            md.append("")
        if issues:
            md += ["**원본 확인이 필요한 곳**", ""]
            md += [f"- {(x['where'] + ' — ') if x['where'] else ''}{x['issue']}" for x in issues]
            md.append("")
    return "\n".join(md)
