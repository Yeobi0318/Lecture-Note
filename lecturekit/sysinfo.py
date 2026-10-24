# -*- coding: utf-8 -*-
"""PC 사양 분석과 로컬 AI 모델 추천.

GPU(종류·메모리), 시스템 메모리, CPU, 남은 디스크를 확인해, 이 PC에서 무리 없이 돌아가는
가장 정확한 모델을 고릅니다. 기준: 모델 전체가 GPU 메모리에 들어가면 빠르고, 넘치면 CPU가 나눠 맡아
2~10배 느려집니다. 필요한 GPU 메모리 ≈ 모델 파일 크기 × 1.15 + 1.3GB (문맥 8,192자·이미지 처리분).
실측: RTX 2060 6GB에서 qwen3.5:4b(3.3GB) 적재 시 GPU 사용 약 4.9GB.
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

# (모델, 내려받는 크기 GB, 설명) — 작은 것부터. 크기는 ollama.com 라이브러리 기준(q4_K_M)
MODELS = [
    ("qwen3.5:0.8b", 1.0, "가장 가벼움 · 정리 품질이 낮아 다른 선택지가 없을 때만"),
    ("qwen3.5:2b-q4_K_M", 1.9, "가벼움 · GPU 메모리 4GB 이하나 GPU 없는 PC용"),
    ("qwen3.5:4b", 3.3, "균형 · GPU 메모리 6~8GB용 (RTX 2060·3060 노트북 등)"),
    ("qwen3.5:9b", 6.6, "정확 · GPU 메모리 10GB 이상용 (RTX 3080·4070 등)"),
    ("qwen3.5:27b", 17.0, "가장 정확 · GPU 메모리 20GB 이상용 (RTX 3090·4090 등)"),
]


def need_vram(size_gb: float) -> float:
    return size_gb * 1.15 + 1.3


def _no_window():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def gpu_info() -> list[dict]:
    """[{name, vram_gb, vendor}] — NVIDIA는 nvidia-smi, 그 밖은 Windows 장치 정보"""
    gpus = []
    smi = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    try:
        r = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=15, **_no_window())
        for line in r.stdout.strip().splitlines():
            name, mem = [x.strip() for x in line.rsplit(",", 1)]
            gpus.append({"name": name, "vram_gb": round(float(mem) / 1024, 1), "vendor": "NVIDIA"})
    except Exception:
        pass
    if gpus or os.name != "nt":
        return gpus
    try:  # NVIDIA가 아니면 이름만이라도 (AMD·Intel은 메모리 값이 정확하지 않을 수 있음)
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + '|' + $_.AdapterRAM }"],
                           capture_output=True, text=True, timeout=20, **_no_window())
        for line in r.stdout.strip().splitlines():
            name, _, ram = line.partition("|")
            vendor = "AMD" if re.search(r"AMD|Radeon", name, re.I) else "Intel" if "Intel" in name else "기타"
            try:
                gb = round(int(ram) / 2 ** 30, 1)
            except ValueError:
                gb = 0.0
            gpus.append({"name": name.strip(), "vram_gb": gb, "vendor": vendor})
    except Exception:
        pass
    return gpus


def ram_gb() -> float:
    if os.name == "nt":
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return round(m.ullTotalPhys / 2 ** 30, 1)
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
    except (ValueError, OSError, AttributeError):
        return 0.0


def cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "알 수 없음"


def model_dir() -> Path:
    """Ollama 모델 저장 위치 (OLLAMA_MODELS 환경변수 → 기본 사용자 폴더)"""
    return Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")


def free_disk_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return round(shutil.disk_usage(p).free / 2 ** 30, 1)
    except OSError:
        return 0.0


def os_name() -> str:
    if os.name == "nt":
        build = int(re.sub(r"\D", "", platform.version().split(".")[-1]) or 0)
        return f"Windows {'11' if build >= 22000 else platform.release()} (빌드 {build})"
    return f"{platform.system()} {platform.release()}"


def analyze_pc() -> dict:
    gpus = gpu_info()
    nv = [g for g in gpus if g["vendor"] == "NVIDIA"]
    best = max(nv, key=lambda g: g["vram_gb"], default=None)
    return {
        "os": os_name(),
        "cpu": cpu_name(), "cores": os.cpu_count() or 0, "ram_gb": ram_gb(), "gpus": gpus,
        "vram_gb": best["vram_gb"] if best else 0.0, "gpu": best["name"] if best else None,
        "disk_free_gb": free_disk_gb(model_dir()), "disk_path": str(model_dir()),
    }


def fit(model_size: float, pc: dict) -> tuple[str, str]:
    """('gpu'|'partial'|'cpu'|'no', 설명) — 이 PC에서 그 모델이 어떻게 돌아가는지"""
    need = need_vram(model_size)
    if pc["disk_free_gb"] and pc["disk_free_gb"] < model_size + 2:
        return "no", f"디스크 공간 부족 (남은 공간 {pc['disk_free_gb']}GB)"
    if pc["vram_gb"] >= need:
        return "gpu", "GPU에 전부 들어감 → 빠름"
    if pc["vram_gb"] >= 3 and pc["ram_gb"] >= model_size + 6:
        return "partial", f"GPU 메모리 부족분을 CPU가 나눠 맡음 → 2~4배 느림 (필요 약 {need:.1f}GB)"
    if pc["ram_gb"] >= model_size * 1.3 + 6:
        return "cpu", "GPU 없이 CPU로 실행 → 매우 느림"
    return "no", f"메모리 부족 (시스템 메모리 {pc['ram_gb']}GB)"


def recommend(pc: dict) -> tuple[str, str]:
    """→ (모델, 이유)"""
    gpu_ok = [m for m, s, _ in MODELS if fit(s, pc)[0] == "gpu"]
    if gpu_ok:
        m = gpu_ok[-1]
        if m == "qwen3.5:0.8b" and fit(1.9, pc)[0] in ("partial", "cpu"):
            m = "qwen3.5:2b-q4_K_M"
        return m, f"GPU 메모리 {pc['vram_gb']}GB에 전부 들어가는 가장 정확한 모델입니다."
    if any(fit(s, pc)[0] in ("partial", "cpu") for m, s, _ in MODELS if m == "qwen3.5:2b-q4_K_M"):
        return "qwen3.5:2b-q4_K_M", ("쓸 수 있는 NVIDIA GPU 메모리가 부족해 CPU로 돌려도 감당할 수 있는 가벼운 모델입니다. "
                                     "분석 시간이 길어질 수 있습니다.")
    return "qwen3.5:0.8b", "메모리가 적어 가장 가벼운 모델만 돌릴 수 있습니다. 정리 품질은 낮을 수 있습니다."


def model_size(model: str) -> float | None:
    return next((s for m, s, _ in MODELS if m == model), None)


def report(pc: dict | None = None) -> str:
    pc = pc or analyze_pc()
    lines = [f"운영체제: {pc['os']}", f"CPU: {pc['cpu']} ({pc['cores']}스레드)", f"시스템 메모리: {pc['ram_gb']}GB"]
    if pc["gpus"]:
        for g in pc["gpus"]:
            lines.append(f"GPU: {g['name']} ({g['vram_gb']}GB{'' if g['vendor'] == 'NVIDIA' else ', 로컬 AI 가속 불확실'})")
    else:
        lines.append("GPU: 찾지 못함")
    lines.append(f"모델 저장 위치 남은 공간: {pc['disk_free_gb']}GB ({pc['disk_path']})")
    m, why = recommend(pc)
    lines.append(f"\n추천 모델: {m}\n  → {why}")
    lines.append("\n모델별 예상:")
    for name, size, desc in MODELS:
        lines.append(f"  {name:<20} {size:>4.1f}GB  {fit(size, pc)[1]}")
    return "\n".join(lines)
