# -*- coding: utf-8 -*-
"""마크다운 노트 → Word(.docx) / PDF.

로컬 AI가 정해진 형식(제목·목록·표·그림)으로 노트를 쓰기 때문에, 변환은 규칙대로 해도 일정한 모양이 나옵니다.
Claude가 써 준 .md도 같은 방식으로 변환할 수 있습니다.
  · Word: python-docx로 직접 작성 (맑은 고딕, 제목 색, 표 머리글 음영, 그림 폭 맞춤)
  · PDF : 같은 내용을 HTML로 만든 뒤 Microsoft Edge(Windows 기본 탑재)로 인쇄.
          Edge가 없으면 설치된 Microsoft Word로 .docx를 PDF로 저장.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import log

FONT = "Malgun Gothic"
ACCENT = (0x1F, 0x3A, 0x5F)  # 짙은 남색
IMG_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def split_front_matter(text: str):
    """맨 앞 '---' 블록(YAML 머리말)을 [(키, 값)]으로 분리"""
    meta = []
    if text.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
        if m:
            key = None
            for line in m.group(1).splitlines():
                kv = re.match(r"^([\w\-]+):\s*(.*)$", line)
                if kv:
                    key = kv.group(1)
                    meta.append([key, kv.group(2).strip().strip('"')])
                elif key and line.strip().startswith("-"):
                    meta[-1][1] += ("\n" if meta[-1][1] else "") + line.strip()[1:].strip().strip('"')
            text = text[m.end():]
    return [tuple(x) for x in meta], text


META_LABEL = {"title": "제목", "source_video": "원본 영상", "duration": "길이", "made_by": "작성",
              "provider": "제공", "instructor": "강사", "parts": "구성"}


# ───────── 블록 단위 해석 (Word·HTML 공용) ─────────

def parse_blocks(text: str):
    """→ [(종류, 값)] 종류: h1~h4, p, li(수준, 기호, 글), table(행 목록), img(설명, 경로), caption, quote, code, hr"""
    lines = text.replace("\r", "").split("\n")
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append(("p", " ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            flush()
            i += 1
            continue
        if s.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append(("code", "\n".join(lines[i + 1:j])))
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush()
            out.append((f"h{min(4, len(m.group(1)))}", m.group(2).strip()))
            i += 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            flush()
            out.append(("hr", ""))
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            flush()
            rows = [_cells(s)]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_cells(lines[j].strip()))
                j += 1
            out.append(("table", rows))
            i = j
            continue
        m = IMG_RE.match(s)
        if m:
            flush()
            out.append(("img", (m.group(1), m.group(2))))
            i += 1
            if i < len(lines) and re.fullmatch(r"\*[^*].*\*", lines[i].strip()):  # 바로 아래 *설명* = 그림 설명
                out.append(("caption", lines[i].strip()[1:-1]))
                i += 1
            continue
        if s.startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(("quote", " ".join(q)))
            continue
        m = LIST_RE.match(ln)
        if m:
            flush()
            level = len(m.group(1).replace("\t", "    ")) // 2
            text_ = m.group(3)
            i += 1
            while i < len(lines) and lines[i].startswith("   ") and lines[i].strip() \
                    and not LIST_RE.match(lines[i]):  # 이어지는 줄
                text_ += " " + lines[i].strip()
                i += 1
            out.append(("li", (min(level, 2), m.group(2), text_)))
            continue
        para.append(s)
        i += 1
    flush()
    return out


def _cells(row: str):
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


INLINE_RE = re.compile(r"(\*\*[^*]+?\*\*|__[^_]+?__|\*[^*\s][^*]*?\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\$[^$]+\$)")


def inline_runs(text: str):
    """→ [(글, {'b','i','code'})]"""
    runs = []
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if (part.startswith("**") and part.endswith("**")) or (part.startswith("__") and part.endswith("__")):
            runs.append((part[2:-2], {"b"}))
        elif part.startswith("`") and part.endswith("`"):
            runs.append((part[1:-1], {"code"}))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            runs.append((part[1:-1], {"i"}))
        elif part.startswith("[") and "](" in part:
            runs.append((part[1:part.index("](")], set()))
        elif part.startswith("$") and part.endswith("$") and len(part) > 2:
            runs.append((part[1:-1], {"code"}))
        else:
            runs.append((part, set()))
    return runs


# ───────── Word ─────────

def _set_font(run_or_style, size=None, bold=None, color=None, mono=False):
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    f = run_or_style.font
    name = "Consolas" if mono else FONT
    f.name = name
    el = run_or_style.element if hasattr(run_or_style, "element") else run_or_style._element
    rpr = el.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(a), FONT if a == "w:eastAsia" else name)
    if size:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bold
    if color:
        f.color.rgb = RGBColor(*color)


def _shade(cell_or_par, fill: str):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tgt = cell_or_par._tc.get_or_add_tcPr() if hasattr(cell_or_par, "_tc") else cell_or_par._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tgt.append(shd)


def _border_left(par, color="9CA3AF"):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    ppr = par._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    for k, v in (("w:val", "single"), ("w:sz", "18"), ("w:space", "8"), ("w:color", color)):
        left.set(qn(k), v)
    bdr.append(left)
    ppr.append(bdr)


def _no_autospace(style):
    """Word가 '1차시'를 '1 차시'처럼 숫자·영문과 한글 사이를 벌려 보이지 않게"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    ppr = style.element.get_or_add_pPr()
    for tag in ("w:autoSpaceDE", "w:autoSpaceDN"):
        el = OxmlElement(tag)
        el.set(qn("w:val"), "0")
        ppr.append(el)


def _col_widths(rows, total):
    """열 너비를 글자 수에 비례해 나눔 (짧은 열도 최소 폭 보장)"""
    from docx.shared import Cm

    ncol = max(len(r) for r in rows)
    w = []
    for c in range(ncol):
        lens = [len(r[c]) if c < len(r) else 0 for r in rows]
        w.append(min(60, max(4, sum(lens) / len(lens), max(lens) * 0.5)))
    tot = sum(w)
    mins = Cm(1.4)
    return [max(mins, int(total * x / tot)) for x in w]


def _add_runs(par, text, size=None, color=None, base_bold=False):
    for t, st in inline_runs(text):
        r = par.add_run(t)
        _set_font(r, size=size, bold=("b" in st) or base_bold or None, color=color, mono="code" in st)
        if "i" in st:
            r.italic = True


def md_to_docx(md_path: Path, out: Path | None = None) -> Path:
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    md_path = Path(md_path)
    out = Path(out) if out else md_path.with_suffix(".docx")
    meta, body = split_front_matter(md_path.read_text(encoding="utf-8-sig"))
    d = docx.Document()
    sec = d.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    for side in ("left_margin", "right_margin"):
        setattr(sec, side, Cm(2.0))
    sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(2.0)
    text_w = sec.page_width - sec.left_margin - sec.right_margin

    st = d.styles
    _set_font(st["Normal"], size=10.5)
    st["Normal"].paragraph_format.line_spacing = 1.15  # 맑은 고딕은 기본 줄 간격이 넓음
    _no_autospace(st["Normal"])
    st["Normal"].paragraph_format.space_after = Pt(4)
    for name, size, before in (("Title", 20, 0), ("Heading 1", 17, 18), ("Heading 2", 14, 16),
                               ("Heading 3", 12, 12), ("Heading 4", 11, 10)):
        s = st[name]
        _set_font(s, size=size, bold=True, color=ACCENT)
        s.paragraph_format.space_before = Pt(before)
        s.paragraph_format.space_after = Pt(6)
        s.paragraph_format.keep_with_next = True
    for name in ("List Bullet", "List Bullet 2", "List Bullet 3"):
        _set_font(st[name], size=10.5)
        st[name].paragraph_format.space_after = Pt(2)

    title = next((v for k, v in meta if k == "title"), None)
    blocks = parse_blocks(body)
    if title and blocks and blocks[0][0] == "h1":  # 머리말 제목과 본문 첫 제목이 같으면 한 번만
        blocks = blocks[1:]
    if title:
        d.add_paragraph(title, style="Title")
    shown = [(META_LABEL.get(k, k), v) for k, v in meta if k != "title"]
    if shown:
        t = d.add_table(rows=0, cols=2)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for k, v in shown:
            cells = t.add_row().cells
            cells[0].width, cells[1].width = Cm(3.2), text_w - Cm(3.2)
            _shade(cells[0], "EEF2F7")
            for c, txt, b in ((cells[0], k, True), (cells[1], v, False)):
                c.paragraphs[0].paragraph_format.space_after = Pt(0)
                _add_runs(c.paragraphs[0], txt, size=9, base_bold=b)
        d.add_paragraph()

    num_counter = {}
    for kind, val in blocks:
        if kind[0] == "h" and kind[1:].isdigit():
            lv = int(kind[1])
            p = d.add_paragraph(style="Title" if lv == 1 and not title else f"Heading {lv}")
            _add_runs(p, val, color=ACCENT)
            num_counter.clear()
        elif kind == "p":
            _add_runs(d.add_paragraph(), val)
        elif kind == "li":
            lv, mark, txt = val
            if mark[0].isdigit():
                p = d.add_paragraph()
                pf = p.paragraph_format
                pf.left_indent = Cm(0.9 + 0.7 * lv)
                pf.first_line_indent = Cm(-0.6)
                pf.space_after = Pt(2)
                _add_runs(p, f"{mark} ")
                _add_runs(p, txt)
            else:
                p = d.add_paragraph(style=["List Bullet", "List Bullet 2", "List Bullet 3"][lv])
                _add_runs(p, txt)
        elif kind == "table":
            rows = val
            ncol = max(len(r) for r in rows)
            t = d.add_table(rows=0, cols=ncol)
            t.style = "Table Grid"
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            t.autofit = False
            widths = _col_widths(rows, text_w)
            for ri, r in enumerate(rows):
                cells = t.add_row().cells
                for ci in range(ncol):
                    c = cells[ci]
                    c.width = widths[ci]
                    para = c.paragraphs[0]
                    para.paragraph_format.space_after = Pt(0)
                    para.paragraph_format.line_spacing = 1.0
                    _add_runs(para, r[ci] if ci < len(r) else "", size=9.5, base_bold=ri == 0)
                    if ri == 0:
                        _shade(c, "DCE6F2")
            d.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == "img":
            alt, src = val
            path = (md_path.parent / src).resolve() if not Path(src).is_absolute() else Path(src)
            if path.is_file():
                p = d.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.keep_with_next = True
                p.add_run().add_picture(str(path), width=min(text_w, Cm(14.5)))
            else:
                _add_runs(d.add_paragraph(), f"[그림 없음: {src}]")
        elif kind == "caption":
            p = d.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _add_runs(p, val, size=9, color=(0x55, 0x55, 0x55))
        elif kind == "quote":
            p = d.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.4)
            _border_left(p)
            _add_runs(p, val, size=9.5, color=(0x4B, 0x55, 0x63))
        elif kind == "code":
            p = d.add_paragraph()
            _shade(p, "F3F4F6")
            r = p.add_run(val)
            _set_font(r, size=9, mono=True)
        elif kind == "hr":
            p = d.add_paragraph()
            p.paragraph_format.space_after = Pt(6)

    try:
        d.save(str(out))
    except PermissionError:
        raise RuntimeError(f"{out.name}이(가) Word에서 열려 있어 저장하지 못했습니다. Word를 닫고 다시 시도하세요.")
    return out


# ───────── HTML / PDF ─────────

CSS = """
@page{size:A4;margin:18mm 17mm}
body{font:10.5pt/1.65 "Malgun Gothic","맑은 고딕",sans-serif;color:#1f2328;margin:0}
h1,h2,h3,h4{color:#1f3a5f;line-height:1.35;page-break-after:avoid;break-after:avoid}
h1{font-size:20pt;margin:0 0 10px} h2{font-size:15pt;margin:22px 0 8px;border-bottom:2px solid #1f3a5f;padding-bottom:3px}
h3{font-size:12.5pt;margin:16px 0 6px} h4{font-size:11pt;margin:12px 0 4px}
p{margin:4px 0 6px} ul,ol{margin:4px 0 8px;padding-left:22px} li{margin:2px 0}
table{border-collapse:collapse;width:100%;margin:8px 0 12px;font-size:9.5pt;page-break-inside:auto}
tr{page-break-inside:avoid;break-inside:avoid}
th,td{border:1px solid #b6c2d1;padding:4px 7px;vertical-align:top;text-align:left}
th{background:#dce6f2}
table.meta td{border:none;border-bottom:1px solid #e5e9f0;font-size:9pt;padding:3px 6px} table.meta td:first-child{background:#eef2f7;font-weight:700;width:85px}
figure{margin:10px 0 14px;text-align:center;page-break-inside:avoid;break-inside:avoid}
figure img{max-width:88%;max-height:95mm;border:1px solid #d0d7de;border-radius:4px}
figcaption{font-size:9pt;color:#555;margin-top:4px}
blockquote{margin:8px 0;padding:4px 12px;border-left:4px solid #9ca3af;color:#4b5563;background:#f8fafc}
code{font-family:Consolas,monospace;font-size:9pt;background:#f3f4f6;padding:0 3px;border-radius:3px}
pre{background:#f3f4f6;padding:8px;white-space:pre-wrap;font-size:9pt}
hr{border:none;border-top:1px solid #d0d7de;margin:14px 0}
"""


def _inline_html(text: str) -> str:
    out = []
    for t, st in inline_runs(text):
        t = html.escape(t)
        if "code" in st:
            t = f"<code>{t}</code>"
        if "b" in st:
            t = f"<b>{t}</b>"
        if "i" in st:
            t = f"<i>{t}</i>"
        out.append(t)
    return "".join(out)


def md_to_html(md_path: Path, absolute_images: bool = True) -> str:
    md_path = Path(md_path)
    meta, body = split_front_matter(md_path.read_text(encoding="utf-8-sig"))
    title = next((v for k, v in meta if k == "title"), md_path.stem)
    blocks = parse_blocks(body)
    h = []
    if blocks and blocks[0][0] == "h1":
        h.append(f"<h1>{_inline_html(blocks[0][1])}</h1>")
        blocks = blocks[1:]
    else:
        h.append(f"<h1>{html.escape(title)}</h1>")
    shown = [(META_LABEL.get(k, k), v) for k, v in meta if k != "title"]
    if shown:
        h.append("<table class='meta'>" + "".join(
            f"<tr><td>{html.escape(k)}</td><td>{_inline_html(v).replace(chr(10), '<br>')}</td></tr>" for k, v in shown)
                 + "</table>")
    open_list = []  # 열린 목록 태그 스택 [(태그, 수준)]

    def close_lists(to=-1):
        while open_list and open_list[-1][1] > to:
            h.append(f"</{open_list.pop()[0]}>")

    for kind, val in blocks:
        if kind != "li":
            close_lists()
        if kind[0] == "h" and kind[1:].isdigit():
            h.append(f"<{kind}>{_inline_html(val)}</{kind}>")
        elif kind == "p":
            h.append(f"<p>{_inline_html(val)}</p>")
        elif kind == "li":
            lv, mark, txt = val
            tag = "ol" if mark[0].isdigit() else "ul"
            close_lists(lv)
            if not open_list or open_list[-1][1] < lv:
                num = re.sub(r"\D", "", mark) or "1"
                start = f" start='{int(num)}'" if tag == "ol" else ""
                h.append(f"<{tag}{start}>")
                open_list.append((tag, lv))
            h.append(f"<li>{_inline_html(txt)}</li>")
        elif kind == "table":
            rows = val
            th = "".join(f"<th>{_inline_html(c)}</th>" for c in rows[0])
            trs = "".join("<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            h.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
        elif kind == "img":
            alt, src = val
            p = (md_path.parent / src).resolve()
            url = p.as_uri() if absolute_images and p.exists() else html.escape(src)
            h.append(f"<figure><img src='{url}' alt='{html.escape(alt)}'>")
            h.append("</figure>")
        elif kind == "caption":
            if h and h[-1] == "</figure>":
                h[-1] = f"<figcaption>{_inline_html(val)}</figcaption></figure>"
            else:
                h.append(f"<p><i>{_inline_html(val)}</i></p>")
        elif kind == "quote":
            h.append(f"<blockquote>{_inline_html(val)}</blockquote>")
        elif kind == "code":
            h.append(f"<pre>{html.escape(val)}</pre>")
        elif kind == "hr":
            h.append("<hr>")
    close_lists()
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{''.join(h)}</body></html>")


def _edge() -> str | None:
    for c in (shutil.which("msedge"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              shutil.which("chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if c and Path(c).is_file():
            return c
    return None


def _pdf_with_edge(html_text: str, out: Path) -> bool:
    exe = _edge()
    if not exe:
        return False
    with tempfile.TemporaryDirectory(prefix="lk_pdf_") as td:
        src = Path(td) / "note.html"
        src.write_text(html_text, encoding="utf-8")
        tmp_pdf = Path(td) / "note.pdf"
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        try:
            subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                            "--allow-file-access-from-files", f"--user-data-dir={Path(td) / 'profile'}",
                            f"--print-to-pdf={tmp_pdf}", src.as_uri()],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=180, **kw)
        except (subprocess.TimeoutExpired, OSError):
            return False
        if not tmp_pdf.is_file() or tmp_pdf.stat().st_size < 1000:
            return False
        shutil.move(str(tmp_pdf), str(out))
    return True


def _pdf_with_word(docx_path: Path, out: Path) -> bool:
    if os.name != "nt":
        return False
    ps = ("$ErrorActionPreference='Stop';$w=New-Object -ComObject Word.Application;$w.Visible=$false;"
          "$w.DisplayAlerts=0;try{$d=$w.Documents.Open($env:LK_IN,$false,$true);"
          "$d.SaveAs([ref]$env:LK_OUT,[ref]17);$d.Close([ref]0)}finally{$w.Quit()}")
    env = dict(os.environ, LK_IN=str(docx_path), LK_OUT=str(out))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=240, creationflags=0x08000000)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and out.is_file()


def md_to_pdf(md_path: Path, out: Path | None = None, docx_path: Path | None = None) -> Path:
    md_path = Path(md_path)
    out = Path(out) if out else md_path.with_suffix(".pdf")
    try:
        if out.exists():
            out.unlink()
    except PermissionError:
        raise RuntimeError(f"{out.name}이(가) 다른 프로그램에서 열려 있어 저장하지 못했습니다. 닫고 다시 시도하세요.")
    if _pdf_with_edge(md_to_html(md_path), out):
        return out
    log("  Edge로 PDF를 만들지 못해 Word로 시도합니다…")
    if docx_path is None or not Path(docx_path).is_file():
        docx_path = md_to_docx(md_path, Path(tempfile.gettempdir()) / f"lk_{os.getpid()}.docx")
    if _pdf_with_word(Path(docx_path), out):
        return out
    raise RuntimeError("PDF를 만들지 못했습니다. Microsoft Edge나 Word가 설치되어 있는지 확인하세요.")


def export(md_path: Path, word: bool = True, pdf: bool = True) -> list[Path]:
    """노트 .md → 같은 폴더에 .docx / .pdf"""
    made = []
    dx = None
    if word:
        dx = md_to_docx(md_path)
        made.append(dx)
        log(f"  Word 문서: {dx.name}")
    if pdf:
        pf = md_to_pdf(md_path, docx_path=dx)
        made.append(pf)
        log(f"  PDF 문서: {pf.name}")
    return made
