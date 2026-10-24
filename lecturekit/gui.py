# -*- coding: utf-8 -*-
"""강의 영상 정리 도구 — GUI (START.bat으로 실행)

분석 작업은 별도 프로세스(python -m lecturekit.cli)로 실행해 창이 멈추지 않게 하고,
출력 줄을 읽어 로그·진행 막대를 갱신한다. ('@@P 비율 설명' = 진행률, '@@R {json}' = 결과, '@@F [파일]' = 만든 문서)
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .ai import DEFAULT_MODEL, MODEL_CHOICES
from .common import TOOL_DIR, VIDEO_EXTS
from .speech import PRESET_DESC
from .transcript import TRANSCRIPT_EXTS, find_external_transcript

SETTINGS = TOOL_DIR / "settings.json"
APP_TITLE = f"강의 영상 정리 도구 v{__version__}"

HELP_TEXT = """\
■ 이 프로그램이 하는 일
  강의 영상의 화면과 음성(클로바노트 전사본)을 내 컴퓨터에서 분석하고,
  내 PC의 로컬 AI가 강의 전체를 읽어 ① 정리 노트와 ② 중요한 장면 캡처를 만듭니다.
  원하면 노트를 Word·PDF로 바로 만들 수 있습니다. 이 과정에서 Claude 토큰은 쓰지 않습니다.

■ 사용 순서
  1. [① 영상 분석] 탭에서 '영상 추가'로 영상을 고릅니다 (여러 개 가능).
  2. 클로바노트 전사본을 연결합니다.
     · 아직 없으면: 영상을 고르고 [클로바노트용 음성 추출] → 생긴 .m4a 파일을 클로바노트(clovanote.naver.com)에 올림
       → 변환이 끝나면 '음성 기록 다운로드'에서 타임스탬프를 포함해 .txt(또는 .docx)로 받음
     · 받은 파일을 영상과 같은 폴더에 두면 자동으로 찾습니다(이름: 영상이름.txt 또는 영상이름_audio.txt).
       이름이 다르면 [전사본 지정…]으로 직접 고르세요.
  3. 필요하면 'Word로도 저장', 'PDF로도 저장'을 체크하고 [▶ 분석 시작]을 누릅니다.
  4. 끝나면 [② 결과 보기] 탭에서 노트(Word·PDF·md)를 엽니다.

■ 노트가 마음에 들지 않으면 — Claude에 맡기기
  [② 결과 보기]에서 [Claude용 자료 복사] → Claude 채팅에 붙여넣기 → [캡처 폴더 열기]의 이미지를 모두 끌어다 놓기 → 전송.
  Claude가 써 준 .md 파일은 [md 파일을 Word·PDF로 변환…]으로 문서로 만들 수 있습니다.

■ 결과 폴더(영상이름_notes)에 생기는 것
  · 영상이름_노트.md (.docx/.pdf)   로컬 AI가 쓰고 검토한 정리 노트
  · 검토_기록.md                   AI가 전사본·노트에서 고친 곳과 이유 (원래 글 포함)
  · attach\\                        노트에 넣은 중요 장면 캡처 (Claude에 맡길 때도 이 이미지를 첨부)
  · Claude용_자료.md               Claude에 맡길 때 붙여 넣을 자료 (정리 요청문 포함)
  · review.html                    모든 화면과 발화를 나란히 보는 복습 페이지
  · transcript.srt                 교정된 전사본 (동영상 자막으로도 사용 가능)
  · _work\\                         중간 자료 (지워도 되지만, 두면 다시 분석할 때 빠릅니다)

■ 로컬 AI
  · 무료 오픈소스 AI 실행기 Ollama와 Qwen3.5 모델을 씁니다. [PC 분석·로컬 AI 설치]를 누르면
    이 PC의 GPU·메모리·디스크를 확인해 알맞은 모델을 추천하고 설치까지 해 줍니다 (처음 한 번).
  · '캡처 최대 장수'만큼 중요한 장면을 고릅니다. AI가 후보 화면을 직접 보고 표지·강사 얼굴 같은 장면은 뺍니다.

■ 로컬 AI 검토·교정 (자동)
  · 전사본 교정: 클로바노트 전사본을 그대로 쓰지 않고, AI가 앞뒤 문맥을 보며 잘못 들은 단어·용어 표기를 찾습니다.
    클로바노트는 원래 정확한 편이라, 고친 말이 슬라이드 글자나 전문용어 목록에 있는(근거 있는) 수정만 적용합니다.
    → '전문용어 편집'에 과목 용어를 적어 두면 더 많이 바로잡습니다.
  · 노트 검토: AI가 쓴 노트를 원문과 다시 대조해 오탈자·원문과 다른 내용·앞뒤 모순을 고칩니다.
  · 고친 곳은 모두 결과 폴더의 '검토_기록.md'에 원래 글과 함께 남습니다([검토 기록 보기]).
    AI가 확신하지 못한 곳은 고치지 않고 노트의 '4. 원본 확인이 필요한 부분'에 적습니다.
  · 잘못 고치는 것을 막기 위해 보수적으로 동작합니다: 근거(슬라이드 글자·원문)가 있거나 명백한 오타만 고치고,
    말투·어미·숫자는 바꾸지 않습니다. 그래서 모든 오류를 찾아내지는 못합니다.
  · Claude가 써 준 정리본 같은 다른 .md 파일도 [md 파일 검토·교정…]으로 검토할 수 있습니다(원본은 그대로, '_검토본.md' 생성).
  · 로컬 AI 노트는 초안입니다. 수치·고유명사 등 중요한 내용은 복습 페이지나 원본 영상으로 확인하세요.

■ 정확도를 높이는 방법
  · 클로바노트 전사본을 쓰면 음성 인식이 가장 정확합니다.
  · 클로바노트 없이 내 PC에서 인식할 때는 '전문용어 편집'에 과목 용어를 한 줄에 하나씩 적어 두세요.
  · 화면 한쪽에 강사 웹캠이 있어 화면이 지나치게 많이 잡히면 고급 설정의 '분석 영역'을 쓰세요.

■ 문제 해결
  · '로컬 AI 설치 필요' → [로컬 AI 설치]를 누르고 끝날 때까지 기다리세요(처음 한 번, 약 5GB 다운로드).
  · 'GPU 음성 인식 실패' → NVIDIA 드라이버 업데이트 후 [설치 점검].
  · 분석이 너무 느림 → [PC 분석·로컬 AI 설치]에서 'GPU에 전부 들어감'으로 표시된 모델을 고르세요.
  · Word·PDF 저장 실패 → 같은 이름의 문서가 Word나 PDF 보기 프로그램에 열려 있으면 닫고 다시 시도하세요.
  · 그 밖의 오류는 아래 로그 내용을 복사해 질문하세요.
"""


def _open(path):
    path = str(path)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path])


def _child_python() -> str:
    """분석용 파이썬: GUI가 pythonw.exe로 떠 있어도 콘솔용 python.exe 사용"""
    if os.environ.get("LK_PYTHON"):
        return os.environ["LK_PYTHON"]
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


class Runner:
    """분석 프로세스 실행·중지와 출력 줄 수집"""

    def __init__(self):
        self.proc = None
        self.q = queue.Queue()

    def start(self, args):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", LK_GUI="1")
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        self.proc = subprocess.Popen([_child_python(), "-u", "-m", "lecturekit.cli", *args], cwd=str(TOOL_DIR),
                                     env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **kw)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        for line in proc.stdout:
            self.q.put(("line", line.rstrip("\n")))
        self.q.put(("done", proc.wait()))

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if not self.running():
            return
        if os.name == "nt":  # ffmpeg 등 하위 프로세스까지 종료
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                           capture_output=True, creationflags=0x08000000)
        else:
            self.proc.terminate()


class ScrollFrame(ttk.Frame):
    """세로 스크롤이 되는 영역 (작은 화면·높은 배율에서도 설정이 잘리지 않게)"""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")

    def _on_resize(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        for w in self.inner.winfo_children():  # 안내 문구 줄바꿈 폭을 창 폭에 맞춤
            if isinstance(w, ttk.Label) and str(w.cget("style")) == "Hint.TLabel":
                w.configure(wraplength=max(200, e.width - 30))

    def _wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        if w is not None and str(w).startswith(str(self)):
            self.canvas.yview_scroll(int(-e.delta / 120), "units")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.runner = Runner()
        self.jobs = []          # 실행 대기 [(설명, args, 완료 후 콜백)]
        self.job_total = 0
        self.job_idx = 0
        self.on_job_done = None
        self.on_all_done = None
        self.results = []
        self.row_out = {}       # 결과 목록 행 id → 결과 폴더
        self.videos = []        # [{"path": str, "transcript": str|None(직접 지정)}]
        self.made_files = []
        self.cfg = self._load_settings()
        self._setup_style()
        self._build()
        self._no_wheel()
        self.root.after(100, self._poll)
        self.refresh_ai_status()

    # ───────── 기본 설정 ─────────
    def _setup_style(self):
        fams = set(tkfont.families())
        fam = next((f for f in ("Malgun Gothic", "맑은 고딕", "Noto Sans CJK KR", "NanumGothic", "notosanskr") if f in fams), None)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            f = tkfont.nametofont(name)
            if fam:
                f.configure(family=fam)
            f.configure(size=10)
        st = ttk.Style()
        if "vista" in st.theme_names():
            st.theme_use("vista")
        elif "clam" in st.theme_names():
            st.theme_use("clam")
        self.hint_font = tkfont.Font(family=fam or "TkDefaultFont", size=9)
        self.bold = tkfont.Font(family=fam or "TkDefaultFont", size=10, weight="bold")
        st.configure("Hint.TLabel", foreground="#666666", font=self.hint_font)
        st.configure("Ok.TLabel", foreground="#15803d", font=self.bold)
        st.configure("Warn.TLabel", foreground="#b45309", font=self.bold)
        st.configure("Big.TButton", font=self.bold, padding=(14, 6))
        rh = int(tkfont.nametofont("TkDefaultFont").metrics("linespace") * 1.5)
        st.configure("Treeview", rowheight=rh)

    def _load_settings(self):
        d = {"speech": "external", "preset": "light", "redo": False, "noslides": False,
             "outmode": "beside", "outdir": "", "model": "large-v3", "sens": "normal", "crop": "", "lastdir": "",
             "use_ai": True, "ai_model": DEFAULT_MODEL, "vision": True, "max_images": 12, "word": True, "pdf": True,
             "fix_tr": True, "review": True}
        try:
            d.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        except Exception:
            pass
        if d.get("speech") not in ("external", "auto"):
            d["speech"] = "external"
        return d

    def _save_settings(self):
        d = {"speech": self.v_speech.get(), "preset": self._preset_key(),
             "redo": self.v_redo.get(), "noslides": self.v_noslides.get(), "outmode": self.v_outmode.get(),
             "outdir": self.v_outdir.get(), "model": self.v_model.get(), "sens": self.v_sens.get(),
             "crop": self.v_crop.get(), "lastdir": self.cfg.get("lastdir", ""),
             "use_ai": self.v_ai.get(), "ai_model": self._ai_model(), "vision": self.v_vision.get(),
             "max_images": self._max_images(), "word": self.v_word.get(), "pdf": self.v_pdf.get(),
             "fix_tr": self.v_fixtr.get(), "review": self.v_review.get()}
        try:
            SETTINGS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    # ───────── 화면 구성 ─────────
    def _build(self):
        r = self.root
        r.title(APP_TITLE)
        sc = max(1.0, r.winfo_fpixels("1i") / 96)  # 화면 배율(125% → 1.25)
        w = min(int(1120 * sc), r.winfo_screenwidth() - 40)
        h = min(int(820 * sc), r.winfo_screenheight() - 80)
        r.geometry(f"{w}x{h}")
        r.minsize(min(w, int(880 * sc)), min(h, int(620 * sc)))
        self._build_bottom(r)  # 아래쪽(진행 막대·로그)을 먼저 배치해 창이 작아도 항상 보이게 함
        nb = ttk.Notebook(r)
        nb.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 4))
        self.nb = nb
        t1, t2, t3, t4 = (ttk.Frame(nb, padding=10) for _ in range(4))
        nb.add(t1, text="  ① 영상 분석  ")
        nb.add(t2, text="  ② 결과 보기  ")
        nb.add(t3, text="  ③ 정확도 도구(선택)  ")
        nb.add(t4, text="  도움말  ")
        self._build_analyze(t1)
        self._build_results(t2)
        self._build_tools(t3)
        self._build_help(t4)

    def _no_wheel(self):
        """선택 상자 위에서 마우스 휠을 굴려도 값이 바뀌지 않게 (설정 칸을 스크롤하다 실수로 바뀌는 것 방지)"""
        self.root.bind_class("TCombobox", "<MouseWheel>", lambda e: "break")
        self.root.bind_class("TSpinbox", "<MouseWheel>", lambda e: "break")

    def _hint(self, parent, text, wrap=470, **grid):
        lb = ttk.Label(parent, text=text, style="Hint.TLabel", wraplength=wrap, justify="left")
        lb.grid(**grid)
        return lb

    def _build_analyze(self, f):
        f.columnconfigure(0, weight=3)
        f.columnconfigure(1, weight=2)
        f.rowconfigure(0, weight=1)

        # 영상 목록 + 전사본
        lf = ttk.LabelFrame(f, text=" 1. 영상과 클로바노트 전사본 ", padding=8)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(1, weight=1)
        self.lbl_count = ttk.Label(lf, text="선택된 영상: 0개", font=self.bold)
        self.lbl_count.grid(row=0, column=0, sticky="w")
        box = ttk.Frame(lf)
        box.grid(row=1, column=0, sticky="nsew", pady=4)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.vt = ttk.Treeview(box, columns=("video", "tr"), show="headings", selectmode="extended", height=8)
        self.vt.heading("video", text="영상")
        self.vt.heading("tr", text="전사본 (클로바노트)")
        self.vt.column("video", width=300)
        self.vt.column("tr", width=230)
        self.vt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.vt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.vt.configure(yscrollcommand=sb.set)
        bf = ttk.Frame(lf)
        bf.grid(row=2, column=0, sticky="ew")
        for k, (txt, cmd) in enumerate([("영상 추가…", self.add_files), ("폴더 추가…", self.add_folder),
                                        ("선택 제거", self.remove_sel), ("모두 제거", self.clear_all)]):
            ttk.Button(bf, text=txt, command=cmd).grid(row=0, column=k, padx=(0, 4))
        bf2 = ttk.Frame(lf)
        bf2.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(bf2, text="전사본 지정…", command=self.pick_transcript).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(bf2, text="클로바노트용 음성 추출(.m4a)", command=self.run_audio).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(bf2, text="클로바노트 열기", command=lambda: _open("https://clovanote.naver.com")).grid(row=0, column=2)
        self._hint(lf, "클로바노트 순서: ① 영상을 고르고 [클로바노트용 음성 추출] → ② 생긴 .m4a를 클로바노트에 올리기 → "
                       "③ 변환이 끝나면 타임스탬프 포함 .txt(또는 .docx)로 내려받아 영상 옆에 두기(자동 인식) "
                       "또는 [전사본 지정…]으로 고르기", wrap=560, row=4, column=0, sticky="w", pady=(6, 0))

        # 설정
        outer = ttk.LabelFrame(f, text=" 2. 설정 ", padding=(8, 4))
        outer.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        scr = ScrollFrame(outer)
        scr.pack(fill="both", expand=True)
        sf = scr.inner
        sf.columnconfigure(0, weight=1)
        row = 0

        def sep():
            nonlocal row
            ttk.Separator(sf).grid(row=row, column=0, sticky="ew", pady=6)
            row += 1

        def label(text):
            nonlocal row
            ttk.Label(sf, text=text, font=self.bold).grid(row=row, column=0, sticky="w")
            row += 1

        label("로컬 AI (노트 작성 · 중요 장면 고르기)")
        stf = ttk.Frame(sf)
        stf.grid(row=row, column=0, sticky="ew")
        self.lbl_ai = ttk.Label(stf, text="확인 중…", style="Hint.TLabel")
        self.lbl_ai.pack(side="left")
        self.btn_ai = ttk.Button(stf, text="PC 분석·로컬 AI 설치", command=self.open_pc_dialog)
        self.btn_ai.pack(side="right")
        row += 1
        self.v_ai = tk.BooleanVar(value=self.cfg["use_ai"])
        ttk.Checkbutton(sf, text="로컬 AI로 노트를 쓰고 중요한 장면을 고르기 (권장)", variable=self.v_ai).grid(
            row=row, column=0, sticky="w", pady=(4, 0))
        row += 1
        mf = ttk.Frame(sf)
        mf.grid(row=row, column=0, sticky="w", pady=(2, 0))
        ttk.Label(mf, text="모델").pack(side="left")
        self.v_aimodel = tk.StringVar(value=self.cfg["ai_model"])
        cb = ttk.Combobox(mf, textvariable=self.v_aimodel, values=list(MODEL_CHOICES), width=16)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: (self._update_model_hint(), self.refresh_ai_status()))
        cb.bind("<FocusOut>", lambda e: self.refresh_ai_status())
        row += 1
        self.lbl_model_hint = self._hint(sf, "", row=row, column=0, sticky="w")
        self._update_model_hint()
        row += 1
        cf = ttk.Frame(sf)
        cf.grid(row=row, column=0, sticky="w", pady=(4, 0))
        ttk.Label(cf, text="캡처 최대").pack(side="left")
        self.v_maxi = tk.StringVar(value=str(self.cfg["max_images"]))
        ttk.Spinbox(cf, from_=1, to=40, textvariable=self.v_maxi, width=5).pack(side="left", padx=6)
        ttk.Label(cf, text="장").pack(side="left")
        row += 1
        self.v_vision = tk.BooleanVar(value=self.cfg["vision"])
        ttk.Checkbutton(sf, text="후보 화면을 AI가 직접 보고 확인 (정확, 몇 분 더 걸림)", variable=self.v_vision).grid(
            row=row, column=0, sticky="w")
        row += 1
        self.v_fixtr = tk.BooleanVar(value=self.cfg["fix_tr"])
        ttk.Checkbutton(sf, text="전사본 검토·교정 (인식 오류·용어·오탈자)", variable=self.v_fixtr).grid(
            row=row, column=0, sticky="w")
        row += 1
        self.v_review = tk.BooleanVar(value=self.cfg["review"])
        ttk.Checkbutton(sf, text="노트 검토 (원문과 대조해 오류·앞뒤 모순 수정)", variable=self.v_review).grid(
            row=row, column=0, sticky="w")
        row += 1
        self._hint(sf, "고친 곳은 결과 폴더의 '검토_기록.md'에 원래 글과 함께 남습니다.", row=row, column=0, sticky="w")
        row += 1

        sep()
        label("바로 만들 문서")
        df = ttk.Frame(sf)
        df.grid(row=row, column=0, sticky="w")
        self.v_word = tk.BooleanVar(value=self.cfg["word"])
        self.v_pdf = tk.BooleanVar(value=self.cfg["pdf"])
        ttk.Checkbutton(df, text="Word(.docx)", variable=self.v_word).pack(side="left")
        ttk.Checkbutton(df, text="PDF", variable=self.v_pdf).pack(side="left", padx=12)
        row += 1
        self._hint(sf, "노트(.md)는 항상 만들어집니다. Word·PDF는 결과 탭에서 나중에 만들 수도 있습니다.",
                   row=row, column=0, sticky="w")
        row += 1

        sep()
        label("음성")
        self.v_speech = tk.StringVar(value=self.cfg["speech"])
        ttk.Radiobutton(sf, text="클로바노트 전사본 사용 (없는 영상은 내 PC에서 인식)", value="external",
                        variable=self.v_speech, command=self._refresh_videos).grid(row=row, column=0, sticky="w")
        row += 1
        ttk.Radiobutton(sf, text="모두 내 PC에서 음성 인식 (Whisper, GPU)", value="auto",
                        variable=self.v_speech, command=self._refresh_videos).grid(row=row, column=0, sticky="w")
        row += 1
        pf = ttk.Frame(sf)
        pf.grid(row=row, column=0, sticky="w", pady=(4, 0))
        ttk.Label(pf, text="음성 전처리").pack(side="left")
        self.preset_names = list(PRESET_DESC.items())
        self.v_preset = tk.StringVar(value=PRESET_DESC.get(self.cfg["preset"], PRESET_DESC["light"]))
        ttk.Combobox(pf, textvariable=self.v_preset, values=[d for _, d in self.preset_names],
                     state="readonly", width=34).pack(side="left", padx=6)
        row += 1
        self.v_redo = tk.BooleanVar(value=self.cfg["redo"])
        ttk.Checkbutton(sf, text="음성 인식 다시 하기 (이전 결과가 있어도 새로 인식)", variable=self.v_redo).grid(
            row=row, column=0, sticky="w", pady=(4, 0))
        row += 1

        sep()
        self.v_noslides = tk.BooleanVar(value=self.cfg["noslides"])
        ttk.Checkbutton(sf, text="슬라이드 없는 영상 (강의실을 카메라로 촬영) — 5분 단위로 정리",
                        variable=self.v_noslides).grid(row=row, column=0, sticky="w")
        row += 1
        of = ttk.Frame(sf)
        of.grid(row=row, column=0, sticky="ew", pady=(6, 0))
        of.columnconfigure(2, weight=1)
        ttk.Label(of, text="저장 위치", font=self.bold).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.v_outmode = tk.StringVar(value=self.cfg["outmode"])
        self.v_outdir = tk.StringVar(value=self.cfg["outdir"])
        ttk.Radiobutton(of, text="영상과 같은 폴더", value="beside", variable=self.v_outmode).grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(of, text="지정 폴더:", value="custom", variable=self.v_outmode).grid(row=1, column=1, sticky="w")
        ttk.Entry(of, textvariable=self.v_outdir).grid(row=1, column=2, sticky="ew", padx=4)
        ttk.Button(of, text="찾기", width=6, command=self.pick_outdir).grid(row=1, column=3)
        row += 1

        self.v_model = tk.StringVar(value=self.cfg["model"])
        self.v_sens = tk.StringVar(value=self.cfg["sens"])
        self.v_crop = tk.StringVar(value=self.cfg["crop"])
        af = ttk.Frame(sf)
        af.grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Button(af, text="고급 설정…", command=self.open_adv).pack(side="left")
        ttk.Button(af, text="전문용어 편집", command=self.edit_terms).pack(side="left", padx=6)
        row += 1
        self._hint(sf, "고급 설정: 음성 인식 모델, 화면 전환 민감도, 웹캠 영역 제외", row=row, column=0, sticky="w")

        # 실행 버튼
        rf = ttk.Frame(f)
        rf.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.btn_start = ttk.Button(rf, text="▶  분석 시작", style="Big.TButton", command=self.start_analysis)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(rf, text="■  중지", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Label(rf, text="분석이 끝나면 [② 결과 보기] 탭으로 이동합니다.", style="Hint.TLabel").pack(side="left", padx=8)

    def _build_results(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="분석이 끝난 영상", font=self.bold).pack(side="left")
        ttk.Button(top, text="예전 결과 불러오기…", command=self.load_result).pack(side="right")
        cols = ("video", "note", "images", "review", "docs", "time")
        tv = ttk.Treeview(f, columns=cols, show="headings", height=7, selectmode="browse")
        for c, txt, w in zip(cols, ("영상", "노트", "캡처", "검토 수정", "Word·PDF", "걸린 시간"),
                             (340, 150, 55, 130, 100, 75)):
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w" if c == "video" else "center")
        tv.grid(row=1, column=0, sticky="nsew", pady=6)
        tv.bind("<Double-1>", lambda e: self.res_action("note"))
        self.tv = tv

        nf = ttk.LabelFrame(f, text=" 노트 ", padding=8)
        nf.grid(row=2, column=0, sticky="ew")
        for k, (txt, act) in enumerate([("노트 열기 (Word)", "note"), ("PDF 열기", "pdf"), ("노트 md 열기", "md"),
                                        ("검토 기록 보기", "log"), ("Word·PDF 다시 만들기", "export"),
                                        ("결과 폴더 열기", "folder"), ("복습 페이지", "review")]):
            ttk.Button(nf, text=txt, command=lambda a=act: self.res_action(a)).grid(row=k // 4, column=k % 4,
                                                                                 padx=(0, 6), pady=2, sticky="ew")

        cf = ttk.LabelFrame(f, text=" 노트가 마음에 들지 않으면 — Claude에 맡기기 ", padding=8)
        cf.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        bf = ttk.Frame(cf)
        bf.pack(anchor="w")
        ttk.Button(bf, text="Claude용 자료 복사", command=lambda: self.res_action("copy")).pack(side="left")
        ttk.Button(bf, text="캡처 폴더 열기 (attach)", command=lambda: self.res_action("attach")).pack(side="left", padx=6)
        ttk.Button(bf, text="md 파일 검토·교정…", command=self.review_any_md).pack(side="left", padx=6)
        ttk.Button(bf, text="md 파일을 Word·PDF로 변환…", command=self.convert_any_md).pack(side="left")
        ttk.Label(cf, justify="left", style="Hint.TLabel", text=(
            "1) [Claude용 자료 복사] → Claude 채팅창에 붙여넣기(Ctrl+V)\n"
            "2) [캡처 폴더 열기] → 이미지를 모두 채팅창으로 끌어다 놓기 → 전송 (정리 요청문이 자료 맨 위에 들어 있습니다)\n"
            "3) Claude가 준 정리본을 .md 파일로 저장했다면 [md 파일 검토·교정…]으로 로컬 AI가 오탈자·모순을 검토하고,\n"
            "   [md 파일을 Word·PDF로 변환…]으로 문서로 만들 수 있습니다.")
        ).pack(anchor="w", pady=(6, 0))

    def _build_tools(self, f):
        f.columnconfigure(0, weight=1)
        a = ttk.LabelFrame(f, text=" 음성 전처리 비교 (내 PC에서 음성 인식할 때) ", padding=10)
        a.grid(row=0, column=0, sticky="ew")
        a.columnconfigure(1, weight=1)
        self.v_tvideo = tk.StringVar()
        ttk.Label(a, text="영상").grid(row=0, column=0, sticky="w")
        ttk.Entry(a, textvariable=self.v_tvideo).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(a, text="찾기", width=6, command=lambda: self._pick_into(self.v_tvideo, video=True)).grid(row=0, column=2)
        bf = ttk.Frame(a)
        bf.grid(row=1, column=1, sticky="w", pady=(6, 0), padx=6)
        ttk.Label(bf, text="비교 시작 지점(분)").pack(side="left")
        self.v_tstart = tk.StringVar(value="30")
        ttk.Spinbox(bf, from_=0, to=600, textvariable=self.v_tstart, width=6).pack(side="left", padx=6)
        ttk.Button(bf, text="전처리 3종 비교 (5분)", command=self.run_presets).pack(side="left", padx=6)
        self._hint(a, "울림·잡음이 심한 구간을 골라 '없음/가벼운 보정/잡음 제거' 인식 결과를 나란히 보여 줍니다.",
                   row=2, column=1, sticky="w", padx=6, pady=(6, 0))

        c = ttk.LabelFrame(f, text=" 음성 인식 정확도 비교 (내 PC 인식 vs 클로바노트 등) ", padding=10)
        c.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        c.columnconfigure(1, weight=1)
        self.v_ta = tk.StringVar()
        self.v_tb = tk.StringVar()
        self.v_tref = tk.StringVar()
        self.v_cs = tk.StringVar(value="30:00")
        self.v_ce = tk.StringVar(value="40:00")
        rows = [("전사본 A", self.v_ta, "예: 결과 폴더의 transcript.srt (정답 초안은 이 파일로 만듭니다)"),
                ("전사본 B", self.v_tb, "예: 클로바노트에서 받은 .txt/.docx (선택)")]
        for k, (lab, var, hint) in enumerate(rows):
            ttk.Label(c, text=lab).grid(row=k * 2, column=0, sticky="w")
            ttk.Entry(c, textvariable=var).grid(row=k * 2, column=1, sticky="ew", padx=6)
            ttk.Button(c, text="찾기", width=6, command=lambda v=var: self._pick_into(v)).grid(row=k * 2, column=2)
            self._hint(c, hint, row=k * 2 + 1, column=1, sticky="w", padx=6)
        tf = ttk.Frame(c)
        tf.grid(row=4, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(tf, text="구간").pack(side="left")
        ttk.Entry(tf, textvariable=self.v_cs, width=9).pack(side="left", padx=4)
        ttk.Label(tf, text="~").pack(side="left")
        ttk.Entry(tf, textvariable=self.v_ce, width=9).pack(side="left", padx=4)
        ttk.Label(tf, text="(분:초, 5~10분 권장)", style="Hint.TLabel").pack(side="left", padx=4)
        ttk.Button(c, text="① 정답 초안 만들기", command=self.run_draft).grid(row=5, column=1, sticky="w", padx=6, pady=(4, 0))
        self._hint(c, "초안이 메모장으로 열리면, 영상을 들으며 틀린 곳을 고쳐 저장하세요. (줄 앞 [시각]은 그대로 둬도 됩니다)",
                   row=6, column=1, sticky="w", padx=6)
        ttk.Label(c, text="정답 파일").grid(row=7, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(c, textvariable=self.v_tref).grid(row=7, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(c, text="찾기", width=6, command=lambda: self._pick_into(self.v_tref)).grid(row=7, column=2, pady=(6, 0))
        ttk.Button(c, text="② 채점", command=self.run_score).grid(row=8, column=1, sticky="w", padx=6, pady=(6, 0))
        self._hint(c, "CER(문자 오류율)이 낮을수록 정확합니다. 정답의 전문용어는 슬라이드 표기(예: MOSFET)로 통일하세요.",
                   row=9, column=1, sticky="w", padx=6)

    def _build_help(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        bf = ttk.Frame(f)
        bf.grid(row=0, column=0, sticky="w")
        ttk.Button(bf, text="설치 점검", command=self.run_check).pack(side="left")
        ttk.Button(bf, text="PC 분석·로컬 AI 설치", command=self.open_pc_dialog).pack(side="left", padx=6)
        ttk.Button(bf, text="전문용어 편집 (terms.txt)", command=self.edit_terms).pack(side="left")
        ttk.Button(bf, text="전체 설명서 (README)", command=lambda: _open(TOOL_DIR / "README.md")).pack(side="left", padx=6)
        txt = tk.Text(f, wrap="word", relief="flat", padx=10, pady=10)
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")
        txt.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

    def _build_bottom(self, r):
        b = ttk.Frame(r, padding=(8, 0, 8, 8))
        b.pack(side="bottom", fill="x")
        top = ttk.Frame(b)
        top.pack(fill="x")
        self.v_status = tk.StringVar(value="대기 중")
        ttk.Label(top, textvariable=self.v_status).pack(side="left")
        self.pbar = ttk.Progressbar(b, maximum=1000)
        self.pbar.pack(fill="x", pady=4)
        lf = ttk.Frame(b)
        lf.pack(fill="both")
        self.log = tk.Text(lf, height=7, wrap="word", font=("Consolas", 9) if os.name == "nt" else None)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set, state="disabled")

    # ───────── 로컬 AI 상태 ─────────
    def _ai_model(self):
        return self.v_aimodel.get().strip() or DEFAULT_MODEL

    def _update_model_hint(self):
        self.lbl_model_hint.configure(text="     " + MODEL_CHOICES.get(self._ai_model(), "직접 입력한 모델 (Ollama 라이브러리 이름)"))

    def refresh_ai_status(self):
        """로컬 AI 상태를 별도 스레드에서 확인 (서버를 켜는 데 몇 초 걸릴 수 있음)"""
        model = self._ai_model()
        self.lbl_ai.configure(text="로컬 AI 확인 중…", style="Hint.TLabel")

        def work():
            try:
                from .ai import status

                code, msg = status(model)
            except Exception as ex:
                code, msg = "error", f"확인 실패: {ex}"
            self.runner.q.put(("ai", (code, msg)))
        threading.Thread(target=work, daemon=True).start()

    def _show_ai_status(self, code, msg):
        ok = code == "ready"
        self.lbl_ai.configure(text=("● " if ok else "▲ ") + msg, style="Ok.TLabel" if ok else "Warn.TLabel")
        self.ai_ready = ok
        if not ok and not getattr(self, "_asked_setup", False) and code in ("not_installed", "no_model"):
            self._asked_setup = True  # 처음 한 번만 안내
            if messagebox.askyesno(APP_TITLE, f"{msg}.\n\nPC 사양을 분석해 알맞은 로컬 AI를 설치할까요?"):
                self.open_pc_dialog()

    def run_ai_setup(self, model=None):
        model = model or self._ai_model()
        try:
            from .ai import has_model, server_up

            if server_up() and has_model(model):
                messagebox.showinfo(APP_TITLE, f"모델 {model}은(는) 이미 설치되어 있습니다.")
                self.refresh_ai_status()
                return
        except Exception:
            pass
        from .sysinfo import model_size

        size = model_size(model)
        if not messagebox.askyesno(APP_TITLE, f"로컬 AI(Ollama)와 모델 {model}"
                                              f"{f'(약 {size:g}GB)' if size else ''}을(를) 설치합니다.\n"
                                              f"처음에는 Ollama(약 1GB)도 함께 내려받아 10~30분 걸릴 수 있습니다. 진행할까요?"):
            return
        self._queue_jobs([("로컬 AI 설치", ["ai-setup", "--model", model], lambda code: self.refresh_ai_status())])

    def open_pc_dialog(self):
        """PC 사양 분석 → 모델별 적합도 표 → 추천 모델 설치·선택"""
        if getattr(self, "_pc_win", None) and self._pc_win.winfo_exists():
            self._pc_win.lift()
            return
        from .sysinfo import MODELS, analyze_pc, fit, recommend

        w = tk.Toplevel(self.root)
        self._pc_win = w
        w.title("PC 분석 · 로컬 AI 설치")
        w.transient(self.root)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)
        ttk.Label(f, text="이 PC의 사양", font=self.bold).grid(row=0, column=0, sticky="w")
        spec = ttk.Label(f, text="분석 중…", justify="left")
        spec.grid(row=1, column=0, sticky="w", pady=(4, 10))
        ttk.Label(f, text="모델별 예상 (더블클릭으로 선택)", font=self.bold).grid(row=2, column=0, sticky="w")
        tv = ttk.Treeview(f, columns=("m", "size", "fit", "desc"), show="headings", height=len(MODELS), selectmode="browse")
        for c, t, wd in (("m", "모델", 170), ("size", "크기", 60), ("fit", "이 PC에서", 470), ("desc", "용도", 400)):
            tv.heading(c, text=t)
            tv.column(c, width=wd, anchor="center" if c == "size" else "w")
        tv.grid(row=3, column=0, sticky="nsew", pady=4)
        rec = ttk.Label(f, text="", style="Ok.TLabel", wraplength=860, justify="left")
        rec.grid(row=4, column=0, sticky="w", pady=(6, 0))
        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        state = {"rec": None}

        def selected():
            sel = tv.selection()
            return sel[0] if sel else state["rec"]

        def install():
            m = selected()
            if m:
                self.v_aimodel.set(m)
                self._update_model_hint()
                self._save_settings()
                w.destroy()
                self.run_ai_setup(m)

        def use_only():
            m = selected()
            if m:
                self.v_aimodel.set(m)
                self._update_model_hint()
                self._save_settings()
                self.refresh_ai_status()
                w.destroy()

        ttk.Button(bf, text="선택한 모델 설치", style="Big.TButton", command=install).pack(side="left")
        ttk.Button(bf, text="설치 없이 이 모델 사용", command=use_only).pack(side="left", padx=8)
        ttk.Button(bf, text="닫기", command=w.destroy).pack(side="right")
        tv.bind("<Double-1>", lambda e: install())

        def work():
            try:
                pc = analyze_pc()
                self.runner.q.put(("pc", (pc, recommend(pc))))
            except Exception as ex:
                self.runner.q.put(("pc", (None, str(ex))))

        def show(pc, r):
            if not w.winfo_exists():
                return
            if pc is None:
                spec.configure(text=f"분석 실패: {r}")
                return
            gl = "\n".join(f"GPU: {g['name']} ({g['vram_gb']}GB)" for g in pc["gpus"]) or "GPU: 찾지 못함"
            spec.configure(text=f"운영체제: {pc['os']}\nCPU: {pc['cpu']} ({pc['cores']}스레드)\n"
                                f"시스템 메모리: {pc['ram_gb']}GB\n{gl}\n"
                                f"모델 저장 위치 남은 공간: {pc['disk_free_gb']}GB")
            mark = {"gpu": "✔ ", "partial": "△ ", "cpu": "△ ", "no": "✖ "}
            for m, size, desc in MODELS:
                code, txt = fit(size, pc)
                tv.insert("", "end", iid=m, values=(m + ("  ★추천" if m == r[0] else ""), f"{size:g}GB",
                                                    mark[code] + txt, desc))
            state["rec"] = r[0]
            tv.selection_set(r[0])
            rec.configure(text=f"추천: {r[0]} — {r[1]}")

        self._pc_show = show
        threading.Thread(target=work, daemon=True).start()

    # ───────── 영상 목록 ─────────
    def _tr_label(self, v):
        if self.v_speech.get() == "auto":
            return "— (내 PC에서 음성 인식)"
        if v.get("transcript"):
            return "지정: " + Path(v["transcript"]).name
        p = find_external_transcript(Path(v["path"]))
        return ("자동: " + p.name) if p else "없음 → 내 PC에서 인식"

    def _refresh_videos(self):
        self.vt.delete(*self.vt.get_children())
        for k, v in enumerate(self.videos):
            self.vt.insert("", "end", iid=str(k), values=(Path(v["path"]).name, self._tr_label(v)))
        self.lbl_count.configure(text=f"선택된 영상: {len(self.videos)}개")

    def _add(self, paths):
        known = {v["path"] for v in self.videos}
        n0 = len(self.videos)
        for p in paths:
            p = str(Path(p))
            if p not in known:
                self.videos.append({"path": p, "transcript": None})
                known.add(p)
        self._refresh_videos()
        return len(self.videos) - n0

    def _selected_videos(self, all_if_none=False):
        idx = [int(i) for i in self.vt.selection()]
        if not idx and all_if_none:
            idx = list(range(len(self.videos)))
        return idx

    def add_files(self):
        exts = " ".join(f"*{e}" for e in VIDEO_EXTS)
        fs = filedialog.askopenfilenames(title="분석할 영상 선택 (여러 개 가능)", initialdir=self.cfg.get("lastdir") or None,
                                         filetypes=[("영상 파일", exts), ("모든 파일", "*.*")])
        if fs:
            self.cfg["lastdir"] = str(Path(fs[0]).parent)
            self._add(fs)

    def add_folder(self):
        d = filedialog.askdirectory(title="영상이 들어 있는 폴더 선택", initialdir=self.cfg.get("lastdir") or None)
        if not d:
            return
        self.cfg["lastdir"] = d
        fs = sorted(p for p in Path(d).iterdir() if p.suffix.lower() in VIDEO_EXTS)
        if not fs:
            messagebox.showinfo(APP_TITLE, "이 폴더에는 영상 파일이 없습니다.")
            return
        added = self._add(fs)
        self._log(f"폴더에서 영상 {added}개를 추가했습니다: {d}")

    def remove_sel(self):
        for i in sorted(self._selected_videos(), reverse=True):
            del self.videos[i]
        self._refresh_videos()

    def clear_all(self):
        self.videos.clear()
        self._refresh_videos()

    def pick_transcript(self):
        idx = self._selected_videos()
        if len(idx) != 1:
            messagebox.showinfo(APP_TITLE, "목록에서 영상을 하나 고른 뒤 [전사본 지정…]을 누르세요.")
            return
        v = self.videos[idx[0]]
        ft = " ".join(f"*{e}" for e in TRANSCRIPT_EXTS)
        p = filedialog.askopenfilename(title=f"'{Path(v['path']).name}'의 클로바노트 전사본 선택",
                                       initialdir=str(Path(v["path"]).parent),
                                       filetypes=[("전사본 (클로바노트 .txt/.docx, 자막 .srt/.vtt)", ft), ("모든 파일", "*.*")])
        if p:
            v["transcript"] = p
            if self.v_speech.get() == "auto":
                self.v_speech.set("external")
            self._refresh_videos()

    def pick_outdir(self):
        d = filedialog.askdirectory(title="결과를 저장할 폴더")
        if d:
            self.v_outdir.set(d)
            self.v_outmode.set("custom")

    def open_adv(self):
        """고급 설정 창 (값은 메인 창 변수에 바로 반영)"""
        if getattr(self, "_adv_win", None) and self._adv_win.winfo_exists():
            self._adv_win.lift()
            return
        w = tk.Toplevel(self.root)
        self._adv_win = w
        w.title("고급 설정")
        w.transient(self.root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="음성 인식 모델", font=self.bold).grid(row=0, column=0, sticky="w")
        ttk.Combobox(f, textvariable=self.v_model, state="readonly", width=18,
                     values=["large-v3", "large-v3-turbo"]).grid(row=0, column=1, sticky="w", padx=8)
        self._hint(f, "내 PC에서 음성 인식할 때만 사용. large-v3: 정확(기본) · large-v3-turbo: 빠르고 메모리 적음",
                   row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(f, text="화면 전환 민감도", font=self.bold).grid(row=2, column=0, sticky="w")
        sf2 = ttk.Frame(f)
        sf2.grid(row=2, column=1, sticky="w", padx=8)
        for k, (val, txt) in enumerate([("low", "낮음"), ("normal", "보통"), ("high", "높음")]):
            ttk.Radiobutton(sf2, text=txt, value=val, variable=self.v_sens).grid(row=0, column=k, padx=(0, 8))
        self._hint(f, "화면 변화를 놓치면 '높음', 같은 화면이 여러 장으로 잡히면 '낮음'",
                   row=3, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(f, text="분석 영역 (웹캠 제외)", font=self.bold).grid(row=4, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_crop, width=20).grid(row=4, column=1, sticky="w", padx=8)
        self._hint(f, "x,y,폭,높이를 0~1 비율로. 예) 0,0,0.75,1 = 왼쪽 75%만 분석 (오른쪽 웹캠 제외). 비우면 화면 전체",
                   row=5, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Button(f, text="닫기", command=w.destroy).grid(row=6, column=1, sticky="e")

    def edit_terms(self):
        p = TOOL_DIR / "terms.txt"
        if not p.exists():
            p.write_text("# 한 줄에 하나씩 전문용어를 적으세요\n", encoding="utf-8")
        _open(p)

    def _preset_key(self):
        d = self.v_preset.get()
        return next((k for k, v in self.preset_names if v == d), "light")

    def _max_images(self):
        try:
            return max(1, min(40, int(self.v_maxi.get())))
        except ValueError:
            return 12

    def _pick_into(self, var, video=False):
        ft = [("영상 파일", " ".join(f"*{e}" for e in VIDEO_EXTS))] if video else \
            [("전사본", " ".join(f"*{e}" for e in TRANSCRIPT_EXTS))]
        p = filedialog.askopenfilename(filetypes=ft + [("모든 파일", "*.*")])
        if p:
            var.set(p)

    # ───────── 실행 ─────────
    def _busy(self, on: bool):
        self.btn_start.configure(state="disabled" if on else "normal")
        self.btn_stop.configure(state="normal" if on else "disabled")

    def _queue_jobs(self, jobs, on_all_done=None):
        if self.runner.running():
            messagebox.showwarning(APP_TITLE, "이미 작업이 진행 중입니다. 끝난 뒤 다시 누르세요.")
            return
        self.jobs = list(jobs)
        self.job_total = len(self.jobs)
        self.job_idx = -1
        self.on_all_done = on_all_done
        self._busy(True)
        self.pbar["value"] = 0
        self._next_job()

    def _next_job(self):
        self.job_idx += 1
        if self.job_idx >= self.job_total:
            self._busy(False)
            self.v_status.set("완료")
            self.pbar["value"] = 1000
            if self.on_all_done:
                self.on_all_done()
            return
        label, args, self.on_job_done = self.jobs[self.job_idx]
        self.v_status.set(f"[{self.job_idx + 1}/{self.job_total}] {label}")
        self._log(f"\n▶ [{self.job_idx + 1}/{self.job_total}] {label}")
        self.runner.start(args)

    def start_analysis(self):
        if not self.videos:
            messagebox.showinfo(APP_TITLE, "먼저 '영상 추가' 또는 '폴더 추가'로 분석할 영상을 고르세요.")
            return
        crop = self.v_crop.get().strip()
        if crop:
            try:
                vals = [float(x) for x in crop.split(",")]
                assert len(vals) == 4 and all(0 <= v <= 1 for v in vals)
            except Exception:
                messagebox.showerror(APP_TITLE, "분석 영역 형식이 잘못되었습니다. 예) 0,0,0.75,1  (비우면 전체)")
                return
        if self.v_outmode.get() == "custom" and not self.v_outdir.get().strip():
            messagebox.showerror(APP_TITLE, "저장할 폴더를 지정하세요.")
            return
        if self.v_ai.get() and not getattr(self, "ai_ready", False):
            if not messagebox.askyesno(APP_TITLE, "로컬 AI가 준비되지 않았습니다.\n"
                                                  "이대로 진행하면 노트·검토 없이 규칙으로 장면만 고릅니다. 진행할까요?\n\n"
                                                  "(아니요 → [PC 분석·로컬 AI 설치]를 먼저 누르세요)"):
                return
        self._refresh_videos()
        self._save_settings()
        opts = ["--preset", self._preset_key(), "--model", self.v_model.get(), "--sensitivity", self.v_sens.get(),
                "--ai-model", self._ai_model(), "--max-images", str(self._max_images())]
        if self.v_speech.get() == "auto":
            opts.append("--no-external")
        if self.v_redo.get():
            opts.append("--redo-stt")
        if self.v_noslides.get():
            opts.append("--no-slides")
        if not self.v_ai.get():
            opts.append("--no-ai")
        if not self.v_vision.get():
            opts.append("--no-vision")
        if not self.v_fixtr.get():
            opts.append("--no-fix-transcript")
        if not self.v_review.get():
            opts.append("--no-review")
        if self.v_word.get():
            opts.append("--word")
        if self.v_pdf.get():
            opts.append("--pdf")
        if crop:
            opts += ["--crop", crop]
        if self.v_outmode.get() == "custom":
            opts += ["--out-root", self.v_outdir.get().strip()]
        jobs = []
        for v in self.videos:
            tr = ["--transcript", v["transcript"]] if v.get("transcript") and self.v_speech.get() == "external" else []
            jobs.append((Path(v["path"]).name, ["run", v["path"], *tr, *opts], None))

        def done():
            ok = sum(1 for r in self.results if r.get("_new"))
            for r in self.results:
                r.pop("_new", None)
            if ok:
                self.nb.select(1)
                messagebox.showinfo(APP_TITLE, f"분석이 끝났습니다 ({ok}/{self.job_total}개 성공).\n"
                                               f"[② 결과 보기] 탭에서 노트를 여세요.")
            else:
                messagebox.showwarning(APP_TITLE, "분석에 실패했습니다. 아래 로그를 확인하세요.")
        self._queue_jobs(jobs, done)

    def stop(self):
        if self.runner.running() and messagebox.askyesno(APP_TITLE, "진행 중인 작업을 중지할까요?"):
            self.jobs = self.jobs[: self.job_idx + 1]
            self.job_total = self.job_idx + 1
            self.on_all_done = None
            self.on_job_done = None
            self.runner.stop()
            self._log("중지했습니다.")

    def run_check(self):
        self._queue_jobs([("설치 점검", ["check"], lambda code: self.refresh_ai_status())])

    def run_audio(self):
        idx = self._selected_videos(all_if_none=True)
        if not idx:
            messagebox.showinfo(APP_TITLE, "먼저 영상을 추가하세요.")
            return
        vids = [self.videos[i]["path"] for i in idx]
        jobs = [(f"클로바노트용 음성 추출: {Path(v).name}", ["audio", v], None) for v in vids]
        self._queue_jobs(jobs, lambda: (_open(Path(vids[0]).parent), messagebox.showinfo(
            APP_TITLE, "영상 옆에 '영상이름_audio.m4a'를 만들었습니다.\n"
                       "클로바노트에 올린 뒤, 타임스탬프 포함 .txt로 내려받아 영상 옆에 두세요.")))

    def _tool_video(self):
        v = self.v_tvideo.get().strip()
        if not v or not Path(v).is_file():
            messagebox.showinfo(APP_TITLE, "영상 파일을 먼저 고르세요.")
            return None
        return v

    def run_presets(self):
        v = self._tool_video()
        if not v:
            return
        try:
            start = float(self.v_tstart.get())
        except ValueError:
            messagebox.showerror(APP_TITLE, "시작 지점은 분 단위 숫자로 입력하세요.")
            return
        out = Path(v).parent / f"{Path(v).stem}_notes" / "compare_audio.html"
        self._queue_jobs([("음성 전처리 비교", ["presets", v, "--start", str(start)],
                           lambda code: code == 0 and _open(out))])

    def run_draft(self):
        a = self.v_ta.get().strip()
        if not a or not Path(a).is_file():
            messagebox.showinfo(APP_TITLE, "'전사본 A'를 먼저 고르세요.")
            return
        out = Path(a).with_name("reference_draft.txt")

        def after(code):
            if code == 0:
                self.v_tref.set(str(out))
                _open(out)
        self._queue_jobs([("정답 초안 만들기", ["cer-draft", a, "--start", self.v_cs.get(), "--end", self.v_ce.get(),
                                         "--out", str(out)], after)])

    def run_score(self):
        ref, a, b = self.v_tref.get().strip(), self.v_ta.get().strip(), self.v_tb.get().strip()
        if not ref or not Path(ref).is_file():
            messagebox.showinfo(APP_TITLE, "정답 파일을 고르세요. (① 정답 초안을 고친 파일)")
            return
        hyps = [h for h in (a, b) if h and Path(h).is_file()]
        if not hyps:
            messagebox.showinfo(APP_TITLE, "비교할 전사본을 하나 이상 고르세요.")
            return
        out = Path(ref).with_name("cer_report.html")
        self._queue_jobs([("정확도 채점", ["cer-score", ref, *hyps, "--start", self.v_cs.get(), "--end", self.v_ce.get(),
                                       "--out", str(out)], lambda code: code == 0 and _open(out))])

    # ───────── 결과 ─────────
    @staticmethod
    def _note_files(r):
        note = Path(r["note"]) if r.get("note") else None
        if note is None:
            return None, None, None
        return note, note.with_suffix(".docx"), note.with_suffix(".pdf")

    def _row_values(self, r):
        note, dx, pf = self._note_files(r)
        docs = " · ".join(n for n, p in (("Word", dx), ("PDF", pf)) if p and p.exists()) or "-"
        note_txt = f"있음 ({r['ai_model']})" if note and note.exists() else "없음 (AI 미사용)"
        mins = r.get("minutes")
        rv = (f"전사본 {r.get('transcript_fixes', 0)} · 노트 {r.get('note_fixes', 0)}"
              if r.get("review_log") else "-")
        return (Path(r["video"]).name, note_txt, r.get("images", 0), rv, docs, f"{mins}분" if mins is not None else "-")

    def _add_result(self, r, new=True):
        r["_new"] = new
        self.results = [x for x in self.results if x["out_dir"] != r["out_dir"]] + [r]
        for iid, out in list(self.row_out.items()):
            if out == r["out_dir"]:
                self.tv.delete(iid)
                del self.row_out[iid]
        iid = self.tv.insert("", "end", values=self._row_values(r))
        self.row_out[iid] = r["out_dir"]
        self.tv.selection_set(iid)

    def _refresh_rows(self):
        for iid, out in self.row_out.items():
            r = next((x for x in self.results if x["out_dir"] == out), None)
            if r:
                self.tv.item(iid, values=self._row_values(r))

    def load_result(self):
        d = filedialog.askdirectory(title="결과 폴더(영상이름_notes) 선택")
        if not d:
            return
        p = Path(d) / "result.json"
        if not p.exists():
            messagebox.showinfo(APP_TITLE, "이 폴더에는 결과(result.json)가 없습니다.")
            return
        r = json.loads(p.read_text(encoding="utf-8"))
        r["out_dir"] = str(Path(d))  # 폴더를 옮겼어도 열리게
        if r.get("note"):
            r["note"] = str(Path(d) / Path(r["note"]).name)
        self._add_result(r, new=False)

    def _selected_result(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "목록에서 영상을 고르세요.")
            return None
        out = self.row_out[sel[0]]
        return next((x for x in self.results if x["out_dir"] == out), None)

    def res_action(self, act):
        r = self._selected_result()
        if not r:
            return
        out = Path(r["out_dir"])
        note, dx, pf = self._note_files(r)
        no_note = "이 결과에는 로컬 AI 노트가 없습니다. 로컬 AI를 설치한 뒤 다시 분석하세요."
        if act in ("note", "pdf", "md", "export") and not (note and note.exists()):
            messagebox.showinfo(APP_TITLE, no_note)
            return
        if act == "note":
            _open(dx if dx.exists() else note)
        elif act == "pdf":
            if pf.exists():
                _open(pf)
            elif messagebox.askyesno(APP_TITLE, "PDF가 아직 없습니다. 지금 만들까요?"):
                self._export(note, r)
        elif act == "md":
            _open(note)
        elif act == "export":
            self._export(note, r)
        elif act == "review":
            _open(out / "review.html")
        elif act == "log":
            p = out / "검토_기록.md"
            if p.exists():
                _open(p)
            else:
                messagebox.showinfo(APP_TITLE, "검토 기록이 없습니다. (검토를 끄고 분석했거나, 고친 곳이 없었습니다)")
        elif act == "folder":
            _open(out)
        elif act == "attach":
            if (out / "attach").exists():
                _open(out / "attach")
            else:
                messagebox.showinfo(APP_TITLE, "캡처한 이미지가 없습니다. 자료만 붙여 넣으면 됩니다.")
        elif act == "copy":
            p = Path(r.get("claude_md") or out / "Claude용_자료.md")
            if not p.exists():
                p = out / "notes_input.md"  # v2 결과
            text = p.read_text(encoding="utf-8")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            messagebox.showinfo(APP_TITLE, f"Claude용 자료를 복사했습니다 ({len(text):,}자).\n"
                                           f"Claude 채팅창에 붙여넣기(Ctrl+V) 한 뒤, [캡처 폴더 열기]의 이미지를 끌어다 놓으세요.")

    def _export(self, md: Path, r=None):
        def after(code):
            self._refresh_rows()
            if code == 0 and self.made_files:
                if messagebox.askyesno(APP_TITLE, "Word·PDF를 만들었습니다. 지금 열까요?"):
                    _open(self.made_files[0])
        self.made_files = []
        self._queue_jobs([("Word·PDF 만들기", ["export", str(md)], after)])

    def review_any_md(self):
        p = filedialog.askopenfilename(title="로컬 AI로 검토·교정할 마크다운(.md) 파일 선택",
                                       filetypes=[("마크다운", "*.md"), ("모든 파일", "*.*")])
        if not p:
            return

        def after(code):
            if code == 0 and self.made_files:
                if messagebox.askyesno(APP_TITLE, "검토가 끝났습니다. 원본은 그대로 두고 '_검토본.md'와 '_검토기록.md'를 "
                                                  "만들었습니다.\n검토 기록을 열까요?"):
                    _open(self.made_files[-1])
        self.made_files = []
        self._queue_jobs([("md 파일 검토·교정", ["review-md", p, "--model", self._ai_model()], after)])

    def convert_any_md(self):
        p = filedialog.askopenfilename(title="Word·PDF로 바꿀 마크다운(.md) 파일 선택",
                                       filetypes=[("마크다운", "*.md"), ("모든 파일", "*.*")])
        if p:
            self._export(Path(p))

    # ───────── 로그·진행 ─────────
    def _log(self, line):
        self.log.configure(state="normal")
        if "%|" in line or line.startswith("\r"):  # 다운로드 진행 표시줄은 마지막 줄만 갱신
            last = self.log.get("end-2l", "end-1c")
            if "%|" in last:
                self.log.delete("end-2l", "end-1c")
        self.log.insert("end", line.strip("\r") + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, val = self.runner.q.get_nowait()
                if kind == "ai":
                    self._show_ai_status(*val)
                elif kind == "pc":
                    if getattr(self, "_pc_show", None):
                        self._pc_show(*val)
                elif kind == "line":
                    if val.startswith("@@P "):
                        _, frac, *label = val.split(" ", 2)
                        overall = (self.job_idx + float(frac)) / max(1, self.job_total)
                        self.pbar["value"] = overall * 1000
                        if label:
                            self.v_status.set(f"[{self.job_idx + 1}/{self.job_total}] {label[0]}")
                    elif val.startswith("@@R "):
                        try:
                            self._add_result(json.loads(val[4:]))
                        except Exception:
                            pass
                    elif val.startswith("@@F "):
                        try:
                            self.made_files = json.loads(val[4:])
                        except Exception:
                            pass
                    else:
                        self._log(val)
                else:  # done
                    if val != 0:
                        self._log(f"(종료 코드 {val})")
                    if self.on_job_done:
                        try:
                            self.on_job_done(val)
                        except Exception as ex:
                            self._log(f"(결과 열기 실패: {ex})")
                    self._next_job()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def on_close(self):
        if self.runner.running():
            if not messagebox.askyesno(APP_TITLE, "작업이 진행 중입니다. 중지하고 종료할까요?"):
                return
            self.runner.stop()
        self._save_settings()
        self.root.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 고해상도 화면에서 글자 흐림 방지
        except Exception:
            pass
    root = tk.Tk()
    try:
        app = App(root)
    except Exception as ex:
        messagebox.showerror(APP_TITLE, f"프로그램을 시작하지 못했습니다:\n{ex}")
        raise
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
