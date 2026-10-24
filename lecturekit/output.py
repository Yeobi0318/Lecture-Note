# -*- coding: utf-8 -*-
"""결과물 만들기.

<영상이름>_notes\
  <영상이름>_노트.md     로컬 AI가 쓴 정리 노트 (이미지 포함)  → 선택 시 .docx / .pdf 도 생성
  attach\                 노트에 넣은 중요 장면 캡처 (Claude에 맡길 때도 이 이미지를 첨부)
  Claude용_자료.md        로컬 노트가 마음에 들지 않을 때 Claude에 맡기는 자료 (정리 요청문 포함)
  review.html             모든 화면과 발화를 나란히 보는 복습 페이지
  transcript.srt          전사본 (동영상 자막으로도 사용 가능)
  _work\                  중간 자료 (감지한 모든 화면, AI 캐시 등)
"""
from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

from .common import hms, text_key
from .ocr import unreliable_score
from .sections import LineDedup, build_sections, paragraphs

PART_SIZE = 20  # Claude 채팅 한 번에 보낼 이미지 묶음 크기
CLAUDE_MD = "Claude용_자료.md"
_OUR_IMG = re.compile(r"^\d{2,3}_.*\.jpg$", re.I)
_OLD_FILES = ("notes_input.md", "결과_안내.txt", "transcript.txt", "slides.json")  # v2가 만들던 파일


def note_path(out_dir: Path, video: Path) -> Path:
    return out_dir / f"{Path(video).stem}_노트.md"


def choose_heuristic(slides, groups, max_images: int):
    """로컬 AI 없이 고를 때: OCR을 믿기 어려운(표·수식·빽빽한 문서) 장면 우선 → 대표 슬라이드 id(시간순)"""
    by_id = {s["id"]: s for s in slides}
    reps = [by_id[g["rep"]] for g in groups if not by_id[g["rep"]].get("blank")]
    cand = sorted((s for s in reps if unreliable_score(s) >= 1.0), key=lambda s: -unreliable_score(s))
    keep = {s["id"] for s in cand[:max_images]}
    return [s["id"] for s in sorted(reps, key=lambda s: s["t_first"]) if s["id"] in keep]


def _clean_attach(att: Path):
    """이전에 이 프로그램이 넣은 이미지만 지움 (사용자가 넣어 둔 다른 파일은 보존)"""
    if not att.is_dir():
        return
    for p in list(att.rglob("*.jpg")):
        if _OUR_IMG.match(p.name):
            try:
                p.unlink()
            except OSError:
                pass
    for d in sorted(att.glob("part*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def copy_attachments(out_dir: Path, slides, chosen_ids, captions=None):
    """선택한 장면을 attach\\에 복사 → [{num, sid, gid, t, file, tokens, caption}]"""
    from PIL import Image

    by_id = {s["id"]: s for s in slides}
    att = out_dir / "attach"
    _clean_attach(att)
    n_parts = (len(chosen_ids) + PART_SIZE - 1) // PART_SIZE
    out = []
    for k, sid in enumerate(chosen_ids, 1):
        s = by_id[sid]
        folder = att / f"part{(k - 1) // PART_SIZE + 1}" if n_parts > 1 else att
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / f"{k:02d}_{hms(s['t_first']).replace(':', '-')}.jpg"
        shutil.copy2(s["image"], dst)
        w, h = Image.open(dst).size
        out.append({"num": k, "sid": sid, "gid": s.get("group"), "t": s["t_first"],
                    "file": dst.relative_to(out_dir).as_posix(), "tokens": w * h // 750,
                    "caption": (captions or {}).get(s.get("group"), "")})
    return out, n_parts


def remove_old_outputs(out_dir: Path):
    for name in _OLD_FILES:
        try:
            (out_dir / name).unlink()
        except OSError:
            pass
    old = out_dir / "slides"  # v2의 슬라이드 폴더 (이제 _work\slides)
    if old.is_dir() and all(re.match(r"S\d{3}_.*\.jpg$", p.name) for p in old.iterdir()):
        shutil.rmtree(old, ignore_errors=True)


# ───────── 로컬 AI 노트 ─────────

def _md_table(tb) -> list[str]:
    esc = lambda c: c.replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = []
    if tb.get("caption"):
        lines.append(f"**{tb['caption']}**\n")
    lines.append("| " + " | ".join(esc(h) for h in tb["header"]) + " |")
    lines.append("|" + "---|" * len(tb["header"]))
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in tb["rows"]]
    return lines


def write_note_md(out_dir: Path, video: Path, duration: float, res: dict, attached) -> Path:
    from .analyze import dedupe_points

    chunks = res["chunks"]
    dedupe_points(chunks)
    title = res["title"]
    md = ["---", f"title: {title}", f"source_video: {Path(video).name}", f'duration: "{hms(duration)}"',
          f"made_by: lecturekit 로컬 AI ({res['model']})", "---", "", f"# {title}", ""]
    md.append(f"> 영상 길이 {hms(duration)} · 로컬 AI가 작성한 초안입니다. "
              f"중요한 내용은 원본 영상이나 복습 페이지(review.html)로 확인하세요.")
    md.append("")
    if res.get("keywords"):
        md += ["**핵심어:** " + ", ".join(res["keywords"]), ""]
    md += ["## 1. 강의 개요", ""]
    md += [f"- {o}" for o in res.get("overview") or ["(개요를 만들지 못했습니다)"]]
    md += ["", "## 2. 구간별 핵심 내용", ""]
    imgs = sorted(attached, key=lambda a: a["t"])
    for k, c in enumerate(chunks, 1):
        md += [f"### 2-{k}. {c['title']} ({hms(c['start'])}–{hms(c['end'])})", ""]
        md += [f"- {p}" for p in c["points"]]
        md.append("")
        if c.get("table"):
            md += _md_table(c["table"]) + [""]
        lo = 0.0 if k == 1 else c["start"]  # 첫 부분보다 앞선 그림·마지막 부분 뒤의 그림도 빠지지 않게
        hi = float("inf") if k == len(chunks) else c["end"]
        for a in imgs:
            if lo <= a["t"] < hi:
                cap = f" — {a['caption']}" if a.get("caption") else ""
                md += [f"![그림 {a['num']:02d}]({a['file']})", f"*그림 {a['num']:02d} · {hms(a['t'])}{cap}*", ""]
    # 개요 단계에서 AI가 합친 문장을 쓰고, 없으면 부분별 강조 중 문장 형태인 것만 사용
    emph = res.get("emphasis") or [e for c in chunks for e in c.get("emphasis", []) if len(text_key(e)) >= 15][:12]
    md += ["## 3. 강사가 강조·반복한 내용", ""]
    md += [f"- {e}" for e in _uniq(emph)] or ["- (특별히 강조한 내용을 찾지 못했습니다)"]
    unc = [u for c in chunks for u in c.get("uncertain", [])] + list(res.get("review_issues") or [])
    md += ["", "## 4. 원본 확인이 필요한 부분", ""]
    md += [f"- {u}" for u in _uniq(unc)] or ["- (없음)"]
    md.append("")
    p = note_path(out_dir, video)
    p.write_text("\n".join(md), encoding="utf-8")
    return p


def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        k = text_key(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


# ───────── Claude에 맡길 자료 ─────────

PROMPT = """# 강의 정리 요청

아래는 강의 영상 "{name}"(길이 {dur})에서 자동 추출한 자료입니다. 각 [구간]에 그 시간대의 화면 정보와 강의 발화(음성 전사)가 있습니다.

## 자료 읽는 법
- **"이미지 NN"** 표시가 있는 구간은 함께 첨부한 이미지(attach 폴더, 파일 이름이 NN_로 시작)를 직접 보고 읽어 주세요. 이미지가 가장 정확한 정보입니다.
- 이미지가 없는 구간의 "화면 글자"는 OCR 결과라 받침 오류(을→올, 를→름, 했→햇 등)가 있을 수 있습니다. 문맥으로 바로잡아 읽어 주세요.
- 화면 글자는 강의 중 처음 나온 줄만 적었습니다(반복되는 제목·머리글은 생략).
- 음성 전사 오류(동음이의어, 전문용어·영문 약어)는 화면 내용과 문맥을 근거로 바로잡아 주세요.
{parts_note}
## 작성 규칙
- 통계·수치는 약간 부정확해 보여도 인식된 그대로 씁니다. 큰 오류나 논리 모순만 예외입니다.
- 강사의 말실수나 논리 불일치는 따로 표시하지 말고, 문맥과 추론으로 바로잡아 씁니다.
- 결과는 **마크다운 노트**(.md)로 작성합니다. 비교·유형·단계는 표로 정리합니다.

## 정리 형식
1. 강의 개요 (3~5줄)
2. 구간별 핵심 내용: 개념·정의, 슬라이드의 표·도식·예시 문서의 요지, 수식(LaTeX), 예제 풀이 흐름
3. 강의자가 강조·반복한 내용, 과제·시험 관련 언급
4. 원본 확인이 필요한 부분(인식 오류 의심, 이미지로도 판독이 어려운 곳)과 시각

"""


def write_claude_md(out_dir: Path, video: Path, slides, events, segs, duration, attached, n_parts) -> tuple[Path, int]:
    by_id = {s["id"]: s for s in slides}
    sections = build_sections(events, segs, duration)
    img_num = {a["gid"]: a["num"] for a in attached}
    dedup, hi_dedup, blocks, seen_group = LineDedup(), LineDedup(), [], set()
    for sec in sections:
        sid = sec["slide"]
        s = by_id.get(sid)
        lines, img, ref = [], None, None
        if s:
            lines = dedup.new_lines(s.get("ocr", ""))
            g = s["group"]
            if g in img_num:
                if g not in seen_group:
                    img = img_num[g]
                else:
                    ref = img_num[g]  # 같은 장면의 다른 모습(강조·말풍선 변화)
                # 이미지가 있으면 OCR은 보조: 신뢰도 높은 짧은 글자 상자(말풍선·제목)만 남김
                lines = hi_dedup.new_lines(s.get("ocr_hi", ""))
            seen_group.add(g)
        substantial = sum(len(text_key(l)) for l in lines) >= 15
        if (not blocks or s is None or substantial or img
                or (blocks[-1]["img"] is None and not blocks[-1]["slides"])):
            blocks.append({"start": sec["start"], "end": sec["end"], "slides": [sid] if sid else [],
                           "lines": lines, "img": img, "ref": ref, "segs": list(sec["segs"])})
        else:
            b = blocks[-1]
            b["end"] = sec["end"]
            b["segs"] += sec["segs"]
            b["lines"] += lines
            if sid and sid not in b["slides"]:
                b["slides"].append(sid)

    parts_note = ""
    if n_parts > 1:
        parts_note = (f"- 첨부 이미지가 {len(attached)}장이라 {n_parts}번에 나눠 보냅니다(attach/part1 … part{n_parts}). "
                      f"제가 \"전송 끝\"이라고 할 때까지 정리를 시작하지 말고 기다려 주세요.\n")
    md = [PROMPT.format(name=Path(video).name, dur=hms(duration), parts_note=parts_note)]
    for k, b in enumerate(blocks, 1):
        head = f"## [구간 {k}] {hms(b['start'])} – {hms(b['end'])}"
        if b["img"]:
            head += f" · 이미지 {b['img']:02d}"
        elif b.get("ref"):
            head += f" · 이미지 {b['ref']:02d}과 같은 장면(강조 표시 등이 바뀜)"
        md.append(head)
        if b["lines"]:
            md.append("화면 글자: " + " / ".join(b["lines"]))
        paras = paragraphs(b["segs"])
        if paras:
            md.append("\n".join(f"({hms(t)}) {p}" for t, p in paras))
        md.append("")
    text = "\n".join(md)
    p = out_dir / CLAUDE_MD
    p.write_text(text, encoding="utf-8")
    return p, len(blocks)


# ───────── 복습 페이지 ─────────

def write_review(out_dir: Path, video: Path, slides, events, segs, duration, attached):
    e = html.escape
    by_id = {s["id"]: s for s in slides}
    img_num = {a["gid"]: a["num"] for a in attached}
    body = []
    for sec in build_sections(events, segs, duration):
        s = by_id.get(sec["slide"])
        left = ""
        if s:
            rel = Path(s["image"]).relative_to(out_dir).as_posix()
            tag = f" <span class='tag'>그림 {img_num[s['group']]:02d}</span>" if s["group"] in img_num else ""
            left = (f"<a href='{rel}' target='_blank'><img src='{rel}' loading='lazy'></a>"
                    f"<div class='meta'>화면 S{s['id']:03d} · 장면 {s['group']}{tag}</div>")
            if s.get("ocr"):
                left += f"<details><summary>화면 글자(OCR, 오류 가능)</summary><pre>{e(s['ocr'])}</pre></details>"
        paras = paragraphs(sec["segs"])
        speech = "".join(f"<p><span class='t'>{hms(t)}</span>{e(p)}</p>" for t, p in paras) or "<p class='meta'>(발화 없음)</p>"
        body.append(f"<section><h2>{hms(sec['start'])} – {hms(sec['end'])}</h2>"
                    f"<div class='row'><div class='img'>{left}</div><div class='txt'>{speech}</div></div></section>")
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(Path(video).stem)} — 복습 페이지</title>
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--card:#fff;--line:#e7e5e4;--acc:#b45309}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--card:#292524;--line:#44403c;--acc:#f59e0b}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 "Malgun Gothic",system-ui,sans-serif}}
main{{max-width:1240px;margin:auto;padding:16px}} h1{{font-size:22px;margin:8px 0}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:12px 0}}
h2{{font-size:15px;margin:0 0 8px;color:var(--mut);font-weight:600}}
.row{{display:flex;gap:18px;flex-wrap:wrap}} .img{{flex:1 1 440px;max-width:600px}} .txt{{flex:1 1 340px}}
img{{width:100%;border-radius:6px;border:1px solid var(--line)}} .meta{{color:var(--mut);font-size:13px}}
.tag{{background:var(--acc);color:#fff;border-radius:4px;padding:0 6px;margin-left:4px}}
.t{{color:var(--acc);font-family:monospace;font-size:13px;margin-right:8px}} p{{margin:0 0 8px}}
pre{{white-space:pre-wrap;font-size:13px}}
</style></head><body><main><h1>{e(Path(video).stem)}</h1>
<p class="meta">길이 {hms(duration)} · 화면 {len(by_id)}장 · 노트에 넣은 그림 {len(attached)}장</p>
{''.join(body)}</main></body></html>"""
    (out_dir / "review.html").write_text(page, encoding="utf-8")


def save_result(out_dir: Path, result: dict):
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
