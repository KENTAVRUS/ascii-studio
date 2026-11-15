"""Explicit diagnostic mode for verifying a distributed application."""
from __future__ import annotations

import json
import subprocess
import time
import traceback
from pathlib import Path

from PIL import Image

from . import __version__
from .engine import NO_WINDOW, audio_export_filter, ffmpeg_path, probe_media, probe_video
from .media import IMAGE_FORMATS, VIDEO_FORMATS
from .demo import demo_frame
from .window import MainWindow


def run_checks(app, output: Path) -> int:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "version": __version__, "checks": []}
    window = None
    errors = []

    def wait_for(predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError(f"Diagnostic timeout. Errors: {errors}")
            time.sleep(0.01)
        app.processEvents()

    try:
        window = MainWindow()
        window._show_error = lambda title, message, details="": errors.append([title, message, details])
        window.show()
        wait_for(lambda: window.canvas.image is not None)
        window.grab().save(str(output / "welcome.png"))
        report["checks"].append("window and fonts")
        window.load_demo()
        wait_for(lambda: window.info is not None and window._ascii_image is not None and not window._loading())
        assert window.info.has_audio
        report["checks"].append("bundled demo and preview")
        assert not window.repeat_button.isChecked() and window.repeat_button.isEnabled()
        window.repeat_button.click()
        window.timeline.setValue(9800)
        window.play_button.click()
        wait_for(lambda: window.position < 1.0)
        assert window.playing and window._play_timer.isActive()
        window.play_button.click()
        paused_at = window.position
        window._tick()
        assert not window.playing and window.position == paused_at
        window.repeat_button.click()
        window.timeline.setValue(9800)
        window.play_button.click()
        wait_for(lambda: not window.playing)
        assert window.position == window._end_position()
        report["checks"].append("video repeat across the end, manual pause and repeat-off")
        old_image = window._ascii_image.cacheKey()
        window.palette_buttons["matrix"].click()
        window.detail_slider.setValue(230)
        wait_for(lambda: not window._debounce.isActive() and not window._preview_busy and not window._preview_pending)
        assert old_image != window._ascii_image.cacheKey()
        window.original_view.click()
        assert window.canvas.image.cacheKey() == window._original_image.cacheKey()
        window.ascii_view.click()
        window.reset_button.click()
        original_settings = window.render_settings()
        window.auto_button.click()
        wait_for(lambda: not window._analysing())
        assert window.auto_undo_button.isVisible() and window._auto_undo == original_settings
        assert not errors, errors
        window.auto_undo_button.click()
        assert window.render_settings() == original_settings
        report["checks"].append("auto contrast, brightness and contours with undo")
        window.timeline.setValue(900)
        wait_for(lambda: not window._debounce.isActive() and not window._preview_busy and not window._preview_pending)
        window.grab().save(str(output / "studio.png"))
        report["checks"].append("detail, palette, compare, seek and reset")
        destination = output / "demo-ascii.mp4"
        started = time.monotonic()
        window.start_export(destination)
        wait_for(lambda: not window._exporting(), timeout=180)
        assert not errors, errors
        assert window._last_export == destination.resolve()
        result = probe_video(destination)
        assert result.has_audio and result.frames == 144
        assert result.duration == 6 and (result.width, result.height) == (960, 600)
        report["export_seconds"] = round(time.monotonic() - started, 2)
        report["checks"].append("bundled FFmpeg, H.264 MP4 and audio export")
        assert window.progress_bar.value() == 1000
        source_4k = output / "4k-240-source.mp4"
        subprocess.run([
            ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-f", "lavfi",
            "-i", "testsrc2=size=3840x2160:rate=240", "-frames:v", "8",
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", str(source_4k),
        ], check=True, creationflags=NO_WINDOW, timeout=60)
        window.load_video(source_4k)
        wait_for(lambda: not window._loading() and window.info.path == source_4k and window._ascii_image is not None)
        window.size_combo.setCurrentIndex(window.size_combo.findData(3840))
        window.fps_combo.setCurrentIndex(window.fps_combo.findData(240))
        assert "3840 × 2160" in window.export_hint.text() and "240 fps" in window.export_hint.text()
        target_4k = output / "4k-240-ascii.mp4"
        started = time.monotonic()
        window.start_export(target_4k)
        wait_for(lambda: not window._exporting(), timeout=180)
        assert not errors, errors
        uhd = probe_video(target_4k)
        assert (uhd.width, uhd.height, uhd.fps, uhd.frames) == (3840, 2160, 240, 8)
        report["4k_240_export_seconds"] = round(time.monotonic() - started, 2)
        report["checks"].append("4K UHD / 240 FPS export from GUI")
        small_source = output / "small-24-source.mp4"
        subprocess.run([
            ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-f", "lavfi",
            "-i", "testsrc2=size=160x90:rate=24:duration=1", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=0.125", "-c:v", "libx264",
            "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(small_source),
        ], check=True, creationflags=NO_WINDOW, timeout=30)
        window.load_video(small_source)
        wait_for(lambda: not window._loading() and window.info.path == small_source and window._ascii_image is not None)
        assert window.info.frames == 24 and window.info.fps == 24
        window.upscale_check.setChecked(True)
        window.interpolate_check.setChecked(True)
        assert window.interpolate_check.isEnabled() and window.motion_note.isVisible()
        assert "3840 × 2160" in window.export_hint.text() and "240 fps" in window.export_hint.text()
        enhanced_target = output / "upscaled-interpolated-4k-240.mp4"
        started = time.monotonic()
        window.start_export(enhanced_target)
        wait_for(lambda: not window._exporting(), timeout=180)
        assert not errors, errors
        enhanced = probe_video(enhanced_target)
        assert (enhanced.width, enhanced.height, enhanced.fps, enhanced.frames) == (3840, 2160, 240, 240)
        assert enhanced.duration == 1 and enhanced.has_audio
        report["enhanced_export_seconds"] = round(time.monotonic() - started, 2)
        report["checks"].append("upscale 160x90 to 4K with 24-to-240 FPS interpolation, duration and audio")
        audio_regression = subprocess.run([
            ffmpeg_path(), "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "color=s=16x16:r=1:d=2[out0];anullsrc=r=8000:cl=mono:n=1:d=45[out1]",
            "-map", "0:v", "-map", "0:a", "-c:v", "mpeg4", "-c:a", "aac",
            "-af", audio_export_filter(300), "-shortest", "-shortest_buf_duration", "0.1", "-f", "null", "-",
        ], capture_output=True, creationflags=NO_WINDOW, timeout=30)
        assert audio_regression.returncode == 0, audio_regression.stderr.decode(errors="replace")
        report["checks"].append("small audio frames do not overflow the FFmpeg sync queue (ENOSPC regression)")
        overlong = output / "over-five-minutes.mp4"
        subprocess.run([
            ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=size=64x36:rate=1",
            "-frames:v", "301", "-c:v", "libx264", "-threads", "2", str(overlong),
        ], check=True, creationflags=NO_WINDOW, timeout=30)
        window.load_video(overlong)
        wait_for(lambda: not window._loading())
        assert len(errors) == 1 and "5 минут" in errors[-1][1]
        assert window.info.path == small_source and window.export_button.isEnabled()
        report["checks"].append("five-minute import limit and recovery")
        errors.clear()
        photo = output / "sample-photo.png"
        Image.fromarray(demo_frame(0.85)).save(photo)
        window.load_video(photo)
        wait_for(lambda: not window._loading() and window.info.path == photo and window._ascii_image is not None)
        assert window.info.is_image and not window.transport.isVisible() and not window.fps_combo.isVisible()
        assert not window.repeat_button.isEnabled()
        window.size_combo.setCurrentIndex(0)
        window.upscale_check.setChecked(False)
        window.auto_button.click()
        wait_for(lambda: not window._analysing())
        wait_for(lambda: not window._debounce.isActive() and not window._preview_busy and not window._preview_pending)
        window.grab().save(str(output / "photo.png"))
        for format in IMAGE_FORMATS:
            window.format_combo.setCurrentIndex(window.format_combo.findData(format))
            target = output / f"photo-ascii.{format}"
            window.start_export(target)
            wait_for(lambda: not window._exporting())
            assert not errors, errors
            with Image.open(target) as image:
                assert image.format == IMAGE_FORMATS[format].upper() and image.size == (960, 600)
        report["checks"].append("photo import, static controls, auto tone, PNG/JPEG/WebP/BMP/TIFF exports")
        animated_source = output / "sample-animation.gif"
        images = [Image.fromarray(demo_frame(t)).resize((320, 200)) for t in (0.1, 0.4, 0.9)]
        images[0].save(animated_source, save_all=True, append_images=images[1:], duration=[100, 200, 100], loop=0)
        window.load_video(animated_source)
        wait_for(lambda: not window._loading() and window.info.path == animated_source and window._ascii_image is not None)
        assert window.info.kind == "gif" and window.transport.isVisible()
        window.repeat_button.click()
        window.play_button.click()
        started = time.monotonic()
        wait_for(lambda: time.monotonic() - started > window.info.duration * 2)
        assert window.playing
        window.repeat_button.click()
        wait_for(lambda: not window.playing)
        report["checks"].append("repeated GIF preview and stop at the end when repeat is disabled")
        window.fps_combo.setCurrentIndex(window.fps_combo.findData(30))
        window.start_export(output / "gif-ascii.mp4")
        wait_for(lambda: not window._exporting())
        assert not errors, errors
        assert probe_media(output / "gif-ascii.mp4").duration == 0.4
        report["checks"].append("GIF input, variable frame delays, preview and GIF-to-video export")
        window.load_video(small_source)
        wait_for(lambda: not window._loading() and window.info.path == small_source and window._ascii_image is not None)
        for format in VIDEO_FORMATS:
            window.format_combo.setCurrentIndex(window.format_combo.findData(format))
            target = output / f"video-ascii.{format}"
            window.start_export(target)
            wait_for(lambda: not window._exporting())
            assert not errors, errors
            result = probe_media(target)
            assert (result.width, result.height) == (160, 90)
            assert result.has_audio == (format != "gif")
        report["checks"].append("MP4/MOV/MKV/WebM/AVI exports with audio and animated GIF export")
        window.load_demo()
        wait_for(lambda: not window._loading() and window.info.path.name == "demo.mp4" and window._ascii_image is not None)
        window.format_combo.setCurrentIndex(window.format_combo.findData("mp4"))
        window.auto_button.click()
        wait_for(lambda: not window._analysing())
        window.fps_combo.setCurrentIndex(window.fps_combo.findData(60))
        window.interpolate_check.setChecked(True)
        window.timeline.setValue(900)
        wait_for(lambda: not window._debounce.isActive() and not window._preview_busy and not window._preview_pending)
        window.progress_panel.hide()
        window.repeat_button.setChecked(True)
        window.status.setText("Пробел — воспроизведение  ·  ← → — перемотка на 5 секунд")
        app.processEvents()
        app.processEvents()
        window.grab().save(str(output / "studio.png"))
        window.close()
        wait_for(lambda: not window.isVisible() and not window._preview_thread.isRunning())
        report["checks"].append("clean shutdown")
        report["passed"] = True
    except BaseException:
        report["error"] = traceback.format_exc()
    finally:
        if window is not None and window.isVisible():
            window._closing = True
            window.close()
            wait_for(lambda: not window.isVisible() and not window._preview_thread.isRunning() and not window._analysing())
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["passed"] else 1
