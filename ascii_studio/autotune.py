"""Conservative, deterministic tone fitting for a whole video, without AI models."""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace
from typing import Callable, Iterable

import cv2
import numpy as np

from .engine import RenderSettings, VideoInfo, fit_size


class AutoTuneCancelled(Exception):
    pass


@dataclass(frozen=True)
class AutoTuneResult:
    settings: RenderSettings
    frames_used: int
    frames_sampled: int


def _check_cancel(cancel: threading.Event | None):
    if cancel is not None and cancel.is_set():
        raise AutoTuneCancelled()


def _remove_bars(gray: np.ndarray) -> np.ndarray:
    """Remove only consecutive nearly black strips touching the frame edges."""
    row_dark = np.quantile(gray, 0.98, axis=1) < 0.025
    col_dark = np.quantile(gray, 0.98, axis=0) < 0.025
    h, w = gray.shape
    top = bottom = left = right = 0
    while top < h // 4 and row_dark[top]:
        top += 1
    while bottom < h // 4 and row_dark[h - 1 - bottom]:
        bottom += 1
    while left < w // 4 and col_dark[left]:
        left += 1
    while right < w // 4 and col_dark[w - 1 - right]:
        right += 1
    return gray[top:h-bottom, left:w-right]


def suggest_settings(
    frames: Iterable[np.ndarray], settings: RenderSettings,
    cancel: threading.Event | None = None,
) -> AutoTuneResult:
    samples, noise_levels, edge_densities, black_fractions = [], [], [], []
    sampled = 0
    for rgb in frames:
        _check_cancel(cancel)
        sampled += 1
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
        gray = _remove_bars(gray)
        q01, q99 = np.quantile(gray, [0.01, 0.99])
        if q99 < 0.025 or q01 > 0.975 or q99 - q01 < 0.025:
            continue  # Blank title cards and fades should not determine the whole clip's tone.
        noise = float(np.median(np.abs(gray - cv2.medianBlur(gray, 3))))
        cols = min(280, max(40, settings.columns))
        rows = max(2, round(gray.shape[0] / gray.shape[1] * cols / 1.85))
        cells = cv2.resize(gray, (cols, rows), interpolation=cv2.INTER_AREA)
        laplacian = np.minimum(np.abs(cv2.Laplacian(cells, cv2.CV_32F)), 0.4)
        black_fractions.append(float(np.mean(cells < 0.015)))
        edge_densities.append(float(np.mean(laplacian > 0.18)))
        noise_levels.append(noise)
        # Ignore fully black backgrounds and already clipped white pixels when fitting.
        active = (cells > 0.015) & (cells < 0.985)
        if np.count_nonzero(active) < 12:
            active = np.ones_like(cells, dtype=bool)
        values, edges = cells[active], laplacian[active]
        indices = np.linspace(0, len(values) - 1, min(2048, len(values)), dtype=int)
        samples.append((values[indices], edges[indices]))
    if not samples:
        raise ValueError("В выбранных кадрах слишком мало деталей для автонастройки. Настройки оставлены прежними.")
    _check_cancel(cancel)
    noise = float(np.median(noise_levels))
    edge_density = float(np.median(edge_densities))
    edge_strength = float(np.clip(0.42 - noise * 5.5 - edge_density * 0.65, 0.04, 0.55))
    edge_strength = round(edge_strength * 100) / 100
    values = np.concatenate([v + e * edge_strength for v, e in samples])
    lo, mid, hi = np.quantile(values, [0.05, 0.50, 0.95])
    hist, boundaries = np.histogram(values, bins=256, range=(0, 1.4))
    weights = hist.astype(np.float32) / max(1, hist.sum())
    centers = (boundaries[:-1] + boundaries[1:]) / 2
    contrasts = np.arange(0.6, 2.201, 0.05, dtype=np.float32)[:, None]
    brightness = np.arange(-0.4, 0.401, 0.02, dtype=np.float32)[None, :]
    low_out = np.clip((lo - 0.5) * contrasts + 0.5 + brightness, 0, 1)
    high_out = np.clip((hi - 0.5) * contrasts + 0.5 + brightness, 0, 1)
    mid_out = np.clip((mid - 0.5) * contrasts + 0.5 + brightness, 0, 1)
    mapped = (centers[None, None, :] - 0.5) * contrasts[..., None] + 0.5 + brightness[..., None]
    clipped = (((mapped < 0.025) | (mapped > 0.975)) * weights).sum(axis=-1)
    loss = (3.0 * (high_out - low_out - 0.78) ** 2 + 0.9 * (mid_out - 0.49) ** 2
            + 2.5 * clipped + 0.025 * (contrasts - 1.2) ** 2 + 0.02 * brightness ** 2)
    if np.median(black_fractions) > 0.15:
        black_out = -0.5 * contrasts + 0.5 + brightness
        loss += np.maximum(black_out - 0.035, 0) * 4
    best = np.unravel_index(np.argmin(loss), loss.shape)
    result = replace(settings, contrast=round(float(contrasts[best[0], 0]), 2),
                     brightness=round(float(brightness[0, best[1]]), 2), edges=edge_strength)
    return AutoTuneResult(result, len(samples), sampled)


def analyze_video(
    info: VideoInfo, settings: RenderSettings, cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> AutoTuneResult:
    _check_cancel(cancel)
    count = min(18, max(6, math.ceil(info.duration / 8)))
    positions = np.linspace(0.04, 0.96, count) * max(0, info.duration - 1 / info.fps)
    cap = cv2.VideoCapture(str(info.path))
    frames = []
    try:
        if not cap.isOpened():
            raise ValueError("Не удалось прочитать видео для автонастройки.")
        size = fit_size(info.width, info.height, 640)
        for i, position in enumerate(positions):
            _check_cancel(cancel)
            cap.set(cv2.CAP_PROP_POS_MSEC, float(position) * 1000)
            success, bgr = cap.read()
            if success:
                small = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
                frames.append(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            if progress:
                progress(i + 1, count)
        _check_cancel(cancel)
        return suggest_settings(frames, settings, cancel)
    finally:
        cap.release()
