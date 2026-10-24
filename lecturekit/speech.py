# -*- coding: utf-8 -*-
"""음성 추출·전처리와 음성 인식(faster-whisper)."""
from __future__ import annotations

import gc
import time
from pathlib import Path

from .common import hms, log, progress, run_ff, setup_cuda_dlls

# 울림·잡음 대응 전처리. 어느 쪽이 나은지는 강의실마다 달라서 '전처리 비교' 기능으로 확인 권장.
AUDIO_PRESETS = {
    "none": None,
    "light": "highpass=f=80,lowpass=f=7800,dynaudnorm=f=250:g=15",
    "denoise": "highpass=f=80,lowpass=f=7800,afftdn=nr=12:nf=-30,dynaudnorm=f=250:g=15",
}
PRESET_DESC = {
    "none": "전처리 없음",
    "light": "가벼운 보정(권장) — 저역 험·고역 잡음 제거 + 음량 평준화",
    "denoise": "잡음 제거 추가 — 에어컨·팬 소음이 클 때. 과하면 오히려 나빠질 수 있음",
}


def extract_audio(video: Path, wav: Path, preset: str = "light", start=None, length=None):
    args = []
    if start is not None:
        args += ["-ss", str(start)]
    args += ["-i", str(video)]
    if length:
        args += ["-t", str(length)]
    args += ["-vn", "-ac", "1", "-ar", "16000"]
    if AUDIO_PRESETS.get(preset):
        args += ["-af", AUDIO_PRESETS[preset]]
    run_ff(args + [str(wav)])


def export_m4a(video: Path, out: Path):
    """클로바노트 등 외부 서비스 업로드용 오디오"""
    run_ff(["-i", str(video), "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "96k", str(out)])


def load_terms(path) -> str:
    """전문용어 목록을 Whisper 힌트(initial_prompt)로 변환"""
    p = Path(path) if path else None
    if not p or not p.is_file():
        return ""
    terms = [l.strip() for l in p.read_text(encoding="utf-8-sig").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    return ("강의 녹음입니다. 주요 용어: " + ", ".join(terms))[:400] if terms else ""


def clean_segments(segs):
    """빈 문장 제거, 같은 문장 3회 이상 연속 반복(환각 루프) 제거"""
    out, rep = [], 0
    for s in segs:
        if not s["text"]:
            continue
        if out and s["text"] == out[-1]["text"]:
            rep += 1
            if rep >= 2:
                continue
        else:
            rep = 0
        out.append(s)
    return out


class Transcriber:
    """GPU(int8_float16) 우선, 실패 시 CPU로 자동 전환"""

    def __init__(self, model_name: str = "large-v3", compute_type: str = "int8_float16", prompt: str = ""):
        self.model_name, self.compute_type, self.prompt = model_name, compute_type, prompt
        setup_cuda_dlls()
        try:
            import torch  # noqa: F401  (Windows: CUDA·cuDNN DLL을 먼저 로드)
        except Exception:
            pass
        self.device = "cuda"
        self._load()

    def _load(self):
        from faster_whisper import WhisperModel

        ctype = self.compute_type if self.device == "cuda" else "int8"
        log(f"  음성 모델 로딩: {self.model_name} ({self.device}) — 처음 실행 시 약 3GB 다운로드")
        self.model = WhisperModel(self.model_name, device=self.device, compute_type=ctype)

    def __call__(self, wav: Path, p0: float = 0.0, p1: float = 1.0):
        try:
            return self._run(wav, p0, p1)
        except Exception as e:
            if self.device != "cuda":
                raise
            log(f"[경고] GPU 음성 인식 실패 → CPU로 다시 시도합니다(매우 느림).\n  원인: {e}\n"
                f"  → '설치 점검'을 실행하거나 NVIDIA 드라이버를 업데이트하세요.")
            del self.model
            gc.collect()
            self.device = "cpu"
            self._load()
            return self._run(wav, p0, p1)

    def _run(self, wav: Path, p0: float, p1: float):
        segs, info = self.model.transcribe(
            str(wav), language="ko", beam_size=5,
            vad_filter=True,  # 무음·잡음 구간 제거 → 없는 말 생성(환각) 감소
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
            condition_on_previous_text=False,  # 같은 문장 무한 반복 방지
            initial_prompt=self.prompt or None,
        )
        out, t0, last = [], time.time(), 0.0
        dur = max(info.duration, 1.0)
        for s in segs:
            out.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip()})
            if time.time() - last > 5:
                last = time.time()
                progress(p0 + (p1 - p0) * min(1.0, s.end / dur), f"음성 인식 {hms(s.end)} / {hms(dur)}")
                log(f"  음성 인식 {hms(s.end)} / {hms(dur)}  ({time.time() - t0:.0f}초 경과)")
        return clean_segments(out)

    def close(self):
        del self.model
        gc.collect()
