from __future__ import annotations

import subprocess
import threading
from dataclasses import replace

import cv2
import numpy as np
import pytest

from ascii_studio.autotune import AutoTuneCancelled, analyze_video, suggest_settings
from ascii_studio.engine import (
    ExportCancelled, ExportJob, ExportOptions, NO_WINDOW, RenderSettings,
    decoder_plan, export_size, ffmpeg_path, probe_video,
)


def encode_frames(path, frames, fps=12):
    height, width = frames[0].shape[:2]
    result = subprocess.run([
        ffmpeg_path(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-threads", "2",
        "-pix_fmt", "yuv420p", str(path),
    ], input=b"".join(frame.tobytes() for frame in frames), capture_output=True, creationflags=NO_WINDOW, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return path


def decode_plan(info, options):
    (width, height), filters = decoder_plan(info, options)
    result = subprocess.run([
        ffmpeg_path(), "-v", "error", "-i", str(info.path), "-an", "-vf", filters,
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ], capture_output=True, creationflags=NO_WINDOW, timeout=45)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return np.frombuffer(result.stdout, np.uint8).reshape(-1, height, width, 3)


@pytest.fixture(scope="module")
def moving_video(tmp_path_factory):
    folder = tmp_path_factory.mktemp("motion")
    rng = np.random.default_rng(41)
    texture = rng.integers(130, 255, (48, 48, 3), dtype=np.uint8)
    frames = []
    for index in range(8):
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        x = 32 + index * 10
        frame[64:112, x:x+48] = texture
        frames.append(frame)
    return encode_frames(folder / "moving.mp4", frames)


def test_motion_interpolation_creates_in_between_positions(moving_video):
    info = probe_video(moving_video)
    repeated = decode_plan(info, ExportOptions(fps=48))
    smooth = decode_plan(info, ExportOptions(fps=48, interpolate=True))
    assert len(repeated) == len(smooth) == 32
    difference = np.abs(repeated.astype(float) - smooth).mean(axis=(1, 2, 3))
    assert np.count_nonzero(difference[4:24] > 0.8) >= 6

    def center(frame):
        ys, xs = np.nonzero(frame.mean(axis=2) > 90)
        assert len(xs) > 1000
        return float(xs.mean())

    # A half-time frame should contain a translated object between source positions.
    source = decode_plan(info, ExportOptions())
    assert center(source[1]) + 2 < center(smooth[6]) < center(source[2]) - 2


@pytest.mark.parametrize("count", [1, 2, 8])
def test_interpolation_keeps_short_clip_duration(tmp_path, count):
    frames = [np.full((64, 96, 3), 30 + i * 20, dtype=np.uint8) for i in range(count)]
    source = encode_frames(tmp_path / f"short-{count}.mp4", frames)
    target = tmp_path / "result.mp4"
    ExportJob().run(probe_video(source), target, RenderSettings(columns=40), ExportOptions(fps=48, interpolate=True))
    info = probe_video(target)
    assert info.frames == count * 4
    assert info.duration == pytest.approx(count / 12)


def test_scene_cut_does_not_blend_unrelated_scenes(tmp_path):
    frames = [np.full((64, 96, 3), value, np.uint8) for value in [0] * 4 + [255] * 4]
    info = probe_video(encode_frames(tmp_path / "cut.mp4", frames))
    output = decode_plan(info, ExportOptions(fps=48, interpolate=True))
    means = output.mean(axis=(1, 2, 3))
    assert len(output) == 32
    assert np.all((means < 3) | (means > 252))


def test_upscale_redraws_glyphs_at_4k_and_keeps_orientation(tmp_path, moving_video):
    assert export_size(1280, 720, 3840, upscale=True) == (3840, 2160)
    assert export_size(720, 1280, 3840, upscale=True) == (2160, 3840)
    assert export_size(1280, 720, 0, upscale=True) == (1280, 720)
    assert export_size(1280, 720, 3840, upscale=False) == (1280, 720)
    assert export_size(1000, 1000, 3840, upscale=True) == (2160, 2160)
    source = encode_frames(tmp_path / "small.mp4", [np.full((90, 160, 3), 180, np.uint8)], fps=24)
    target = tmp_path / "upscaled.mp4"
    ExportJob().run(probe_video(source), target, RenderSettings(columns=70), ExportOptions(resolution=3840, upscale=True))
    result = probe_video(target)
    assert (result.width, result.height, result.frames) == (3840, 2160, 1)
    capture = cv2.VideoCapture(str(target))
    try:
        success, frame = capture.read()
        assert success and frame.shape == (2160, 3840, 3) and frame.mean() > 0
    finally:
        capture.release()


def test_interpolated_export_preserves_audio_and_duration(tmp_path, moving_video):
    source = tmp_path / "with-audio.mp4"
    subprocess.run([
        ffmpeg_path(), "-v", "error", "-y", "-i", str(moving_video),
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-c:v", "copy", "-c:a", "aac", str(source),
    ], check=True, creationflags=NO_WINDOW, timeout=30)
    target = tmp_path / "smooth.mp4"
    ExportJob().run(probe_video(source), target, RenderSettings(), ExportOptions(fps=48, interpolate=True))
    result = probe_video(target)
    assert result.has_audio and result.frames == 32 and result.fps == 48
    assert result.duration == pytest.approx(8 / 12)


def test_interpolation_cancel_preserves_old_file(tmp_path, moving_video):
    target = tmp_path / "existing.mp4"
    target.write_bytes(b"old video")
    job = ExportJob()
    with pytest.raises(ExportCancelled):
        job.run(probe_video(moving_video), target, RenderSettings(), ExportOptions(fps=240, interpolate=True),
                progress=lambda *_: job.cancel())
    assert target.read_bytes() == b"old video"
    assert not list(tmp_path.glob("*.part.mp4"))


def test_vfr_motion_interpolation_keeps_timeline(tmp_path):
    source = tmp_path / "vfr.mp4"
    subprocess.run([
        ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=2",
        "-vf", "select='if(lt(t,1),not(mod(n,3)),1)'", "-fps_mode", "vfr", "-c:v", "libx264", str(source),
    ], check=True, creationflags=NO_WINDOW, timeout=30)
    target = tmp_path / "smooth-vfr.mp4"
    ExportJob().run(probe_video(source), target, RenderSettings(columns=70), ExportOptions(fps=48, interpolate=True))
    result = probe_video(target)
    assert result.fps == 48 and result.duration == pytest.approx(2, abs=0.05)


def gradient(low, high):
    values = np.tile(np.linspace(low, high, 320, dtype=np.uint8), (180, 1))
    return np.repeat(values[..., None], 3, axis=2)


def tone(gray, settings):
    return np.clip((gray / 255 - 0.5) * settings.contrast + 0.5 + settings.brightness, 0, 1)


def test_auto_improves_dark_detail_and_preserves_other_choices():
    frames = [gradient(15, 75), gradient(20, 80), gradient(10, 70)]
    original = RenderSettings(columns=230, palette="matrix", charset="binary", background="#050505", invert=True)
    result = suggest_settings(frames, original)
    assert result.frames_used == result.frames_sampled == 3
    assert replace(result.settings, contrast=original.contrast, brightness=original.brightness, edges=original.edges) == original
    before = tone(frames[0][..., 0], original)
    after = tone(frames[0][..., 0], result.settings)
    assert np.mean(after < 0.03) < np.mean(before < 0.03)
    assert after.mean() > before.mean() + 0.1


def test_auto_protects_highlight_detail():
    frames = [gradient(175, 250), gradient(170, 245)]
    original = RenderSettings()
    result = suggest_settings(frames, original)
    assert result.settings.brightness < 0
    assert np.mean(tone(frames[0][..., 0], result.settings) > 0.975) < np.mean(tone(frames[0][..., 0], original) > 0.975)


def test_auto_ignores_blank_fades_and_letterbox_bars():
    frame = gradient(30, 210)
    plain = suggest_settings([frame], RenderSettings())
    bordered = cv2.copyMakeBorder(frame, 30, 30, 0, 0, cv2.BORDER_CONSTANT, value=0)
    tested = suggest_settings([np.zeros_like(bordered), bordered, np.full_like(bordered, 255)], RenderSettings())
    assert tested.frames_used == 1 and tested.frames_sampled == 3
    assert tested.settings == plain.settings


def test_auto_reduces_edge_enhancement_for_noise():
    clean = gradient(60, 190)
    rng = np.random.default_rng(9)
    noisy = np.clip(clean.astype(float) + rng.normal(0, 25, clean.shape[:2])[..., None], 0, 255).astype(np.uint8)
    smooth = suggest_settings([clean], RenderSettings())
    noise = suggest_settings([noisy], RenderSettings())
    assert noise.settings.edges < smooth.settings.edges


def test_auto_is_deterministic_and_cancellable(moving_video):
    info = probe_video(moving_video)
    first = analyze_video(info, RenderSettings())
    second = analyze_video(info, RenderSettings())
    assert first == second and first.frames_used > 0
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(AutoTuneCancelled):
        analyze_video(info, RenderSettings(), cancelled)
    with pytest.raises(ValueError, match="мало деталей"):
        suggest_settings([np.zeros((60, 100, 3), np.uint8)], RenderSettings())
