# -*- coding: utf-8 -*-
"""슬라이드(화면) 전환 감지와 대표 프레임 저장."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .common import ff_popen, grab_frame, hms, log, progress

W, H = 160, 90  # 비교용 축소 크기


def _ratio(a, b) -> float:
    """두 축소 프레임에서 밝기가 크게(>25) 바뀐 픽셀 비율"""
    return float(np.mean(np.abs(a - b) > 25))


def detect_slides(video: Path, duration: float, interval: float = 2.0, change: float = 0.01,
                  stable: float = 0.004, crop=None, p0: float = 0.0, p1: float = 0.10):
    """
    interval초마다 160x90 흑백 프레임을 비교해 화면 전환을 찾는다.
      - 연속 두 샘플이 거의 같으면(≤ stable) '안정된 화면'으로 본다 (전환 효과 무시)
      - 안정된 화면이 현재 슬라이드와 충분히 다르면(> change) 새 구간
      - 예전에 나온 슬라이드와 같으면 '재등장'으로 처리 (중복 저장 없음)
      - 같은 슬라이드가 유지되는 동안 마지막으로 안정된 시각(t_last)을 갱신
        → 요소가 하나씩 나타나는 슬라이드도 '완성된 마지막 모습'을 대표 프레임으로 사용
    반환: slides [{id, t_first, t_last}], events [(시작시각, slide_id)]
    """
    vf = f"fps=1/{interval}"
    if crop:
        x, y, w, h = crop
        vf += f",crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}"
    vf += f",scale={W}:{H}"
    p = ff_popen(["-i", str(video), "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"])

    slides, events = [], []
    prev, prev_t, cur, cur_img = None, 0.0, None, None
    i = 0
    while True:
        buf = p.stdout.read(W * H)
        if len(buf) < W * H:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W).astype(np.int16)
        t = i * interval
        i += 1
        if prev is not None and _ratio(img, prev) <= stable:  # 안정된 화면
            if cur is None or _ratio(img, cur_img) > change:   # 다른 화면으로 바뀜
                match = next((s for s in slides
                              if min(_ratio(img, s["_first"]), _ratio(img, s["_last"])) <= stable * 2), None)
                if match is None:
                    match = {"id": len(slides) + 1, "t_first": prev_t, "t_last": t,
                             "_first": img, "_last": img}
                    slides.append(match)
                events.append((prev_t, match["id"]))
                cur, cur_img = match, img
            cur["t_last"], cur["_last"] = t, img
        prev, prev_t = img, t
        if i % 150 == 0:
            progress(p0 + (p1 - p0) * min(1.0, t / max(duration, 1)), "화면 전환 감지")
        if i % 300 == 0:
            log(f"  화면 분석 {hms(t)} / {hms(duration)}  (슬라이드 {len(slides)}장)")
    if p.wait() != 0 or i == 0:
        raise RuntimeError("영상 프레임을 읽지 못했습니다(ffmpeg 오류). 파일이 손상되지 않았는지 확인하세요.")
    for s in slides:
        del s["_first"], s["_last"]
    if len(slides) > 400:
        log(f"[주의] 슬라이드가 {len(slides)}장으로 많습니다. 강사 웹캠처럼 움직이는 영역이 있으면 "
            f"고급 설정의 '분석 영역'으로 제외하거나 전환 민감도를 낮추세요.")
    return slides, events


def save_slide_images(video: Path, slides, out_dir: Path, duration: float, max_w: int = 1280,
                      p0: float = 0.10, p1: float = 0.15):
    """각 슬라이드의 완성된 모습(t_last)을 원본 해상도(최대 가로 max_w)로 저장"""
    sd = out_dir / "slides"
    sd.mkdir(parents=True, exist_ok=True)
    for k, s in enumerate(slides, 1):
        path = sd / f"S{s['id']:03d}_{hms(s['t_first']).replace(':', '-')}.jpg"
        grab_frame(video, min(s["t_last"], max(0.0, duration - 0.5)), path, max_w)
        s["image"] = path
        if k % 20 == 0:
            progress(p0 + (p1 - p0) * k / len(slides), "화면 이미지 저장")
