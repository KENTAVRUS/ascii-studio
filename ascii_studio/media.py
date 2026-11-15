"""Image loading and GIF timelines shared by preview, auto tone and export."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from itertools import accumulate
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageOps, UnidentifiedImageError


IMAGE_FORMATS = {"png": "PNG", "jpg": "JPEG", "webp": "WebP", "bmp": "BMP", "tiff": "TIFF"}
VIDEO_FORMATS = {"mp4": "MP4 · H.264", "mov": "MOV · H.264", "mkv": "MKV · H.264",
                 "webm": "WebM · VP9", "avi": "AVI · MPEG-4", "gif": "GIF · анимация"}
GIF_FPS_PRESETS = (10, 12, 15, 20, 24, 25, 30, 50)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")


def format_key(suffix: str) -> str:
    value = suffix.lower().lstrip(".")
    return {"jpeg": "jpg", "tif": "tiff"}.get(value, value)


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    width: int
    height: int
    fps: float
    frames: int
    duration: float
    has_audio: bool
    kind: str = "video"
    frame_durations: tuple[float, ...] = ()

    @property
    def is_image(self) -> bool:
        return self.kind == "image"


def probe_picture(path: Path) -> MediaInfo | None:
    try:
        picture = Image.open(path)
    except UnidentifiedImageError:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            raise ValueError("Изображение повреждено или его формат не поддерживается.") from None
        return None
    except Image.DecompressionBombError:
        raise ValueError("Изображение слишком большое. Поддерживаются фотографии до 64 мегапикселей.") from None
    with picture:
        width, height = picture.size
        if min(width, height) < 2 or max(width, height) > 16384 or width * height > 64_000_000:
            raise ValueError("Поддерживаются изображения до 64 мегапикселей и 16384 пикселей по стороне.")
        animated = getattr(picture, "is_animated", False) and picture.format != "TIFF"
        if animated:
            if picture.format != "GIF":
                raise ValueError("Для анимации используйте GIF или видео. Анимированные WebP и PNG пока не поддерживаются.")
            durations = []
            total = 0
            index = 0
            while True:
                try:
                    picture.seek(index)
                except EOFError:
                    break
                milliseconds = max(0, int(picture.info.get("duration", 100))) or 100
                total += milliseconds
                if total > 300_000:
                    raise ValueError("Анимация длиннее 5 минут. Максимальная длительность — 05:00.")
                durations.append(milliseconds / 1000)
                index += 1
            # Use the shortest delay as the nominal rate so automatic CFR video
            # export does not silently drop short frames in a variable-delay GIF.
            return MediaInfo(path, width, height, 1 / min(durations), len(durations),
                             total / 1000, False, "gif", tuple(durations))
        if picture.getexif().get(274, 1) in (5, 6, 7, 8):
            width, height = height, width
        # Validate pixel data without keeping a full-resolution photograph in memory.
        picture.thumbnail((1000, 1000))
        picture.load()
        return MediaInfo(path, width, height, 0, 1, 0, False, "image")


class PictureReader:
    def __init__(self, info: MediaInfo):
        self.info = info
        self.picture = Image.open(info.path)
        self.ends = tuple(accumulate(info.frame_durations))

    def read(self, position=0.0, background="#000000", size=None) -> np.ndarray:
        index = min(len(self.ends) - 1, bisect_right(self.ends, position + 1e-8)) if self.ends else 0
        self.picture.seek(max(0, index))
        picture = ImageOps.exif_transpose(self.picture) if self.info.is_image else self.picture.copy()
        if size:
            picture.thumbnail(size, Image.Resampling.LANCZOS)
        rgba = picture.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, ImageColor.getrgb(background) + (255,))
        canvas.alpha_composite(rgba)
        return np.asarray(canvas.convert("RGB"))

    def close(self):
        self.picture.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
