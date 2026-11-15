from __future__ import annotations

import errno
import subprocess

import numpy as np
import pytest

from ascii_studio.engine import (
    audio_export_filter, ExportCancelled, ExportError, ExportJob, ExportOptions, NO_WINDOW,
    RenderSettings, describe_export_error, ffmpeg_path, probe_video,
)


def run_ffmpeg(arguments):
    result = subprocess.run([ffmpeg_path(), "-v", "error", "-nostdin", "-y", *arguments],
                            capture_output=True, creationflags=NO_WINDOW, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def test_small_audio_frames_do_not_overflow_aac_sync_queue(tmp_path):
    # FFmpeg #25878: one-sample audio frames + -shortest used to overflow the
    # sync FIFO and return ENOSPC even when writing to the null muxer.
    target = tmp_path / "small-audio-frames.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=s=16x16:r=1:d=2[out0];anullsrc=r=8000:cl=mono:n=1:d=45[out1]",
        "-map", "0:v", "-map", "0:a", "-c:v", "mpeg4", "-c:a", "aac",
        "-af", audio_export_filter(300), "-shortest", "-shortest_buf_duration", "0.1", str(target),
    ])
    result = probe_video(target)
    assert result.frames == 2 and result.duration == 2 and result.has_audio
    assert target.stat().st_size < 100_000


@pytest.mark.parametrize("sample_rate,audio_duration", [(8000, 0.12), (44100, 0.12), (48000, 3.0)])
def test_audio_is_padded_or_trimmed_to_the_video(tmp_path, sample_rate, audio_duration):
    source = tmp_path / "source.mkv"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1.25",
        "-f", "lavfi", "-i", f"sine=sample_rate={sample_rate}:frequency=440:duration={audio_duration}",
        "-c:v", "libx264", "-threads", "2", "-c:a", "pcm_s16le", str(source),
    ])
    target = tmp_path / "result.mp4"
    ExportJob().run(probe_video(source), target, RenderSettings(columns=70), ExportOptions(fps=240))
    video = probe_video(target)
    assert video.frames == 300 and video.duration == 1.25 and video.has_audio
    pcm = np.frombuffer(run_ffmpeg(["-i", str(target), "-vn", "-ac", "1", "-ar", "48000",
                                    "-f", "f32le", "pipe:1"]), np.float32)
    assert abs(len(pcm) / 48000 - 1.25) < 0.05
    assert np.sqrt(np.mean(pcm[1000:4000] ** 2)) > 0.01
    if audio_duration < 1:
        assert np.max(np.abs(pcm[24000:48000])) < 0.005


def test_audio_delay_is_preserved(tmp_path):
    source = tmp_path / "delayed.mkv"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1.5",
        "-itsoffset", "0.3", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
        "-c:v", "libx264", "-threads", "2", "-c:a", "pcm_s16le", str(source),
    ])
    target = tmp_path / "delayed-result.mp4"
    ExportJob().run(probe_video(source), target, RenderSettings(columns=70), ExportOptions(fps=60))
    # Decode with a common zero origin, including the intentional initial silence.
    pcm = np.frombuffer(run_ffmpeg(["-i", str(target), "-vn", "-af", "aresample=async=1:first_pts=0",
                                    "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"]), np.float32)
    assert probe_video(target).frames == 90
    assert np.max(np.abs(pcm[:9000])) < 0.005
    assert np.sqrt(np.mean(pcm[20000:26000] ** 2)) > 0.01
    assert np.max(np.abs(pcm[48000:60000])) < 0.005


def test_audio_queue_error_is_not_misreported_as_disk_full():
    diagnostic = "[af#0:1 @ 123] Error sending frames to consumers: No space left on device"
    error = describe_export_error(BrokenPipeError(32, "Broken pipe"), diagnostic)
    assert isinstance(error, ExportError)
    assert "очередь обработки звука" in str(error)
    assert "Освободите место" not in str(error)
    assert error.details == diagnostic


def test_real_disk_error_remains_actionable():
    for original, log in [(OSError(errno.ENOSPC, "No space left on device"), ""),
                          (BrokenPipeError(32, "Broken pipe"), "[out#0/mp4] Error writing trailer: No space left on device")]:
        error = describe_export_error(original, log)
        assert "Освободите место" in str(error)
        assert "очередь" not in str(error)


def test_disk_full_before_encoding_preserves_existing_result(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "color=size=64x36:rate=1:duration=1",
                "-c:v", "libx264", "-threads", "2", str(source)])
    target = tmp_path / "result.mp4"
    target.write_bytes(b"previous complete result")

    def full_disk(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("ascii_studio.engine.tempfile.mkstemp", full_disk)
    job = ExportJob()
    with pytest.raises(ExportError, match="Освободите место"):
        job.run(probe_video(source), target, RenderSettings())
    assert target.read_bytes() == b"previous complete result"
    assert not job._processes
    assert not list(tmp_path.glob("*.part.mp4"))


@pytest.fixture
def audio_source(tmp_path):
    source = tmp_path / "audio-source.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=0.5",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(source)])
    return source


def test_cancel_during_audio_mux_preserves_existing_result(tmp_path, audio_source, monkeypatch):
    target = tmp_path / "existing.mp4"
    target.write_bytes(b"existing complete export")
    job = ExportJob()
    start_process = job._start
    mux_was_started = []

    def start_and_cancel(args, **kwargs):
        process = start_process(args, **kwargs)
        if "-c:v" in args and args[args.index("-c:v") + 1] == "copy":
            mux_was_started.append(True)
            job.cancel()
        return process

    monkeypatch.setattr(job, "_start", start_and_cancel)
    with pytest.raises(ExportCancelled):
        job.run(probe_video(audio_source), target, RenderSettings())
    assert mux_was_started and not job._processes
    assert target.read_bytes() == b"existing complete export"
    assert not list(tmp_path.glob("*.part.mp4"))


def test_audio_mux_failure_preserves_existing_result(tmp_path, audio_source):
    target = tmp_path / "existing.mp4"
    target.write_bytes(b"existing complete export")
    job = ExportJob()
    with pytest.raises(ExportError, match="добавить звук") as error:
        job.run(probe_video(audio_source), target, RenderSettings(),
                phase=lambda _: audio_source.write_bytes(b"source changed during rendering"))
    assert error.value.details
    assert target.read_bytes() == b"existing complete export"
    assert not job._processes and not list(tmp_path.glob("*.part.mp4"))
