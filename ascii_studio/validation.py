"""Explicit diagnostic mode for verifying a distributed application."""
from __future__ import annotations

import json
import subprocess
import time
import traceback
from pathlib import Path

from . import __version__
from .engine import NO_WINDOW, ffmpeg_path, probe_video
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
        window._show_error = lambda title, message: errors.append([title, message])
        window.show()
        wait_for(lambda: window.canvas.image is not None)
        window.grab().save(str(output / "welcome.png"))
        report["checks"].append("window and fonts")
        window.load_demo()
        wait_for(lambda: window.info is not None and window._ascii_image is not None and not window._loading())
        assert window.info.has_audio
        report["checks"].append("bundled demo and preview")
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
            "-i", "testsrc2=size=160x90:rate=24:duration=0.125", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=0.125", "-c:v", "libx264",
            "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(small_source),
        ], check=True, creationflags=NO_WINDOW, timeout=30)
        window.load_video(small_source)
        wait_for(lambda: not window._loading() and window.info.path == small_source and window._ascii_image is not None)
        assert window.info.frames == 3 and window.info.fps == 24
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
        assert (enhanced.width, enhanced.height, enhanced.fps, enhanced.frames) == (3840, 2160, 240, 30)
        assert enhanced.duration == 0.125 and enhanced.has_audio
        report["enhanced_export_seconds"] = round(time.monotonic() - started, 2)
        report["checks"].append("upscale 160x90 to 4K with 24-to-240 FPS interpolation, duration and audio")
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
        window.load_demo()
        wait_for(lambda: not window._loading() and window.info.path.name == "demo.mp4" and window._ascii_image is not None)
        window.auto_button.click()
        wait_for(lambda: not window._analysing())
        window.fps_combo.setCurrentIndex(window.fps_combo.findData(60))
        window.interpolate_check.setChecked(True)
        window.timeline.setValue(900)
        wait_for(lambda: not window._debounce.isActive() and not window._preview_busy and not window._preview_pending)
        window.progress_panel.hide()
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
