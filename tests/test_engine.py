from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from ascii_studio.engine import (
    AsciiRenderer, ExportCancelled, ExportJob, ExportOptions, NO_WINDOW,
    RenderSettings, ffmpeg_path, fit_size, probe_video,
)


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    folder = tmp_path_factory.mktemp("media")
    path = folder / "Тест со звуком.mp4"
    subprocess.run([
        ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=1.5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.6", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True, creationflags=NO_WINDOW)
    return path


def media_metadata(path):
    result = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path)],
                            capture_output=True, creationflags=NO_WINDOW)
    return result.stderr.decode(errors="replace")


def test_glyph_grid_colors_detail_and_inversion():
    gray = np.tile(np.linspace(0, 255, 320, dtype=np.uint8), (180, 1))
    source = np.stack([gray, gray, gray], axis=-1)
    renderer = AsciiRenderer()
    gold = renderer.render(source, RenderSettings(columns=70), (320, 180))
    fine = renderer.render(source, RenderSettings(columns=230), (320, 180))
    assert gold.shape == source.shape and gold.dtype == np.uint8
    assert np.mean(np.abs(gold.astype(float) - fine)) > 4
    assert gold[..., 0].sum() > gold[..., 1].sum() > gold[..., 2].sum()
    white = renderer.render(source, RenderSettings(palette="custom", custom_color="#ffffff"))
    assert np.array_equal(white[..., 0], white[..., 1])
    green = renderer.render(source, RenderSettings(palette="matrix"))
    assert green[..., 1].sum() > green[..., 0].sum() * 2
    blank = np.zeros_like(source)
    assert not renderer.render(blank, RenderSettings(brightness=0)).any()
    assert renderer.render(blank, RenderSettings(invert=True, brightness=0)).sum() > 0


@pytest.mark.parametrize("charset", ["classic", "detailed", "minimal", "binary"])
def test_charsets_preserve_output_dimensions(charset):
    rng = np.random.default_rng(4)
    source = rng.integers(0, 256, (190, 108, 3), dtype=np.uint8)
    output = AsciiRenderer().render(source, RenderSettings(charset=charset), (108, 190))
    assert output.shape == source.shape
    assert output.sum() > 0


def test_export_audio_timing_and_unicode_paths(video, tmp_path):
    info = probe_video(video)
    assert info.has_audio and info.frames == 18
    destination = tmp_path / "Готовое аски видео.mp4"
    updates = []
    ExportJob().run(info, destination, RenderSettings(columns=70),
                    ExportOptions(fps=24), lambda *args: updates.append(args))
    exported = probe_video(destination)
    assert exported.width == 320 and exported.height == 180
    assert exported.frames == 36 and exported.fps == pytest.approx(24)
    assert exported.duration == pytest.approx(1.5, abs=0.05)
    assert exported.has_audio  # A shorter audio track must not truncate the video.
    assert "h264" in media_metadata(destination)
    assert updates[-1][0] == updates[-1][1] == 36
    assert not list(tmp_path.glob("*.part.mp4"))


def test_export_without_audio(video, tmp_path):
    destination = tmp_path / "muted.mp4"
    ExportJob().run(probe_video(video), destination, RenderSettings(), ExportOptions(audio=False, resolution=200))
    info = probe_video(destination)
    assert not info.has_audio
    assert info.width == 200 and info.height == 112
    second = tmp_path / "silent-source.mp4"
    ExportJob().run(info, second, RenderSettings(), ExportOptions(audio=True))
    assert not probe_video(second).has_audio


def test_cancellation_preserves_existing_destination(video, tmp_path):
    destination = tmp_path / "keep.mp4"
    destination.write_bytes(b"existing export")
    job = ExportJob()

    def cancel_after_first_frame(*args):
        job.cancel()

    with pytest.raises(ExportCancelled):
        job.run(probe_video(video), destination, RenderSettings(), progress=cancel_after_first_frame)
    assert destination.read_bytes() == b"existing export"
    assert not list(tmp_path.glob("*.part.mp4"))
    assert not job._processes


def test_input_is_never_overwritten(video):
    before = video.read_bytes()
    with pytest.raises(ValueError, match="исходное"):
        ExportJob().run(probe_video(video), video, RenderSettings())
    assert video.read_bytes() == before


def test_invalid_video_and_even_aspect_sizes(tmp_path):
    invalid = tmp_path / "broken.mp4"
    invalid.write_text("This is not a video")
    with pytest.raises(ValueError):
        probe_video(invalid)
    assert fit_size(1080, 1920, 1280) == (720, 1280)
    assert fit_size(641, 481, 1920) == (640, 480)


def test_portrait_rotation_is_not_lost(video, tmp_path):
    rotated = tmp_path / "rotated.mp4"
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-display_rotation", "90", "-i", str(video),
                    "-c", "copy", str(rotated)], check=True, creationflags=NO_WINDOW)
    info = probe_video(rotated)
    assert (info.width, info.height) == (180, 320)
    destination = tmp_path / "portrait-ascii.mp4"
    ExportJob().run(info, destination, RenderSettings(), ExportOptions(audio=False))
    output = probe_video(destination)
    assert (output.width, output.height) == (180, 320)


def test_vfr_input_exports_on_a_constant_timeline(tmp_path):
    source = tmp_path / "vfr.mp4"
    subprocess.run([
        ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=2",
        "-vf", "select='if(lt(t,1),not(mod(n,3)),1)'", "-fps_mode", "vfr", "-c:v", "libx264", str(source),
    ], check=True, creationflags=NO_WINDOW)
    info = probe_video(source)
    destination = tmp_path / "constant.mp4"
    ExportJob().run(info, destination, RenderSettings(columns=50), ExportOptions(fps=24))
    exported = probe_video(destination)
    assert exported.fps == pytest.approx(24)
    assert exported.duration == pytest.approx(2, abs=0.10)
