from __future__ import annotations

import subprocess
from dataclasses import replace

import cv2
import numpy as np
import pytest

from ascii_studio.engine import (
    AsciiRenderer, ExportJob, ExportOptions, NO_WINDOW, RenderSettings,
    export_size, ffmpeg_path, probe_video,
)


def make_video(path, size="96x54", fps=1, frames=300):
    subprocess.run([
        ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-f", "lavfi",
        "-i", f"testsrc2=size={size}:rate={fps}", "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", str(path),
    ], check=True, creationflags=NO_WINDOW, timeout=60)
    return path


@pytest.fixture(scope="module")
def duration_inputs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("duration")
    return (
        make_video(folder / "exactly-five-minutes.mp4"),
        make_video(folder / "too-long.mp4", frames=301),
        make_video(folder / "just-over.mp4", fps=10, frames=3001),
    )


@pytest.fixture(scope="module")
def uhd_source(tmp_path_factory):
    folder = tmp_path_factory.mktemp("uhd")
    return make_video(folder / "uhd-240.mp4", size="3840x2160", fps=240, frames=8)


@pytest.mark.parametrize("palette", ["gold", "original"])
def test_real_4k_export_at_240_fps(uhd_source, tmp_path, palette):
    info = probe_video(uhd_source)
    target = tmp_path / f"4k-240-{palette}.mp4"
    ExportJob().run(info, target, RenderSettings(columns=150, palette=palette),
                    ExportOptions(resolution=3840, fps=240, audio=False))
    output = probe_video(target)
    assert (output.width, output.height) == (3840, 2160)
    assert output.fps == pytest.approx(240)
    assert output.frames == 8 and output.duration == pytest.approx(8 / 240)
    capture = cv2.VideoCapture(str(target))
    try:
        success, frame = capture.read()
        assert success and frame.shape == (2160, 3840, 3)
        assert frame.mean() > 0
    finally:
        capture.release()


def test_240_fps_export_preserves_audio(tmp_path):
    source = tmp_path / "audio.mp4"
    subprocess.run([
        ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=0.2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2", "-c:v", "libx264", "-threads", "2",
        "-c:a", "aac", str(source),
    ], check=True, creationflags=NO_WINDOW, timeout=30)
    target = tmp_path / "audio-240.mp4"
    ExportJob().run(probe_video(source), target, RenderSettings(), ExportOptions(fps=240))
    output = probe_video(target)
    assert output.has_audio and output.frames == 48
    assert output.fps == 240 and output.duration == pytest.approx(0.2)


def test_exactly_five_minutes_can_be_opened_and_exported(duration_inputs, tmp_path):
    source, _, _ = duration_inputs
    info = probe_video(source)
    assert info.duration == 300
    target = tmp_path / "five-minute-ascii.mp4"
    ExportJob().run(info, target, RenderSettings(columns=40), ExportOptions(audio=False))
    output = probe_video(target)
    assert output.duration == 300 and output.frames == 300


def test_overlong_inputs_are_rejected(duration_inputs):
    _, too_long, just_over = duration_inputs
    for path in (too_long, just_over):
        with pytest.raises(ValueError, match="5 минут"):
            probe_video(path)


def test_export_revalidates_duration_and_preserves_destination(duration_inputs, tmp_path):
    source, too_long, _ = duration_inputs
    info = probe_video(source)
    target = tmp_path / "previous.mp4"
    target.write_bytes(b"previous complete video")
    with pytest.raises(ValueError, match="5 минут"):
        ExportJob().run(replace(info, duration=301), target, RenderSettings())
    # Stale or inaccurate metadata must not allow more than five minutes through the encoder.
    with pytest.raises(ValueError, match="5 минут"):
        ExportJob().run(replace(info, path=too_long), target, RenderSettings(columns=40))
    assert target.read_bytes() == b"previous complete video"
    assert not list(tmp_path.glob("*.part.mp4"))


def test_auto_caps_source_fps_without_changing_probed_duration(tmp_path):
    source = make_video(tmp_path / "300fps.mp4", fps=300, frames=30)
    info = probe_video(source)
    assert info.fps == 300 and info.duration == pytest.approx(0.1)
    target = tmp_path / "auto.mp4"
    ExportJob().run(info, target, RenderSettings(columns=40))
    result = probe_video(target)
    assert result.fps == 240 and result.frames == 24


def test_resolution_bounds_keep_orientation_and_do_not_upscale():
    assert export_size(7680, 4320) == (3840, 2160)
    assert export_size(4320, 7680, 3840) == (2160, 3840)
    assert export_size(3840, 2160, 2560) == (2560, 1440)
    assert export_size(3840, 2160, 1920) == (1920, 1080)
    assert export_size(3840, 2160, 1280) == (1280, 720)
    assert export_size(1920, 1080, 3840) == (1920, 1080)
    assert export_size(4000, 4000) == (2160, 2160)
    with pytest.raises(ValueError, match="4K"):
        export_size(7680, 4320, 7680)


def test_rates_above_240_are_rejected(duration_inputs, tmp_path):
    with pytest.raises(ValueError, match="240"):
        ExportJob().run(probe_video(duration_inputs[0]), tmp_path / "invalid-fps.mp4",
                        RenderSettings(), ExportOptions(fps=241))
    assert not list(tmp_path.iterdir())


def test_original_color_blending_matches_the_same_solid_custom_color():
    color = (110, 182, 230)
    source = np.full((180, 320, 3), color, dtype=np.uint8)
    renderer = AsciiRenderer()
    settings = RenderSettings(columns=70, palette="original", background="#f0e0d0")
    original = renderer.render(source, settings)
    custom = renderer.render(source, replace(settings, palette="custom", custom_color="#6eb6e6"))
    np.testing.assert_array_equal(original, custom)
