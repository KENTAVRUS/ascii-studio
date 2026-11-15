BG = "#000000"
PANEL = "#0b0b0c"
GOLD = "#dbb86c"
MUTED = "#7f7f84"


def configure_app(app):
    """Use identical native and diagnostic rendering, including Windows fonts."""
    import os
    from pathlib import Path
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "consola.ttf"):
        path = font_dir / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#000000"), (QPalette.ColorRole.WindowText, "#e9e7e1"),
        (QPalette.ColorRole.Base, "#101011"), (QPalette.ColorRole.AlternateBase, "#161617"),
        (QPalette.ColorRole.Text, "#e9e7e1"), (QPalette.ColorRole.Button, "#161617"),
        (QPalette.ColorRole.ButtonText, "#e9e7e1"), (QPalette.ColorRole.Highlight, "#8e7340"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    ]:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)


STYLESHEET = """
QMainWindow, QWidget#root { background: #000000; color: #e9e7e1; }
QWidget { color: #e9e7e1; font-family: 'Segoe UI'; font-size: 13px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 20px; font-weight: 650; letter-spacing: 3px; color: #edeae3; }
QLabel#eyebrow { color: #dbb86c; font-size: 10px; letter-spacing: 2px; font-weight: 600; }
QLabel#title { font-size: 23px; font-weight: 600; }
QLabel#muted { color: #7f7f84; font-size: 12px; }
QLabel#section { color: #89898b; font-size: 10px; letter-spacing: 1.7px; font-weight: 600; }
QLabel#value { color: #dbb86c; font-family: 'Consolas'; font-size: 13px; }
QFrame#line { background: #202022; border: none; max-height: 1px; }
QFrame#panel { background: #0b0b0c; border: 1px solid #242322; border-radius: 16px; }
QWidget#settingsBody { background: #0b0b0c; }
QFrame#previewShell { background: #060606; border: 1px solid #242322; border-radius: 16px; }
QFrame#transport { background: #0b0b0c; border: 1px solid #242322; border-radius: 12px; }
QPushButton { background: #131314; border: 1px solid #2b2b2c; border-radius: 8px;
    padding: 9px 14px; color: #dddcd7; font-weight: 500; }
QPushButton:hover { background: #1d1c19; border-color: #665737; color: #f0d496; }
QPushButton:pressed { background: #2b2519; }
QPushButton:focus { border-color: #aa8d50; }
QPushButton:disabled { color: #4d4d50; background: #0d0d0e; border-color: #1d1d1f; }
QPushButton#primary { background: #dbb86c; color: #17130b; border: 1px solid #dbb86c; font-weight: 650; }
QPushButton#primary:hover { background: #ebcd8c; border-color: #ebcd8c; }
QPushButton#primary:pressed { background: #c3a15b; }
QPushButton#primary:disabled { background: #242017; border-color: #30281a; color: #6e5e3d; }
QPushButton#ghost, QPushButton#repeat { background: transparent; border-color: transparent; color: #9d9b95; padding: 6px 8px; }
QPushButton#ghost:hover, QPushButton#repeat:hover { background: #191713; color: #dbb86c; }
QPushButton#repeat:checked { background: #221d13; border-color: #665332; }
QPushButton#repeat:focus { border-color: #aa8d50; }
QPushButton#repeat:disabled { background: transparent; border-color: transparent; }
QPushButton#segment { background: transparent; border-color: #262627; padding: 6px 12px; color: #818187; }
QPushButton#segment:checked { background: #221d13; color: #e0c080; border-color: #665332; }
QPushButton#palette { text-align: left; padding: 11px 10px; border-color: #29292a; background: #101011; }
QPushButton#palette:checked { border-color: #9d8148; background: #211c12; color: #ead09b; }
QComboBox { border: 1px solid #2c2c2d; border-radius: 7px; background: #131314;
    color: #d1d0ca; padding: 8px 12px; min-height: 18px; }
QComboBox:hover { border-color: #71603c; }
QComboBox:focus { border-color: #aa8d50; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; border: none; }
QComboBox QAbstractItemView { background: #161616; color: #dedad1; border: 1px solid #5a4c31;
    selection-background-color: #302819; selection-color: #f1d49a; padding: 4px; outline: 0; }
QComboBox:disabled { color: #545456; border-color: #202020; }
QSlider::groove:horizontal { background: #29292b; height: 3px; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #b69756; height: 3px; border-radius: 1px; }
QSlider::handle:horizontal { background: #e1c082; width: 12px; height: 12px; margin: -5px 0;
    border: 2px solid #15120d; border-radius: 7px; }
QSlider::handle:horizontal:hover { background: #ffe4b0; }
QSlider::sub-page:horizontal:disabled { background: #524732; }
QCheckBox { color: #aaa9a4; spacing: 8px; font-size: 12px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #454138; border-radius: 4px; background: #111112; }
QCheckBox::indicator:checked { background: #dbb86c; border: 4px solid #514227; }
QCheckBox::indicator:hover { border-color: #bfa36b; }
QProgressBar { border: none; background: #25231e; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: #dbb86c; border-radius: 2px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 5px; margin: 8px 0; }
QScrollBar::handle:vertical { background: #39342a; border-radius: 2px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #211c13; color: #ead3a0; border: 1px solid #6a5835; padding: 6px; }
QMessageBox, QColorDialog { background: #0d0d0e; }
"""
