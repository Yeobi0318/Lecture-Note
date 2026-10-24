# -*- coding: utf-8 -*-
"""로컬 AI(Ollama) 연결: 설치·실행 확인, 모델 내려받기, 대화 요청.

Ollama는 내 PC에서 돌아가는 무료 오픈소스 AI 실행기입니다(http://127.0.0.1:11434).
추가 파이썬 패키지 없이 표준 라이브러리(urllib)로만 통신합니다.

기본 모델 qwen3.5:4b (약 3.4GB, 글·이미지 입력 가능) — RTX 2060 6GB에 전부 올라가는 크기.
음성 인식(Whisper)·OCR과 GPU를 나눠 쓰지 않도록, 파이프라인에서는 그 둘이 끝난 뒤에 사용합니다.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from .common import TOOL_DIR, log, progress

HOST = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = "qwen3.5:4b"
# GUI 선택지 (직접 입력도 가능) — 크기·설명은 sysinfo.MODELS 한 곳에서 관리
from .sysinfo import MODELS as _MODELS  # noqa: E402

MODEL_CHOICES = {m: f"약 {size:g}GB · {desc}" for m, size, desc in _MODELS}
INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
NUM_CTX = 8192  # 6GB GPU에서 4B 모델 + 문맥이 함께 들어가는 크기


class AIError(RuntimeError):
    pass


# ───────── 설치·실행 ─────────

def ollama_exe() -> str | None:
    p = shutil.which("ollama")
    if p:
        return p
    for c in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe"):
        if c.is_file():
            return str(c)
    return None


def _request(path: str, payload=None, timeout: float = 600, method=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(HOST + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def server_up() -> bool:
    try:
        with _request("/api/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_server(wait: float = 30) -> None:
    """Ollama 서버가 꺼져 있으면 창 없이 켬"""
    if server_up():
        return
    exe = ollama_exe()
    if not exe:
        raise AIError("로컬 AI(Ollama)가 설치되어 있지 않습니다. [로컬 AI 설치] 버튼을 누르세요.")
    kw = {"creationflags": 0x08000000 | 0x00000008} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kw)
    t0 = time.time()
    while time.time() - t0 < wait:
        if server_up():
            return
        time.sleep(0.5)
    raise AIError("로컬 AI(Ollama)를 시작하지 못했습니다. PC를 다시 시작한 뒤 다시 시도하세요.")


def installed_models() -> list[str]:
    try:
        with _request("/api/tags", timeout=5) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def has_model(model: str) -> bool:
    names = installed_models()
    return model in names or (":" not in model and f"{model}:latest" in names)


def status(model: str = DEFAULT_MODEL) -> tuple[str, str]:
    """('ready'|'no_model'|'not_running'|'not_installed', 설명)"""
    if not server_up():
        if ollama_exe():
            try:
                ensure_server(15)
            except AIError:
                return "not_running", "로컬 AI가 설치되어 있지만 실행되지 않습니다"
        else:
            return "not_installed", "로컬 AI(Ollama)가 설치되어 있지 않습니다"
    if not has_model(model):
        return "no_model", f"모델 {model}을(를) 아직 받지 않았습니다"
    return "ready", f"준비됨 ({model})"


def install_ollama() -> None:
    """ollama.com의 공식 설치 파일을 받아 사용자 계정에 조용히 설치 (관리자 권한 불필요)"""
    if ollama_exe():
        log("로컬 AI(Ollama)는 이미 설치되어 있습니다.")
        return
    dst = Path(os.environ.get("TEMP", str(TOOL_DIR))) / "OllamaSetup.exe"
    log("로컬 AI(Ollama) 설치 파일 내려받는 중… (약 1GB)")
    with urllib.request.urlopen(INSTALLER_URL, timeout=60) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while True:
            buf = r.read(1 << 20)
            if not buf:
                break
            f.write(buf)
            done += len(buf)
            if total and time.time() - last > 1:
                last = time.time()
                progress(0.4 * done / total, f"로컬 AI 설치 파일 {done >> 20}/{total >> 20}MB")
    log("설치 중… (1~3분)")
    progress(0.42, "로컬 AI 설치 중")
    r = subprocess.run([str(dst), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                       stdin=subprocess.DEVNULL, capture_output=True)
    try:
        dst.unlink()
    except OSError:
        pass
    if r.returncode != 0 or not ollama_exe():
        raise AIError(f"로컬 AI 설치에 실패했습니다(코드 {r.returncode}). "
                      f"https://ollama.com/download 에서 직접 설치한 뒤 다시 시도하세요.")
    log("로컬 AI(Ollama) 설치 완료")


def pull_model(model: str, p0: float = 0.45, p1: float = 1.0) -> None:
    """모델 내려받기 (진행률 표시). 이미 있으면 바로 끝남."""
    ensure_server()
    if has_model(model):
        log(f"모델 {model}: 이미 받아 두었습니다.")
        return
    log(f"모델 {model} 내려받는 중… (처음 한 번만, 수 GB)")
    last = 0.0
    try:
        with _request("/api/pull", {"model": model, "stream": True}, timeout=3600) as r:
            for raw in r:
                d = json.loads(raw.decode("utf-8") or "{}")
                if d.get("error"):
                    raise AIError(f"모델을 받지 못했습니다: {d['error']}")
                tot, comp = d.get("total"), d.get("completed")
                if tot and comp and time.time() - last > 1:
                    last = time.time()
                    progress(p0 + (p1 - p0) * comp / tot, f"모델 받는 중 {comp >> 20}/{tot >> 20}MB")
                    log(f"  {d.get('status', '')} {comp >> 20}/{tot >> 20}MB")
    except urllib.error.HTTPError as ex:
        raise AIError(f"모델을 받지 못했습니다: {ex.read().decode('utf-8', 'replace')[:300]}")
    if not has_model(model):
        raise AIError(f"모델 {model}을(를) 받지 못했습니다. 모델 이름을 확인하세요.")
    log(f"모델 {model} 준비 완료")


def setup(model: str = DEFAULT_MODEL) -> None:
    install_ollama()
    ensure_server()
    pull_model(model)
    log("간단한 시험 중…")
    t0 = time.time()
    out = chat(model, "한 문장으로 답하세요: 대한민국의 수도는?", num_predict=40)
    log(f"  AI 응답: {out.strip()[:80]}  ({time.time() - t0:.1f}초)")
    unload(model)
    progress(1.0, "완료")
    log("로컬 AI 준비 완료 ✅")


# ───────── 대화 ─────────

def image_b64(path: Path, max_w: int = 1024) -> str:
    """이미지를 max_w 이하로 줄여 base64 JPEG로 (처리 시간·메모리 절약)"""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def chat(model: str, prompt: str, system: str = "", images=None, schema=None, temperature: float = 0.2,
         num_predict: int = 2048, num_ctx: int = NUM_CTX, timeout: float = 900) -> str:
    """한 번의 질문 → 답 문자열. schema(JSON Schema)를 주면 그 형식의 JSON을 강제."""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    m = {"role": "user", "content": prompt}
    if images:
        m["images"] = [image_b64(Path(p)) for p in images]
    msgs.append(m)
    payload = {"model": model, "messages": msgs, "stream": False, "think": False, "keep_alive": "10m",
               "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict}}
    if schema:
        payload["format"] = schema
    for attempt in range(2):
        try:
            with _request("/api/chat", payload, timeout=timeout) as r:
                return json.load(r).get("message", {}).get("content", "")
        except urllib.error.HTTPError as ex:
            body = ex.read().decode("utf-8", "replace")
            if "think" in body and "think" in payload:  # 생각(thinking) 기능이 없는 모델
                payload.pop("think")
                continue
            raise AIError(f"로컬 AI 오류: {body[:300]}")
        except urllib.error.URLError as ex:
            raise AIError(f"로컬 AI에 연결하지 못했습니다: {ex.reason}")
    raise AIError("로컬 AI 요청 실패")


def chat_json(model: str, prompt: str, schema: dict, system: str = "", images=None, retries: int = 2, **kw) -> dict:
    """JSON 답을 받아 dict로. 형식이 깨지면 다시 요청."""
    last = ""
    for k in range(retries + 1):
        last = chat(model, prompt, system=system, images=images, schema=schema,
                    temperature=0.2 + 0.15 * k, **kw)
        try:
            d = json.loads(last)
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            s, e = last.find("{"), last.rfind("}")
            if 0 <= s < e:
                try:
                    return json.loads(last[s:e + 1])
                except json.JSONDecodeError:
                    pass
    raise AIError(f"로컬 AI 답의 형식이 올바르지 않습니다: {last[:200]}")


def unload(model: str) -> None:
    """GPU 메모리 반환"""
    try:
        with _request("/api/generate", {"model": model, "keep_alive": 0}, timeout=30):
            pass
    except Exception:
        pass


def unload_all() -> None:
    """GPU에 올라가 있는 로컬 AI 모델을 모두 내림 (음성 인식·OCR 전에 GPU 메모리 확보)"""
    if not server_up():
        return
    try:
        with _request("/api/ps", timeout=5) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return
    for n in names:
        unload(n)
