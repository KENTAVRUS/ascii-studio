from __future__ import annotations

import subprocess
import threading

import cv2
import numpy as np
import pytest
from PIL import Image

from ascii_studio.autotune import analyze_video
from ascii_studio.engine import (AsciiRenderer, ExportCancelled, ExportJob, ExportOptions, NO_WINDOW,
                                 RenderSettings, ffmpeg_path, probe_media, probe_video)
from ascii_studio.media import IMAGE_FORMATS, VIDEO_FORMATS, PictureReader


@pytest.fixture
def photograph(tmp_path):
    gray = np.tile(np.linspace(10, 245, 321, dtype=np.uint8), (181, 1))
    path = tmp_path / "Фото с пробелами.png"
    Image.fromarray(np.repeat(gray[..., None], 3, axis=2)).save(path)
    return path


@pytest.mark.parametrize("format", IMAGE_FORMATS)
def test_image_export_formats_preserve_odd_dimensions(photograph, tmp_path, format):
    info = probe_media(photograph)
    assert info.is_image and info.fps == 0 and info.duration == 0
    output = tmp_path / f"готовое фото.{format}"
    ExportJob().run(info, output, RenderSettings(columns=70))
    with Image.open(output) as image:
        assert image.format == IMAGE_FORMATS[format].upper()
        assert image.size == (321, 181)
        assert np.asarray(image).mean() > 0
    loaded = probe_media(output)
    assert loaded.is_image and (loaded.width, loaded.height) == (321, 181)
    assert not list(tmp_path.glob("*.part.*"))


def test_photo_exif_orientation_and_auto(tmp_path, photograph):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    with Image.open(photograph) as image:
        image.save(path, exif=exif)
    info = probe_media(path)
    assert (info.width, info.height) == (181, 321)
    with PictureReader(info) as reader:
        assert reader.read().shape == (321, 181, 3)
    suggestion = analyze_video(info, RenderSettings())
    assert suggestion.frames_used == suggestion.frames_sampled == 1
    target = tmp_path / "upright.png"
    ExportJob().run(info, target, suggestion.settings)
    with Image.open(target) as image:
        assert image.size == (181, 321) and image.getexif().get(274, 1) == 1


def test_transparent_photo_uses_selected_background(tmp_path):
    path = tmp_path / "transparent.png"
    pixels = np.zeros((90, 160, 4), dtype=np.uint8)
    pixels[..., :3] = 255  # Hidden white must not become visible when alpha is flattened.
    pixels[20:60, 30:100, 3] = 255
    Image.fromarray(pixels).save(path)
    info = probe_media(path)
    settings = RenderSettings(background="#102030")
    with PictureReader(info) as reader:
        rgb = reader.read(background=settings.background)
    np.testing.assert_array_equal(rgb[0, 0], [16, 32, 48])
    target = tmp_path / "opaque.png"
    ExportJob().run(info, target, settings)
    with Image.open(target) as result:
        np.testing.assert_array_equal(np.asarray(result), AsciiRenderer().render(rgb, settings))


@pytest.fixture
def gif_source(tmp_path):
    path = tmp_path / "неровные интервалы.gif"
    frames = []
    for x in (10, 60, 110):
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[20:70, x:x + 30] = 255
        frames.append(Image.fromarray(image))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[40, 160, 100], loop=0, disposal=2)
    return path


def test_gif_preview_and_export_follow_frame_delays(gif_source, tmp_path):
    info = probe_media(gif_source)
    assert info.kind == "gif" and info.frames == 3 and info.duration == pytest.approx(0.3)
    assert info.frame_durations == (0.04, 0.16, 0.1)
    with PictureReader(info) as reader:
        assert reader.read(0.02)[30, 15, 0] == 255
        assert reader.read(0.12)[30, 65, 0] == 255
        assert reader.read(0.27)[30, 115, 0] == 255
        assert reader.read(0.02)[30, 15, 0] == 255  # Seeking backwards replays disposal.
    target = tmp_path / "animation.mp4"
    ExportJob().run(info, target, RenderSettings(columns=40), ExportOptions(fps=30))
    result = probe_video(target)
    assert result.frames == 9 and result.duration == pytest.approx(0.3)
    capture = cv2.VideoCapture(str(target))
    frames = []
    try:
        while True:
            success, frame = capture.read()
            if not success:
                break
            frames.append(frame)
    finally:
        capture.release()
    for index, center in [(0, 25), (4, 75), (8, 125)]:
        ys, xs = np.nonzero(frames[index].mean(axis=2) > 35)
        assert len(xs) > 100 and abs(xs.mean() - center) < 12


@pytest.fixture(scope="module")
def video_source(tmp_path_factory):
    path = tmp_path_factory.mktemp("formats") / "source.mp4"
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=24:duration=0.5", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.2", "-c:v", "libx264", "-threads", "2",
                    "-c:a", "aac", str(path)], check=True, creationflags=NO_WINDOW, timeout=30)
    return path


@pytest.mark.parametrize("format", VIDEO_FORMATS)
def test_video_exports_are_real_containers_with_correct_streams(video_source, tmp_path, format):
    target = tmp_path / f"результат.{format}"
    fps = 20 if format == "gif" else 24
    ExportJob().run(probe_media(video_source), target, RenderSettings(columns=70), ExportOptions(fps=fps))
    result = probe_media(target)
    assert (result.width, result.height) == (160, 90)
    metadata = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(target)], capture_output=True,
                              creationflags=NO_WINDOW, timeout=30).stderr.decode(errors="replace")
    assert {"mp4": "h264", "mov": "h264", "mkv": "h264", "webm": "vp9", "avi": "mpeg4", "gif": "gif"}[format] in metadata
    if format == "gif":
        assert not result.has_audio and result.duration == pytest.approx(0.5, abs=0.02)
        with Image.open(target) as image:
            assert image.info["loop"] == 0 and image.n_frames >= 2
    else:
        assert result.has_audio
        assert {"webm": "opus", "avi": "mp3"}.get(format, "aac") in metadata
        capture = cv2.VideoCapture(str(target))
        count = 0
        try:
            while capture.read()[0]:
                count += 1
        finally:
            capture.release()
        assert count == 12
    assert not list(tmp_path.glob("*.part.*"))


@pytest.mark.parametrize("format", ["gif", "webm", "mov"])
def test_cancelling_new_formats_preserves_previous_file(video_source, tmp_path, format):
    target = tmp_path / f"existing.{format}"
    target.write_bytes(b"previous complete file")
    job = ExportJob()
    with pytest.raises(ExportCancelled):
        job.run(probe_media(video_source), target, RenderSettings(), ExportOptions(fps=24),
                progress=lambda *_: job.cancel())
    assert target.read_bytes() == b"previous complete file"
    assert not job._processes and not list(tmp_path.glob("*.part.*"))


def test_photo_cancellation_and_source_protection(photograph, tmp_path, monkeypatch):
    info = probe_media(photograph)
    before = photograph.read_bytes()
    with pytest.raises(ValueError, match="исходное"):
        ExportJob().run(info, photograph, RenderSettings())
    assert photograph.read_bytes() == before
    target = tmp_path / "existing.png"
    target.write_bytes(b"previous image")
    save = Image.Image.save
    job = ExportJob()

    def save_and_cancel(*args, **kwargs):
        save(*args, **kwargs)
        job.cancel()

    monkeypatch.setattr(Image.Image, "save", save_and_cancel)
    with pytest.raises(ExportCancelled):
        job.run(info, target, RenderSettings())
    assert target.read_bytes() == b"previous image" and not list(tmp_path.glob("*.part.*"))


def test_incompatible_formats_and_gif_rate_are_rejected(photograph, video_source, tmp_path):
    with pytest.raises(ValueError, match="изображения"):
        ExportJob().run(probe_media(photograph), tmp_path / "wrong.mp4", RenderSettings())
    with pytest.raises(ValueError, match="видео или GIF"):
        ExportJob().run(probe_media(video_source), tmp_path / "wrong.png", RenderSettings())
    with pytest.raises(ValueError, match="50"):
        ExportJob().run(probe_media(video_source), tmp_path / "wrong.gif", RenderSettings(), ExportOptions(fps=240))
    assert not list(tmp_path.glob("*.part.*"))


def test_gif_transparency_and_ten_millisecond_frames(tmp_path):
    path = tmp_path / "transparent.gif"
    frames = []
    for x in (10, 60, 110):
        pixels = np.zeros((90, 160, 4), np.uint8)
        pixels[..., :3] = 255
        pixels[20:70, x:x+30, 3] = 255
        frames.append(Image.fromarray(pixels))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[100, 100, 10], loop=0, disposal=2)
    info = probe_media(path)
    assert info.duration == pytest.approx(0.21)
    settings = RenderSettings(columns=40, background="#182838")
    with PictureReader(info) as reader:
        frame = reader.read(0.205, settings.background)
        np.testing.assert_array_equal(frame[0, 0], [24, 40, 56])
        assert frame[30, 115, 0] == 255
        np.testing.assert_array_equal(frame[30, 15], [24, 40, 56])
    target = tmp_path / "transparent-result.mp4"
    ExportJob().run(info, target, settings, ExportOptions(fps=100))
    assert probe_video(target).frames == 21
    cap = cv2.VideoCapture(str(target))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 20)
        success, bgr = cap.read()
        assert success
        expected = AsciiRenderer().render(frame, settings)
        assert np.abs(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(float) - expected).mean() < 8
    finally:
        cap.release()


def test_gif_duration_limit_and_unsupported_animated_image(tmp_path):
    first, second = Image.new("RGB", (64, 36), "red"), Image.new("RGB", (64, 36), "blue")
    long_gif = tmp_path / "long.gif"
    first.save(long_gif, save_all=True, append_images=[second], duration=[150000, 151000])
    with pytest.raises(ValueError, match="5 минут"):
        probe_media(long_gif)
    webp = tmp_path / "animated.webp"
    first.save(webp, save_all=True, append_images=[second], duration=100, loop=0)
    with pytest.raises(ValueError, match="Анимированные WebP"):
        probe_media(webp)


def test_gif_export_at_4k(tmp_path):
    source = tmp_path / "tiny.mp4"
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=50:duration=0.06", "-c:v", "libx264", "-threads", "2", str(source)],
                   check=True, creationflags=NO_WINDOW, timeout=30)
    target = tmp_path / "4k.gif"
    ExportJob().run(probe_media(source), target, RenderSettings(columns=70),
                    ExportOptions(resolution=3840, fps=50, upscale=True))
    result = probe_media(target)
    assert (result.width, result.height) == (3840, 2160)
    assert result.duration == pytest.approx(0.06, abs=0.01)


def test_gif_interpolation_preserves_a_single_loop(gif_source, tmp_path):
    target = tmp_path / "interpolated.mp4"
    ExportJob().run(probe_media(gif_source), target, RenderSettings(), ExportOptions(fps=60, interpolate=True))
    result = probe_video(target)
    assert result.frames == 18 and result.duration == pytest.approx(0.3)
