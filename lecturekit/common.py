# -*- coding: utf-8 -*-
"""공통 유틸: 시간 표기, ffmpeg, GPU 경로, 텍스트 정리, 진행률 출력."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".ts", ".flv", ".mpg", ".mpeg")


# ───────── 진행 상황 출력 (GUI는 '@@' 줄을 읽어 진행 막대를 갱신) ─────────

def log(msg: str = ""):
    print(msg, flush=True)


def progress(frac: float, label: str = ""):
    if os.environ.get("LK_GUI"):
        print(f"@@P {max(0.0, min(1.0, frac)):.3f} {label}", flush=True)


# ───────── 시간 표기 ─────────

def hms(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_clock(s: str):
    """'1:02:03', '02:03', '00:02:03,500', '02:03.5' → 초 (실패 시 None)"""
    s = str(s).strip().replace(",", ".")
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


# ───────── ffmpeg ─────────

_FF = None


def ffmpeg() -> str:
    """시스템 ffmpeg가 없으면 imageio-ffmpeg에 포함된 실행 파일 사용"""
    global _FF
    if not _FF:
        _FF = shutil.which("ffmpeg")
        if not _FF:
            import imageio_ffmpeg

            _FF = imageio_ffmpeg.get_ffmpeg_exe()
    return _FF


def _no_window():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW


def run_ff(args: list[str]):
    """ffmpeg 실행. 표준 입력은 차단(키 입력을 가로채 멈추는 문제 방지)."""
    r = subprocess.run([ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **_no_window())
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 오류:\n" + r.stderr[-1500:])


def ff_popen(args: list[str]):
    return subprocess.Popen([ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", *args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, **_no_window())


def video_duration(video: Path) -> float:
    r = subprocess.run([ffmpeg(), "-nostdin", "-hide_banner", "-i", str(video)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **_no_window())
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        raise RuntimeError(f"영상 길이를 읽지 못했습니다: {video}\n" + r.stderr[-600:])
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def grab_frame(video: Path, t: float, out: Path, max_w: int = 1280):
    """t초 지점의 프레임을 JPEG로 저장 (원본이 max_w보다 크면 축소, 작으면 그대로)"""
    run_ff(["-ss", f"{max(0.0, t):.2f}", "-i", str(video), "-frames:v", "1",
            "-vf", f"scale='min({max_w},iw)':-2", "-q:v", "2", str(out)])


# ───────── GPU (Windows에서 CTranslate2가 CUDA·cuDNN DLL을 찾도록) ─────────

_DLL_HANDLES = []


def setup_cuda_dlls():
    if os.name != "nt" or _DLL_HANDLES:
        return
    cands = []
    try:
        import importlib.util

        spec = importlib.util.find_spec("torch")
        if spec and spec.origin:
            cands.append(Path(spec.origin).parent / "lib")
    except Exception:
        pass
    for sp in sys.path:  # nvidia-* 패키지 경로를 나중에 넣어 PATH 맨 앞에 오게 함
        nv = Path(sp) / "nvidia"
        if nv.is_dir():
            cands += [d / "bin" for d in nv.iterdir() if (d / "bin").is_dir()]
    for c in cands:
        if c.is_dir():
            try:
                _DLL_HANDLES.append(os.add_dll_directory(str(c)))
            except Exception:
                pass
            os.environ["PATH"] = str(c) + os.pathsep + os.environ.get("PATH", "")


def gpu_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


# ───────── 텍스트 ─────────

def text_key(line: str) -> str:
    """비교용 키: 공백·문장부호 제거, 소문자"""
    return re.sub(r"[\W_]+", "", line).lower()


def useful_line(line: str) -> bool:
    """글자(한글·영문) 3자 이상, 숫자·기호보다 글자가 많은 줄만 유지 (플레이어 시간 표시 등 제거)"""
    k = text_key(line)
    letters = len(re.findall(r"[가-힣A-Za-z]", k))
    return letters >= 3 and letters / max(1, len(k)) >= 0.5


FILLER_RE = re.compile(r"(?:(?<=\s)|^)(?:어+|음+|으+|아+|에+)[,.…]*(?=\s|$)")
REPEAT_RE = re.compile(r"(?<!\S)([^\s\d]\S+)(?:\s+\1)+(?!\S)")  # 숫자열(1 0 1 1)은 건드리지 않음


def clean_text(t: str) -> str:
    """군말(어, 음…) 제거 + 같은 단어 연속 반복 축약"""
    t = FILLER_RE.sub("", t)
    t = REPEAT_RE.sub(r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def char_ngrams(text: str, n: int = 3) -> set:
    k = text_key(text)
    return {k[i:i + n] for i in range(max(0, len(k) - n + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0
