"""Exercise photo/GIF import, format switching and saves through Qt widgets."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

from ascii_studio.engine import probe_media
from ascii_studio.theme import configure_app
from ascii_studio.window import MainWindow


def main():
    app = QApplication([])
    configure_app(app)
    window = MainWindow()
    errors = []
    window._show_error = lambda *args: errors.append(args)
    output = ROOT / "test-output" / "media-gui"
    output.mkdir(parents=True, exist_ok=True)

    def wait_for(predicate, timeout=40):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError(f"Timeout: {errors}")
            time.sleep(0.01)
        app.processEvents()

    def settle():
        wait_for(lambda: not window._debounce.isActive() and not window._preview_busy and not window._preview_pending)

    def load(path):
        window.load_video(path)
        wait_for(lambda: not window._loading() and window.info is not None and window.info.path == path.resolve())
        settle()
        assert not errors, errors

    try:
        window.show()
        picture = output / "portrait.png"
        gray = np.tile(np.linspace(20, 240, 241, dtype=np.uint8), (321, 1))
        Image.fromarray(np.repeat(gray[..., None], 3, axis=2)).save(picture)
        load(picture)
        assert window.info.is_image and window._ascii_image is not None
        assert not window.transport.isVisible() and not window.fps_combo.isVisible()
        assert not window.repeat_button.isVisible() and not window.repeat_button.isEnabled()
        assert not window.audio_check.isVisible() and not window.interpolate_check.isVisible()
        window.toggle_play()
        window.seek_relative(5)
        assert not window.playing and window.position == 0
        window.auto_button.click()
        wait_for(lambda: not window._analysing())
        settle()
        assert window.auto_undo_button.isVisible()
        window.grab().save(str(output / "photo.png"))
        for format in ("png", "jpg", "webp", "bmp", "tiff"):
            window.format_combo.setCurrentIndex(window.format_combo.findData(format))
            assert window.export_button.text() == f"Экспорт {format.upper()}"
            target = output / f"photo-result.{format}"
            if format == "jpg":
                target = output / "photo-result.jpeg"
                with patch("ascii_studio.window.QFileDialog.getSaveFileName", return_value=(str(target), "JPEG (*.jpeg)")):
                    window.choose_export()
            else:
                window.start_export(target)
            wait_for(lambda: not window._exporting())
            assert not errors, errors
            assert probe_media(target).is_image
        animation = output / "animated.gif"
        frames = [Image.new("RGB", (160, 90), color) for color in ("red", "green", "blue")]
        frames[0].save(animation, save_all=True, append_images=frames[1:], duration=[100, 100, 10], loop=0)
        load(animation)
        assert window.transport.isVisible() and window.format_combo.currentData() == "mp4"
        window.timeline.setValue(10000)
        settle()
        assert window.position > 0.2 and window._original_image.pixelColor(0, 0).blue() > 200
        assert window.repeat_button.isEnabled()
        window.repeat_button.click()
        started = time.monotonic()
        window.play_button.click()
        wait_for(lambda: time.monotonic() - started > window.info.duration * 3)
        assert window.playing and window._play_timer.isActive()
        window.repeat_button.click()
        wait_for(lambda: not window.playing)
        settle()
        assert window.position > 0.2 and window._original_image.pixelColor(0, 0).blue() > 200
        window.repeat_button.click()
        window.fps_combo.setCurrentIndex(window.fps_combo.findData(240))
        window.format_combo.setCurrentIndex(window.format_combo.findData("gif"))
        assert window.fps_combo.currentData() == 50 and window.fps_combo.findData(240) == -1
        assert not window.audio_check.isVisible() and window.format_note.isVisible()
        window.grab().save(str(output / "gif.png"))
        for format in ("gif", "mp4", "mov", "mkv", "webm", "avi"):
            window.format_combo.setCurrentIndex(window.format_combo.findData(format))
            target = output / f"animation-result.{format}"
            window.start_export(target)
            wait_for(lambda: not window._exporting())
            assert not errors, errors
            assert target.is_file() and not probe_media(target).is_image
        load(picture)
        assert window.format_combo.currentData() == "png"
        assert window.repeat_button.isChecked() and not window.repeat_button.isEnabled()
        window.resize(1000, 720)
        app.processEvents()
        window.grab().save(str(output / "compact-photo.png"))
        assert window.export_button.geometry().right() < window.centralWidget().width()
        load(animation)
        assert window.repeat_button.isChecked() and window.repeat_button.isEnabled()
        print("PASS: photo/GIF import, EXIF-aware preview, photo auto tone, static controls, last GIF frame, repeated GIF playback and repeat-off, repeat choice across file loads, format switching, all image/video/GIF exports and compact layout")
    finally:
        window.close()
        wait_for(lambda: not window.isVisible() and not window._preview_thread.isRunning())


if __name__ == "__main__":
    main()
