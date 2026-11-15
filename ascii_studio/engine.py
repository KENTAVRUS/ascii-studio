from __future__ import annotations

import math
import errno
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Callable

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .media import IMAGE_FORMATS, VIDEO_FORMATS, MediaInfo as VideoInfo, PictureReader, format_key, probe_picture


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
class ExportOptions:
    resolution: int = 0  # Long edge of the 16:9 bounding box; auto is capped at UHD.
    fps: float = 0      # Zero keeps the nominal source rate, up to 240 fps.
    audio: bool = True
    crf: int = 18
    upscale: bool = False
    interpolate: bool = False
    format: str = ""  # Empty infers the format from the destination suffix.


class ExportCancelled(Exception):
    pass


class ExportError(RuntimeError):
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details


def describe_export_error(error: Exception, details: str) -> ExportError:
    diagnostics = details.strip()
    lower = diagnostics.lower()
    no_space = "no space left on device" in lower
    if no_space and "error sending frames to consumers" in lower and "[af#" in lower:
        message = ("Не удалось синхронизировать звук и видео: переполнена внутренняя очередь обработки звука.\n\n"
                   "Повторите экспорт. Если ошибка повторяется, попробуйте отключить «Сохранить исходный звук».")
    elif no_space or (isinstance(error, OSError) and (error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112)):
        message = "Недостаточно свободного места для записи видео. Освободите место или выберите папку на другом диске."
    elif isinstance(error, BrokenPipeError):
        message = "Кодировщик остановился во время экспорта. Точная причина указана в технических подробностях."
    else:
        message = str(error)
    return ExportError(message, diagnostics or repr(error))


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


def export_size(width: int, height: int, resolution: int = 0, upscale: bool = False, *, even: bool = True) -> tuple[int, int]:
    if not 0 <= resolution <= MAX_EXPORT_LONG_EDGE:
        raise ValueError("Выберите разрешение экспорта до 4K (3840 × 2160).")
    long_edge = resolution or MAX_EXPORT_LONG_EDGE
    if not even:
        scale = min(long_edge / max(width, height), long_edge * 9 / 16 / min(width, height))
        if not upscale or not resolution:
            scale = min(1, scale)
        return max(1, round(width * scale)), max(1, round(height * scale))
    if upscale and resolution:
        scale = min(long_edge / max(width, height), long_edge * 9 / 16 / min(width, height))
        return max(2, round(width * scale) // 2 * 2), max(2, round(height * scale) // 2 * 2)
    return fit_size(width, height, long_edge, long_edge * 9 / 16)


def export_fps(source_fps: float, requested_fps: float = 0, format: str = "") -> float:
    limit = 50 if format == "gif" else MAX_EXPORT_FPS
    fps = requested_fps or min(source_fps, 30 if format == "gif" else limit)
    if not math.isfinite(fps) or not 0.1 <= fps <= limit:
        raise ValueError(f"Частота кадров должна быть от 0,1 до {limit} для этого формата.")
    return fps


def audio_export_filter(duration: float) -> str:
    # Bound audio to the output timeline instead of infinite apad + -shortest.
    # AAC consumes 1024 samples; normalize input frames to avoid FFmpeg #25878
    # (ENOSPC from the sync FIFO, unrelated to disk space).
    return (f"aresample=48000,apad=whole_dur={duration:.9f},"
            f"atrim=end={duration:.9f},asetnsamples=n=1024:p=0")


def validate_duration(duration: float):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Не удалось определить длительность видео.")
    if duration > MAX_VIDEO_SECONDS + 1e-6:
        raise ValueError(DURATION_LIMIT_ERROR)


def interpolation_active(info: VideoInfo, options: ExportOptions) -> bool:
    return options.interpolate and export_fps(info.fps, options.fps, options.format) > info.fps + 0.01


def decoder_plan(info: VideoInfo, options: ExportOptions) -> tuple[tuple[int, int], str]:
    """Decode for motion/analysis separately from the final high-resolution glyph raster."""
    output_width, output_height = export_size(info.width, info.height, options.resolution, options.upscale)
    width, height = fit_size(info.width, info.height, max(output_width, output_height), min(output_width, output_height))
    fps = export_fps(info.fps, options.fps, options.format)
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
        if info.kind == "gif":
            # The GIF demuxer can infer an overlong final packet; Pillow gives
            # us the declared single-loop duration, including the last delay.
            filters += [f"trim=duration={info.duration:.9f}"]
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


def probe_media(path: str | Path) -> VideoInfo:
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("Файл не найден.")
    return probe_picture(path) or probe_video(path)


def video_encoding_args(format: str, crf: int) -> list[str]:
    if format == "gif":
        # Per-frame palettes bound memory; no whole-animation frame cache.
        return ["-vf", "split[a][b];[a]palettegen=stats_mode=single:reserve_transparent=0[p];"
                       "[b][p]paletteuse=new=1:dither=bayer:bayer_scale=3", "-loop", "0"]
    if format == "webm":
        return ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf + 10),
                "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-threads", "4", "-pix_fmt", "yuv420p"]
    if format == "avi":
        return ["-c:v", "mpeg4", "-q:v", "2", "-bf", "0", "-enc_time_base", "1:60000",
                "-threads", "4", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-threads", "4",
            "-rc-lookahead", "12", "-pix_fmt", "yuv420p"]


def container_args(format: str) -> list[str]:
    return ["-movflags", "+faststart"] if format in ("mp4", "mov") else []


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

    def _run_image(self, info, target, settings, options, progress):
        temporary = None
        started = time.monotonic()
        try:
            self._check_cancel()
            current = probe_media(info.path)
            if not current.is_image or (current.width, current.height) != (info.width, info.height):
                raise ValueError("Исходное изображение изменилось. Откройте файл заново.")
            size = export_size(info.width, info.height, options.resolution, options.upscale, even=False)
            with PictureReader(info) as reader:
                rgb = reader.read(background=settings.background, size=size)
            self._check_cancel()
            output = AsciiRenderer().render(rgb, settings, size)
            self._check_cancel()
            handle, name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=f".part{target.suffix}", dir=target.parent)
            os.close(handle)
            temporary = Path(name)
            save_options = {"jpg": {"quality": 95, "subsampling": 0, "optimize": True},
                            "webp": {"lossless": True, "method": 4}, "tiff": {"compression": "tiff_lzw"}}
            Image.fromarray(output).save(temporary, format=IMAGE_FORMATS[options.format].upper(),
                                         **save_options.get(options.format, {}))
            self._check_cancel()
            os.replace(temporary, target)
            if progress:
                progress(1, 1, time.monotonic() - started)
            return target
        except OSError as error:
            raise describe_export_error(error, "") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def run(
        self, info: VideoInfo, destination: str | Path, settings: RenderSettings,
        options: ExportOptions = ExportOptions(),
        progress: Callable[[int, int, float], None] | None = None,
        phase: Callable[[str], None] | None = None,
    ) -> Path:
        target = Path(destination).resolve()
        if target == info.path.resolve():
            raise ValueError("Выберите другое имя: исходное видео нельзя перезаписать.")
        format = format_key(target.suffix)
        available = IMAGE_FORMATS if info.is_image else VIDEO_FORMATS
        if format not in available:
            raise ValueError("Выберите формат изображения." if info.is_image else "Выберите формат видео или GIF.")
        if options.format and format_key(options.format) != format:
            raise ValueError("Расширение файла не соответствует выбранному формату экспорта.")
        options = replace(options, format=format)
        if not target.parent.is_dir():
            raise ValueError("Папка для сохранения не найдена.")
        if info.is_image:
            return self._run_image(info, target, settings, options, progress)
        validate_duration(info.duration)
        width, height = export_size(info.width, info.height, options.resolution, options.upscale)
        fps = export_fps(info.fps, options.fps, format)
        self._check_cancel()
        current = probe_media(info.path)
        if ((current.width, current.height) != (info.width, info.height)
                or current.kind != info.kind or current.frame_durations != info.frame_durations
                or abs(current.duration - info.duration) > 0.01 or abs(current.fps - info.fps) > 0.01):
            raise ValueError("Исходное видео изменилось. Откройте его заново перед экспортом.")
        (decode_width, decode_height), video_filter = decoder_plan(info, options)
        input_options = []
        if info.kind == "gif":
            input_options = ["-ignore_loop", "1", "-min_delay", "1", "-default_delay", "10"]
            r, g, b = hex_rgb(settings.background)
            video_filter = (f"format=rgba,split[fg][bg];[bg]lutrgb=r={r}:g={g}:b={b}:a=255[solid];"
                            f"[solid][fg]overlay=shortest=1:format=rgb,{video_filter}")
        fps_arg = f"{fps:.8f}"
        expected = max(1, round(info.duration * fps))
        max_frames = max(1, math.floor(MAX_VIDEO_SECONDS * fps + 1e-6))
        renderer = AsciiRenderer()
        with_audio = options.audio and info.has_audio and format != "gif"
        temporary = video_temporary = decoder_log = encoder_log = muxer_log = None
        started = time.monotonic()
        try:
            handle, name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=f".part.{format}", dir=target.parent)
            os.close(handle)
            temporary = Path(name)
            video_temporary = temporary
            if with_audio:
                handle, name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=f".video.part.{format}", dir=target.parent)
                os.close(handle)
                video_temporary = Path(name)
            decoder_log = tempfile.TemporaryFile()
            encoder_log = tempfile.TemporaryFile()
            self._check_cancel()
            # The fps filter uses timestamps, including variable-frame-rate inputs.
            decoder = self._start([
                ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-threads", "4", *input_options, "-i", str(info.path), "-map", "0:v:0", "-an", "-sn", "-dn",
                "-vf", video_filter,
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
            ], stdout=subprocess.PIPE, stderr=decoder_log, stdin=subprocess.DEVNULL)
            args = [
                ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
                "-r", fps_arg, "-i", "pipe:0",
            ]
            args += ["-map", "0:v:0", "-an"]
            args += video_encoding_args(format, options.crf)
            args += ["-map_metadata", "-1", *container_args(format), str(video_temporary)]
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
            if count == 0 or video_temporary.stat().st_size < 100:
                raise RuntimeError("Видео не содержит кадров для экспорта.")
            if with_audio:
                # Add audio only after rendering, with the actual video duration.
                # This avoids racing audio against a slow raw-video pipe, and
                # does not trust container duration when audio outlasts video.
                if phase:
                    phase("Добавляем звук…")
                muxer_log = tempfile.TemporaryFile()
                audio_codec = {"webm": "libopus", "avi": "libmp3lame"}.get(format, "aac")
                muxer = self._start([
                    ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-i", str(video_temporary), "-i", str(info.path),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                    "-c:a", audio_codec, "-b:a", "192k", "-af", audio_export_filter(count / fps),
                    "-map_metadata", "-1", *container_args(format), str(temporary),
                ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=muxer_log)
                while muxer.poll() is None:
                    self._check_cancel()
                    self.cancelled.wait(0.1)
                self._check_cancel()
                if muxer.returncode != 0 or temporary.stat().st_size < 100:
                    raise RuntimeError("Не удалось добавить звук к видео.")
            # Publish only a complete file; cancellation never destroys an older export.
            os.replace(temporary, target)
            if progress:
                progress(count, count, time.monotonic() - started)
            return target
        except Exception as error:
            if self.cancelled.is_set():
                raise ExportCancelled() from error
            if isinstance(error, (OSError, RuntimeError)):
                logs = []
                for log in (muxer_log, encoder_log, decoder_log):
                    if log is not None:
                        log.seek(0)
                        logs.append(log.read())
                details = b"\n".join(logs).decode("utf-8", errors="replace").strip()
                raise describe_export_error(error, details[-8000:]) from error
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
            for log in (decoder_log, encoder_log, muxer_log):
                if log is not None:
                    log.close()
            for path in {temporary, video_temporary}:
                if path is not None:
                    path.unlink(missing_ok=True)
