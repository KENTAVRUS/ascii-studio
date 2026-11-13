"""A small, original procedural scene, used for the welcome canvas and demo."""
from __future__ import annotations

import cv2
import numpy as np


def demo_frame(seconds: float, size: tuple[int, int] = (960, 600)) -> np.ndarray:
    width, height = size
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    u = np.linspace(0, 2 * np.pi, 104, endpoint=False)
    v = np.linspace(0, 2 * np.pi, 44, endpoint=False)
    u, v = np.meshgrid(u, v)
    radius = 1.65 + 0.62 * np.cos(v)
    points = np.stack([radius * np.cos(u), radius * np.sin(u), 0.62 * np.sin(v)], axis=-1)
    normals = np.stack([np.cos(v) * np.cos(u), np.cos(v) * np.sin(u), np.sin(v)], axis=-1)
    a, b = 0.95 + np.sin(seconds * 0.6) * 0.3, seconds * 0.55 + 0.25
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    rotation = rx @ ry
    points = points @ rotation.T
    normals = normals @ rotation.T
    light = np.maximum(0, normals @ np.array([-0.35, -0.45, 0.82]))
    light = 0.16 + 0.84 * light
    hue = (u / (2 * np.pi) * 150 + seconds * 15 + 10) % 180
    hsv = np.stack([hue, np.full_like(hue, 135), light * 255], axis=-1).astype(np.uint8)
    colors = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    scale = min(width, height) * 0.29
    depth = 5.7 - points[..., 2]
    xy = np.stack([
        width / 2 + points[..., 0] * scale * 3.9 / depth,
        height / 2 + points[..., 1] * scale * 3.9 / depth,
    ], axis=-1).astype(np.int32)
    # Back-to-front quad painting keeps the torus hole and silhouette clean.
    for index in np.argsort(points[..., 2].ravel()):
        j, i = divmod(int(index), len(u[0]))
        nj, ni = (j + 1) % len(v), (i + 1) % len(u[0])
        quad = np.array([xy[j, i], xy[j, ni], xy[nj, ni], xy[nj, i]])
        color = tuple(int(c) for c in colors[j, i])
        cv2.fillConvexPoly(canvas, quad, color, lineType=cv2.LINE_AA)
    return canvas
