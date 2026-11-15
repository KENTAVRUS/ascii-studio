"""Exercise the Qt components offscreen and save actual widget renders."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ascii_studio.engine import ExportError, NO_WINDOW, ffmpeg_path, probe_video
from ascii_studio.theme import configure_app
from ascii_studio.window import MainWindow


def main():
    app = QApplication([])
    configure_app(app)
    window = MainWindow()
    errors = []
    window._show_error = lambda title, message, details="": errors.append((title, message, details))
    output = ROOT / "test-output"
    output.mkdir(exist_ok=True)

    def wait_for(predicate, timeout=25):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError(f"GUI timeout; errors={errors}")
            time.sleep(0.01)
        app.processEvents()

    def settle():
        wait_for(lambda: not window._debounce.isActive() and not window._preview_busy and not window._preview_pending)

    try:
        window.show()
        wait_for(lambda: window.canvas.image is not None)
        window.grab().save(str(output / "welcome.png"))
        assert not window.export_button.isEnabled()
        assert not window.repeat_button.isEnabled() and not window.repeat_button.isChecked()
        window.demo_button.click()
        wait_for(lambda: window.info is not None and window._ascii_image is not None and not window._loading())
        assert window.export_button.isEnabled()
        assert window.info.has_audio
        original_tone = window.render_settings()
        window.auto_button.click()
        assert not window.open_button.isEnabled()
        wait_for(lambda: not window._analysing())
        settle()
        assert window.auto_undo_button.isVisible() and window._auto_undo == original_tone
        window.auto_undo_button.click()
        settle()
        assert window.render_settings() == original_tone
        window.auto_button.click()
        window.auto_button.click()
        wait_for(lambda: not window._analysing())
        assert window.render_settings() == original_tone
        assert window.open_button.isEnabled()
        assert window.size_combo.findData(2560) >= 0 and window.size_combo.findData(3840) >= 0
        window.size_combo.setCurrentIndex(window.size_combo.findData(3840))
        window.upscale_check.setChecked(True)
        assert "3456 × 2160" in window.export_hint.text()
        window.fps_combo.setCurrentIndex(window.fps_combo.findData(240))
        assert window.interpolate_check.isEnabled()
        window.interpolate_check.setChecked(True)
        assert window.motion_note.isVisible()
        assert "240 fps" in window.export_hint.text()
        window.size_combo.setCurrentIndex(0)
        window.fps_combo.setCurrentIndex(0)
        assert not window.upscale_check.isEnabled()
        assert not window.interpolate_check.isEnabled() and not window.interpolate_check.isChecked()
        window.upscale_check.setChecked(False)
        slider = window.detail_slider
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(0, slider.height() // 2))
        assert slider.value() == 40
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() - 1, slider.height() // 2))
        assert slider.value() == 280
        previous = window._ascii_image.cacheKey()
        window.palette_buttons["matrix"].click()
        window.detail_slider.setValue(230)
        settle()
        assert window.render_settings().palette == "matrix"
        assert window._ascii_image.cacheKey() != previous
        window.original_view.click()
        assert window.canvas.image.cacheKey() == window._original_image.cacheKey()
        window.ascii_view.click()
        window.timeline.setValue(5000)
        settle()
        assert window.position == 3
        window.play_button.click()
        wait_for(lambda: window.position > 3.15)
        window.play_button.click()
        assert not window.playing
        window.repeat_button.click()
        assert window.repeat_button.isChecked()
        with patch("ascii_studio.window.time.monotonic", return_value=100.0):
            window.play_button.click()
            # Delayed timer delivery must wrap correctly even after several cycles.
            for cycles in (1, 4):
                window._play_origin = 100.0 - window.info.duration * cycles - 0.25
                window._tick()
                assert window.playing and window._play_timer.isActive()
                assert abs(window.position - 0.25) < 0.000001
                assert window.timeline.value() == round(0.25 / window.info.duration * 10000)
            window.play_button.click()
            paused_at = window.position
            window._play_origin = 0.0
            window._tick()
            assert not window.playing and window.position == paused_at
            window.play_button.click()
            window.repeat_button.click()
            window._play_origin = 100.0 - window.info.duration - 0.1
            window._tick()
            assert not window.playing and not window._play_timer.isActive()
            assert window.position == window._end_position()
        settle()
        window.reset_button.click()
        settle()
        assert window.detail_slider.value() == 150
        assert window.palette_key == "gold"
        window.timeline.setValue(1200)
        settle()
        window.grab().save(str(output / "studio.png"))
        window.resize(1000, 720)
        app.processEvents()
        window.grab().save(str(output / "compact.png"))
        assert window.export_button.geometry().right() < window.centralWidget().width()
        window.resize(1280, 860)
        window.fps_combo.setCurrentIndex(1)
        window.start_export(output / "gui-export.mp4")
        assert not window.open_button.isEnabled()
        assert not window.settings_body.isEnabled()
        wait_for(lambda: not window._exporting(), timeout=60)
        assert not errors, errors
        assert window._last_export is not None
        assert window.progress_bar.value() == 1000
        result = probe_video(output / "gui-export.mp4")
        assert result.has_audio and result.duration == 6
        window.start_export(output / "gui-cancelled.mp4")
        window.cancel_button.click()
        wait_for(lambda: not window._exporting())
        assert not (output / "gui-cancelled.mp4").exists()
        assert window.export_button.isEnabled()
        with patch("ascii_studio.workers.ExportJob.run", side_effect=ExportError("Недостаточно свободного места.", "encoder diagnostics")):
            window.start_export(output / "gui-error.mp4")
            wait_for(lambda: not window._exporting())
        assert errors == [("Не удалось экспортировать видео", "Недостаточно свободного места.", "encoder diagnostics")]
        assert window.export_button.isEnabled() and window.open_button.isEnabled()
        assert window.progress_label.text() == "Экспорт не завершён"
        errors.clear()
        invalid = output / "invalid.mp4"
        invalid.write_bytes(b"not a video")
        window.load_video(invalid)
        wait_for(lambda: not window._loading())
        assert len(errors) == 1
        assert window.info.path.name == "demo.mp4"
        assert window.open_button.isEnabled()
        overlong = output / "over-five-minutes.mp4"
        subprocess.run([
            ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=size=64x36:rate=1",
            "-frames:v", "301", "-c:v", "libx264", "-threads", "2", str(overlong),
        ], check=True, creationflags=NO_WINDOW, timeout=30)
        window.load_video(overlong)
        wait_for(lambda: not window._loading())
        assert len(errors) == 2 and "5 минут" in errors[-1][1]
        assert window.info.path.name == "demo.mp4"
        assert window.export_button.isEnabled()
        window.grab().save(str(output / "after-export.png"))
        print("PASS: auto tone, undo, cancel analysis, upscale/motion controls, welcome, import, 4K/240 FPS controls, palettes, detail, compare, seek, playback, repeat with delayed timers, pause and repeat-off, reset, compact layout, export with audio, error diagnostics and recovery, cancel, invalid and overlong input")
        window.auto_button.click()
    finally:
        window.close()
        wait_for(lambda: not window.isVisible() and not window._preview_thread.isRunning() and not window._analysing())
        print("PASS: clean thread shutdown")


if __name__ == "__main__":
    main()
