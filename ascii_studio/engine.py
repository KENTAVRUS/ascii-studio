from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont


PALETTES = {
    "gold": (219, 184, 108),
    "white": (239, 240, 235),
    "matrix": (79, 239, 139),
    "ice": (118, 200, 241),
}
CHARSETS = {
    "classic": " .:-=+*#%@",
    "detailed": " .'`^\",:;Il!i~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "minimal": " .:+#@",
    "binary": " 01",
}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MAX_VIDEO_SECONDS = 5 * 60
MAX_EXPORT_FPS = 240
MAX_EXPORT_LONG_EDGE = 3840
RESOLUTION_PRESETS = (("Авто · до 4K", 0), ("720p", 1280), ("1080p", 1920),
                      ("1440p · QHD", 2560), ("2160p · 4K", 3840))
FPS_PRESETS = (24, 25, 30, 48, 50, 60, 90, 120, 144, 165, 180, 200, 240)
DURATION_LIMIT_ERROR = "Видео длиннее 5 минут. Максимальная длительность — 05:00. Выберите более короткий ролик."


@dataclass(frozen=True)
class RenderSettings:
    columns: int = 150
    palette: str = "gold"
    custom_color: str = "#dbb86c"
    background: str = "#000000"
    charset: str = "classic"
    contrast: float = 1.2
    brightness: float = 0.04
    edges: float = 0.25
    invert: bool = False


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    frames: int
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class ExportOptions:
    resolution: int = 0  # Long edge of the 16:9 bounding box; auto is capped at UHD.
    fps: float = 0      # Zero keeps the nominal source rate, up to 240 fps.
    audio: bool = True
    crf: int = 18
    upscale: bool = False
    interpolate: bool = False


class ExportCancelled(Exception):
    pass


def ffmpeg_path() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError("Цвет должен быть в формате #RRGGBB.")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def fit_size(width: int, height: int, long_edge: int = 0, short_edge: float = 0) -> tuple[int, int]:
    scale = min(1.0, long_edge / max(width, height)) if long_edge else 1.0
    if short_edge:
        scale = min(scale, short_edge / min(width, height))
    # H.264 yuv420p needs even dimensions. Never upscale the source.
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def export_size(width: int, height: int, resolution: int = 0, upscale: bool = False) -> tuple[int, int]:
    if not 0 <= resolution <= MAX_EXPORT_LONG_EDGE:
        raise ValueError("Выберите разрешение экспорта до 4K (3840 × 2160).")
    long_edge = resolution or MAX_EXPORT_LONG_EDGE
    if upscale and resolution:
        scale = min(long_edge / max(width, height), long_edge * 9 / 16 / min(width, height))
        return max(2, round(width * scale) // 2 * 2), max(2, round(height * scale) // 2 * 2)
    return fit_size(width, height, long_edge, long_edge * 9 / 16)


def export_fps(source_fps: float, requested_fps: float = 0) -> float:
    fps = requested_fps or min(source_fps, MAX_EXPORT_FPS)
    if not math.isfinite(fps) or not 0.1 <= fps <= MAX_EXPORT_FPS:
        raise ValueError("Частота кадров должна быть от 0,1 до 240.")
    return fps


def validate_duration(duration: float):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Не удалось определить длительность видео.")
    if duration > MAX_VIDEO_SECONDS + 1e-6:
        raise ValueError(DURATION_LIMIT_ERROR)


def interpolation_active(info: VideoInfo, options: ExportOptions) -> bool:
    return options.interpolate and export_fps(info.fps, options.fps) > info.fps + 0.01


def decoder_plan(info: VideoInfo, options: ExportOptions) -> tuple[tuple[int, int], str]:
    """Decode for motion/analysis separately from the final high-resolution glyph raster."""
    output_width, output_height = export_size(info.width, info.height, options.resolution, options.upscale)
    width, height = fit_size(info.width, info.height, max(output_width, output_height), min(output_width, output_height))
    fps = export_fps(info.fps, options.fps)
    filters = []
    if interpolation_active(info, options):
        # ASCII uses at most 280 columns. Motion at up to 720p retains ample samples
        # per character without the cost of estimating optical motion at 4K.
        width, height = fit_size(width, height, 1280, 720)
        if min(width, height) < 32:
            raise ValueError("Для интерполяции размер кадра после уменьшения должен быть не меньше 32 × 32.")
        filters += [f"scale={width}:{height}:flags=area", "setsar=1", "setpts=PTS-STARTPTS",
                    "format=yuv420p", "tpad=stop_mode=clone:stop=3",
                    f"minterpolate=fps={fps:.8f}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:me=epzs:vsbmc=1:scd=fdiff:scd_threshold=10",
                    f"trim=duration={info.duration:.9f}"]
    else:
        filters += [f"fps={fps:.8f}", f"scale={width}:{height}:flags=area", "setsar=1"]
    return (width, height), ",".join(filters)


def probe_video(path: str | Path) -> VideoInfo:
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("Видеофайл не найден.")
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("Не удалось открыть видео. Попробуйте MP4, MOV, MKV или WebM.")
        success, frame = cap.read()
        if not success or frame is None:
            raise ValueError("В файле нет доступных видеокадров.")
        height, width = frame.shape[:2]
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        fps_known = math.isfinite(fps) and fps > 0
        if not fps_known:
            fps = 30.0
        frames = max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        sar_n = cap.get(cv2.CAP_PROP_SAR_NUM)
        sar_d = cap.get(cv2.CAP_PROP_SAR_DEN)
        rotation = int(cap.get(cv2.CAP_PROP_ORIENTATION_META)) % 180
        if sar_n > 0 and sar_d > 0:
            if rotation:
                height = round(height * sar_n / sar_d)
            else:
                width = round(width * sar_n / sar_d)
    finally:
        cap.release()
    result = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=NO_WINDOW, timeout=30,
    )
    metadata = result.stderr.decode("utf-8", errors="replace")
    duration = frames / fps if fps_known else 0
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", metadata)
    if duration <= 0 and match:
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    validate_duration(duration)
    if width < 2 or height < 2 or max(width, height) > 16384:
        raise ValueError("Неподдерживаемый размер кадра.")
    return VideoInfo(path, width, height, fps, frames, duration, "Audio:" in metadata)


def _font(size: int):
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "consola.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


@lru_cache(maxsize=96)
def glyph_atlas(charset: str, cell_width: int, cell_height: int) -> np.ndarray:
    font = _font(max(7, round(cell_height * 0.90)))
    masks = []
    for char in CHARSETS[charset]:
        canvas = Image.new("L", (cell_width, cell_height))
        draw = ImageDraw.Draw(canvas)
        box = draw.textbbox((0, 0), char, font=font)
        x = (cell_width - (box[2] - box[0])) / 2 - box[0]
        # A common baseline keeps punctuation and letters aligned.
        anchor_box = draw.textbbox((0, 0), "Mg", font=font)
        y = (cell_height - (anchor_box[3] - anchor_box[1])) / 2 - anchor_box[1]
        draw.text((x, y), char, font=font, fill=255)
        masks.append(np.asarray(canvas))
    # Order glyphs by their actual coverage in the chosen font.
    atlas = np.stack(masks)
    return atlas[np.argsort(atlas.mean(axis=(1, 2)), kind="stable")]


class AsciiRenderer:
    """Rasterize a whole glyph grid with NumPy, without per-cell text drawing."""

    def __init__(self):
        self._previous: np.ndarray | None = None
        self._previous_key = None

    def render(
        self, rgb: np.ndarray, settings: RenderSettings,
        size: tuple[int, int] | None = None, temporal: bool = False,
    ) -> np.ndarray:
        width, height = size or (rgb.shape[1], rgb.shape[0])
        cols = min(300, max(32, int(settings.columns)))
        cell_width = max(4, round(width / cols))
        cell_height = round(cell_width * 1.85)
        rows = max(1, round(height / width * cols * cell_width / cell_height))
        cells = cv2.resize(rgb, (cols, rows), interpolation=cv2.INTER_AREA)
        luma = cv2.cvtColor(cells, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        if settings.edges:
            edges = np.abs(cv2.Laplacian(luma, cv2.CV_32F, ksize=1))
            luma += np.minimum(edges, 0.4) * settings.edges
        luma = np.clip((luma - 0.5) * settings.contrast + 0.5 + settings.brightness, 0, 1)
        if settings.invert:
            luma = 1.0 - luma
        key = (settings, width, height)
        if temporal and self._previous_key == key and self._previous is not None:
            # Stabilize almost unchanged cells without leaving motion trails.
            stable = np.abs(luma - self._previous) < 0.075
            luma = np.where(stable, luma * 0.72 + self._previous * 0.28, luma)
        self._previous = luma.copy() if temporal else None
        self._previous_key = key
        atlas = glyph_atlas(settings.charset, cell_width, cell_height)
        levels = np.rint(luma * (len(atlas) - 1)).astype(np.uint8)
        masks = atlas[levels].transpose(0, 2, 1, 3).reshape(rows * cell_height, cols * cell_width)
        background = np.array(hex_rgb(settings.background), dtype=np.uint16)
        if settings.palette == "original":
            color = cv2.resize(cells, (cols * cell_width, rows * cell_height), interpolation=cv2.INTER_NEAREST)
            alpha = masks.astype(np.uint16)
            inverse = 255 - alpha
            result = np.empty((*masks.shape, 3), dtype=np.uint8)
            # Blend one channel at a time to avoid multiple 100 MB float RGB arrays at 4K.
            for channel in range(3):
                blended = color[..., channel] * alpha + background[channel] * inverse
                result[..., channel] = (blended // 255).astype(np.uint8)
        else:
            rgb_color = PALETTES.get(settings.palette, hex_rgb(settings.custom_color))
            alpha = np.arange(256, dtype=np.uint16)[:, None]
            colors = np.array(rgb_color, dtype=np.uint16)
            lookup = ((colors * alpha + background * (255 - alpha)) // 255).astype(np.uint8)
            result = lookup[masks]
        if result.shape[1] != width or result.shape[0] != height:
            result = cv2.resize(result, (width, height), interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(result)


class ExportJob:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._processes: list[subprocess.Popen] = []

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            for process in self._processes:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass

    def _check_cancel(self):
        if self.cancelled.is_set():
            raise ExportCancelled()

    def _start(self, args: list[str], **kwargs) -> subprocess.Popen:
        with self._lock:
            self._check_cancel()
            process = subprocess.Popen(args, creationflags=NO_WINDOW, **kwargs)
            self._processes.append(process)
            return process

    def run(
        self, info: VideoInfo, destination: str | Path, settings: RenderSettings,
        options: ExportOptions = ExportOptions(),
        progress: Callable[[int, int, float], None] | None = None,
    ) -> Path:
        target = Path(destination).resolve()
        if target == info.path.resolve():
            raise ValueError("Выберите другое имя: исходное видео нельзя перезаписать.")
        if target.suffix.lower() != ".mp4":
            raise ValueError("Экспорт поддерживает формат MP4.")
        if not target.parent.is_dir():
            raise ValueError("Папка для сохранения не найдена.")
        validate_duration(info.duration)
        width, height = export_size(info.width, info.height, options.resolution, options.upscale)
        fps = export_fps(info.fps, options.fps)
        self._check_cancel()
        current = probe_video(info.path)
        if ((current.width, current.height) != (info.width, info.height)
                or abs(current.duration - info.duration) > 0.01 or abs(current.fps - info.fps) > 0.01):
            raise ValueError("Исходное видео изменилось. Откройте его заново перед экспортом.")
        (decode_width, decode_height), video_filter = decoder_plan(info, options)
        fps_arg = f"{fps:.8f}"
        expected = max(1, round(info.duration * fps))
        max_frames = max(1, math.floor(MAX_VIDEO_SECONDS * fps + 1e-6))
        handle, name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".part.mp4", dir=target.parent)
        os.close(handle)
        temporary = Path(name)
        renderer = AsciiRenderer()
        decoder_log = tempfile.TemporaryFile()
        encoder_log = tempfile.TemporaryFile()
        started = time.monotonic()
        try:
            self._check_cancel()
            # The fps filter uses timestamps, including variable-frame-rate inputs.
            decoder = self._start([
                ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-threads", "4", "-i", str(info.path), "-map", "0:v:0", "-an", "-sn", "-dn",
                "-vf", video_filter,
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
            ], stdout=subprocess.PIPE, stderr=decoder_log, stdin=subprocess.DEVNULL)
            args = [
                ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
                "-r", fps_arg, "-i", "pipe:0",
            ]
            with_audio = options.audio and info.has_audio
            if with_audio:
                args += ["-i", str(info.path)]
            args += ["-map", "0:v:0"]
            if with_audio:
                args += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-af", "apad", "-shortest"]
            args += [
                "-c:v", "libx264", "-preset", "fast", "-crf", str(options.crf),
                "-threads", "4", "-rc-lookahead", "12",
                "-pix_fmt", "yuv420p", "-map_metadata", "-1", "-movflags", "+faststart", str(temporary),
            ]
            encoder = self._start(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encoder_log)
            frame_bytes = decode_width * decode_height * 3
            count = 0
            last_report = 0.0
            while True:
                self._check_cancel()
                data = decoder.stdout.read(frame_bytes)
                if not data:
                    break
                if count >= max_frames:
                    raise ValueError(DURATION_LIMIT_ERROR)
                if len(data) != frame_bytes:
                    raise RuntimeError("Не удалось декодировать полный видеокадр.")
                rgb = np.frombuffer(data, np.uint8).reshape(decode_height, decode_width, 3)
                output = renderer.render(rgb, settings, (width, height), temporal=True)
                self._check_cancel()
                encoder.stdin.write(output.tobytes())
                count += 1
                now = time.monotonic()
                if progress and (now - last_report > 0.15 or count == 1):
                    progress(count, expected, now - started)
                    last_report = now
            decoder.stdout.close()
            encoder.stdin.close()
            if decoder.wait(timeout=30) != 0:
                raise RuntimeError("Не удалось прочитать исходное видео.")
            if encoder.wait(timeout=120) != 0:
                raise RuntimeError("Не удалось сохранить видео. Проверьте свободное место и доступ к папке.")
            self._check_cancel()
            if count == 0 or temporary.stat().st_size < 100:
                raise RuntimeError("Видео не содержит кадров для экспорта.")
            # Publish only a complete file; cancellation never destroys an older export.
            os.replace(temporary, target)
            if progress:
                progress(count, count, time.monotonic() - started)
            return target
        except Exception as error:
            if self.cancelled.is_set():
                raise ExportCancelled() from error
            if isinstance(error, (BrokenPipeError, RuntimeError)):
                encoder_log.seek(0)
                decoder_log.seek(0)
                details = (encoder_log.read() + decoder_log.read()).decode("utf-8", errors="replace").strip()
                if details:
                    raise RuntimeError(f"{error}\n\n{details[-1600:]}") from error
            raise
        finally:
            with self._lock:
                for process in self._processes:
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                    for stream in (process.stdin, process.stdout):
                        if stream and not stream.closed:
                            try:
                                stream.close()
                            except OSError:
                                pass
                self._processes.clear()
            decoder_log.close()
            encoder_log.close()
            temporary.unlink(missing_ok=True)
