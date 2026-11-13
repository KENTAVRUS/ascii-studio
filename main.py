from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

if "--smoke-test-dir" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

def main():
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ascii_studio import __version__
    from ascii_studio.theme import configure_app
    from ascii_studio.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ASCII Studio")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("ASCII Studio")
    configure_app(app)
    if "--smoke-test-dir" in sys.argv:
        from ascii_studio.validation import run_checks
        target_index = sys.argv.index("--smoke-test-dir") + 1
        if target_index >= len(sys.argv):
            return 2
        return run_checks(app, Path(sys.argv[target_index]))
    log_path = Path(tempfile.gettempdir()) / "ascii-studio.log"
    logging.basicConfig(filename=log_path, encoding="utf-8", level=logging.ERROR,
                        format="%(asctime)s %(levelname)s %(message)s")

    def exception_hook(kind, value, traceback):
        logging.error("Unhandled error", exc_info=(kind, value, traceback))
        QMessageBox.critical(None, "ASCII Studio", f"Произошла ошибка: {value}\n\nЖурнал: {log_path}")

    sys.excepthook = exception_hook
    initial_path = next((arg for arg in sys.argv[1:] if not arg.startswith("-")), None)
    window = MainWindow(initial_path)
    window.show()
    if sys.platform == "win32":
        # Use a dark native title bar while retaining normal Windows resizing.
        try:
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(window.winId()), 20, ctypes.byref(value), ctypes.sizeof(value))
        except (OSError, AttributeError):
            pass
    return app.exec()


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        details = traceback.format_exc()
        log_path = Path(tempfile.gettempdir()) / "ascii-studio-startup.log"
        log_path.write_text(details, encoding="utf-8")
        if "--smoke-test-dir" in sys.argv:
            index = sys.argv.index("--smoke-test-dir") + 1
            if index < len(sys.argv):
                output = Path(sys.argv[index])
                output.mkdir(parents=True, exist_ok=True)
                (output / "report.json").write_text(json.dumps({"passed": False, "error": details}, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, f"Не удалось запустить приложение.\nПодробности: {log_path}", "ASCII Studio", 0x10)
        exit_code = 1
    raise SystemExit(exit_code)
