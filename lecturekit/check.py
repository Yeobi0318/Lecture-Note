# -*- coding: utf-8 -*-
"""설치·GPU 점검. 종료 코드: 0 정상 / 2 GPU 라이브러리·드라이버 문제 / 3 그 밖의 문제(네트워크 등)."""
from __future__ import annotations

import sys

from .common import TOOL_DIR, ffmpeg, log, run_ff, setup_cuda_dlls


def check() -> int:
    log(f"Python {sys.version.split()[0]}")
    try:
        log(f"ffmpeg: OK ({ffmpeg()})")
    except Exception as ex:
        log(f"ffmpeg 오류: {ex}")
        return 3
    setup_cuda_dlls()
    try:
        import torch

        ok = torch.cuda.is_available()
        log(f"PyTorch {torch.__version__} | GPU 사용 가능: {ok} {torch.cuda.get_device_name(0) if ok else ''}")
    except Exception as ex:
        log(f"PyTorch 오류: {ex}")
    try:
        from .ai import DEFAULT_MODEL, status

        log(f"로컬 AI: {status(DEFAULT_MODEL)[1]}")
    except Exception as ex:
        log(f"로컬 AI 확인 실패: {ex}")
    for mod in ("faster_whisper", "easyocr", "PIL", "numpy", "docx"):
        try:
            __import__(mod)
            log(f"{mod}: OK")
        except Exception as ex:
            log(f"{mod} 오류: {ex}")
            return 3
    try:
        import tkinter  # noqa: F401

        log("tkinter(GUI): OK")
    except Exception:
        log("tkinter(GUI) 없음 — Python을 python.org 설치 파일로 다시 설치하세요")
    log("\n작은 모델(tiny, 약 75MB)로 GPU 음성 인식 시험 중…")
    tmp = TOOL_DIR / "_check.wav"
    try:
        run_ff(["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ac", "1", "-ar", "16000", str(tmp)])
        from faster_whisper import WhisperModel

        try:
            import torch  # noqa: F401
        except Exception:
            pass
        m = WhisperModel("tiny", device="cuda", compute_type="int8_float16")
        list(m.transcribe(str(tmp), language="ko")[0])  # cuDNN·cuBLAS는 실제 인식 시점에 로드됨
        log("GPU 음성 인식: OK ✅")
        return 0
    except Exception as ex:
        msg = str(ex)
        log(f"GPU 음성 인식 실패 ❌: {msg}")
        if any(k in msg.lower() for k in ("cuda", "cudnn", "cublas", ".dll", "gpu", "device")):
            log("→ GPU 라이브러리·드라이버 문제로 보입니다. NVIDIA 드라이버를 업데이트하세요.")
            return 2
        log("→ 네트워크(모델 다운로드) 등 GPU 외의 문제로 보입니다.")
        return 3
    finally:
        tmp.unlink(missing_ok=True)
