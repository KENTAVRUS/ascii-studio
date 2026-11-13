from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .demo import demo_frame
from . import __version__
from .engine import (AsciiRenderer, CHARSETS, ExportOptions, FPS_PRESETS,
                     RESOLUTION_PRESETS, RenderSettings, export_fps, export_size, fit_size)
from .theme import GOLD, STYLESHEET
from .widgets import PreviewCanvas, SeekSlider, StudioComboBox, button, icon, label
from .workers import AutoTuneWorker, ExportWorker, PreviewWorker, ProbeWorker, image_from_array


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "assets" / name


def time_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def line():
    widget = QFrame()
    widget.setObjectName("line")
    widget.setFixedHeight(1)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, initial_path=None):
        super().__init__()
        self.setWindowTitle("ASCII Studio")
        self.setWindowIcon(icon("mark", GOLD, 64))
        self.resize(1280, 860)
        self.setMinimumSize(1000, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLESHEET)
        self.info = None
        self.position = 0.0
        self.playing = False
        self.palette_key = "gold"
        self.custom_color = "#dbb86c"
        self.background_color = "#000000"
        self._building = True
        self._closing = False
        self._probe_thread = None
        self._export_thread = None
        self._auto_thread = None
        self._auto_undo = None
        self._last_export = None
        self._original_image = None
        self._ascii_image = None
        self._preview_busy = False
        self._preview_pending = False
        self._request_id = 0
        self._revision = 0
        self._issued_revision = -1
        self._preview_errors = False
        self._hero_source = demo_frame(0.85)
        self._hero_renderer = AsciiRenderer()
        self._build_ui()
        self._building = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(65)
        self._debounce.timeout.connect(self._request_preview)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(33)
        self._play_timer.timeout.connect(self._tick)
        self._preview_thread = PreviewWorker(self)
        self._preview_thread.rendered.connect(self._preview_ready)
        self._preview_thread.failed.connect(self._preview_failed)
        self._preview_thread.start()
        self._preview_thread.finished.connect(self._finish_close)
        self._shortcuts = []
        for key, callback in [("Ctrl+O", self.choose_file), ("Ctrl+E", self.choose_export),
                              ("Space", self.toggle_play), ("Left", lambda: self.seek_relative(-5)),
                              ("Right", lambda: self.seek_relative(5))]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        self._settings_changed()
        self._set_controls()
        if initial_path:
            QTimer.singleShot(100, lambda: self.load_video(initial_path))

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 20, 28, 15)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(15)
        mark = label()
        mark.setPixmap(icon("mark", GOLD, 42).pixmap(42, 42))
        header.addWidget(mark)
        brand = QVBoxLayout()
        brand.setSpacing(1)
        brand.addWidget(label("ASCII STUDIO", "brand"))
        brand.addWidget(label("ИСКУССТВО КАЖДОГО СИМВОЛА", "eyebrow"))
        header.addLayout(brand)
        header.addStretch()
        self.local_label = label("●  Локальная обработка", "muted")
        header.addWidget(self.local_label)
        header.addSpacing(16)
        self.open_button = button("Открыть видео", icon_name="folder")
        self.open_button.setToolTip("Выбрать видео · Ctrl+O")
        self.open_button.clicked.connect(self.choose_file)
        header.addWidget(self.open_button)
        self.export_button = button("Экспорт MP4", "primary", "export")
        self.export_button.setToolTip("Сохранить ASCII-видео · Ctrl+E")
        self.export_button.clicked.connect(self.choose_export)
        header.addWidget(self.export_button)
        layout.addLayout(header)
        layout.addWidget(line())

        content = QHBoxLayout()
        content.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(14)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(6)
        titles.addWidget(label("01  /  ХОЛСТ", "eyebrow"))
        self.file_title = label("Из видео — в ASCII.", "title")
        self.file_title.setTextFormat(Qt.TextFormat.PlainText)
        self.file_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        titles.addWidget(self.file_title)
        self.file_meta = label("Ваше видео. Ваш характер изображения.", "muted")
        titles.addWidget(self.file_meta)
        heading.addLayout(titles, 1)
        self.demo_button = button("Попробовать демо", "ghost")
        self.demo_button.clicked.connect(self.load_demo)
        heading.addWidget(self.demo_button, 0, Qt.AlignmentFlag.AlignBottom)
        left.addLayout(heading)

        shell = QFrame()
        shell.setObjectName("previewShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(1, 10, 1, 10)
        shell_layout.setSpacing(7)
        preview_header = QHBoxLayout()
        preview_header.setContentsMargins(15, 0, 15, 0)
        self.canvas_label = label("ПРЕДПРОСМОТР", "section")
        preview_header.addWidget(self.canvas_label)
        preview_header.addStretch()
        self.view_group = QButtonGroup(self)
        self.ascii_view = button("ASCII", "segment")
        self.original_view = button("Оригинал", "segment")
        for i, b in enumerate((self.ascii_view, self.original_view)):
            b.setCheckable(True)
            self.view_group.addButton(b, i)
            preview_header.addWidget(b)
        self.ascii_view.setChecked(True)
        self.view_group.idClicked.connect(self._view_changed)
        shell_layout.addLayout(preview_header)
        self.canvas = PreviewCanvas()
        self.canvas.clicked.connect(self._canvas_clicked)
        shell_layout.addWidget(self.canvas, 1)
        canvas_footer = QHBoxLayout()
        canvas_footer.setContentsMargins(17, 0, 17, 0)
        self.grid_label = label("150 КОЛОНОК", "value")
        canvas_footer.addWidget(self.grid_label)
        canvas_footer.addStretch()
        self.preview_note = label("Пример ASCII-графики", "muted")
        canvas_footer.addWidget(self.preview_note)
        shell_layout.addLayout(canvas_footer)
        left.addWidget(shell, 1)

        transport = QFrame()
        transport.setObjectName("transport")
        tl = QHBoxLayout(transport)
        tl.setContentsMargins(10, 8, 15, 8)
        tl.setSpacing(13)
        self.play_button = button("", "ghost", "play")
        self.play_button.setFixedSize(37, 34)
        self.play_button.setAccessibleName("Воспроизвести или приостановить")
        self.play_button.setToolTip("Воспроизведение / пауза · Пробел")
        self.play_button.clicked.connect(self.toggle_play)
        tl.addWidget(self.play_button)
        self.current_time = label("00:00", "value")
        tl.addWidget(self.current_time)
        self.timeline = SeekSlider(Qt.Orientation.Horizontal)
        self.timeline.setAccessibleName("Позиция видео")
        self.timeline.setRange(0, 10000)
        self.timeline.valueChanged.connect(self._seek)
        self.timeline.sliderPressed.connect(self.pause)
        tl.addWidget(self.timeline, 1)
        self.total_time = label("00:00", "muted")
        tl.addWidget(self.total_time)
        left.addWidget(transport)
        self.status = label("MP4, MOV, MKV, AVI, WebM  ·  До 5 минут  ·  Перетащите файл в окно", "muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(30)
        left.addWidget(self.status)
        content.addLayout(left, 1)

        self.panel = QFrame()
        self.panel.setObjectName("panel")
        self.panel.setFixedWidth(340)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 16, 0, 16)
        panel_layout.setSpacing(14)
        panel_heading = QHBoxLayout()
        panel_heading.setContentsMargins(20, 0, 16, 0)
        panel_heading.addWidget(label("02  /  СТИЛЬ", "eyebrow"))
        panel_heading.addStretch()
        self.auto_button = button("Авто", "segment")
        self.auto_button.setToolTip("Подобрать контраст, яркость и контуры по нескольким кадрам всего ролика")
        self.auto_button.clicked.connect(self.auto_tune)
        panel_heading.addWidget(self.auto_button)
        self.reset_button = button("Сбросить", "ghost")
        self.reset_button.clicked.connect(self.reset_settings)
        panel_heading.addWidget(self.reset_button)
        panel_layout.addLayout(panel_heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_body = QWidget()
        self.settings_body.setObjectName("settingsBody")
        settings_layout = QVBoxLayout(self.settings_body)
        settings_layout.setContentsMargins(20, 0, 20, 0)
        settings_layout.setSpacing(12)
        self.auto_undo_button = button("Вернуть настройки до «Авто»", "ghost")
        self.auto_undo_button.clicked.connect(self.undo_auto)
        self.auto_undo_button.hide()
        settings_layout.addWidget(self.auto_undo_button)

        self.detail_slider = self._slider(settings_layout, "ДЕТАЛИЗАЦИЯ", 40, 280, 150, lambda v: str(v), "Число колонок ASCII")
        captions = QHBoxLayout()
        captions.addWidget(label("Крупные символы", "muted"))
        captions.addStretch()
        captions.addWidget(label("Тонкие детали", "muted"))
        settings_layout.addLayout(captions)
        presets = QHBoxLayout()
        presets.setSpacing(7)
        self.detail_presets = QButtonGroup(self)
        for text, value in [("Крупно", 70), ("Баланс", 150), ("Детально", 230)]:
            b = button(text, "segment")
            b.setCheckable(True)
            b.clicked.connect(lambda checked=False, v=value: self.detail_slider.setValue(v))
            self.detail_presets.addButton(b, value)
            presets.addWidget(b)
        settings_layout.addLayout(presets)
        self.charset_combo = StudioComboBox()
        self.charset_combo.setAccessibleName("Набор символов")
        for text, key in [("Классический", "classic"), ("Расширенный", "detailed"),
                          ("Минимализм", "minimal"), ("Двоичный · 01", "binary")]:
            self.charset_combo.addItem(text, key)
        self.charset_combo.currentIndexChanged.connect(self._settings_changed)
        settings_layout.addWidget(self.charset_combo)
        self.characters = label(". : - = + * # % @", "value")
        self.characters.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.characters.setFixedHeight(16)
        settings_layout.addWidget(self.characters)
        settings_layout.addWidget(line())
        settings_layout.addWidget(label("ПАЛИТРА", "section"))
        colors = QGridLayout()
        colors.setSpacing(8)
        self.palette_group = QButtonGroup(self)
        self.palette_buttons = {}
        for i, (text, key, color) in enumerate([
            ("Золото", "gold", "#dbb86c"), ("Белый", "white", "#e7e8e2"),
            ("Матрица", "matrix", "#4fef8b"), ("Исходные цвета", "original", "#a9b8d3"),
        ]):
            b = button("●  " + text, "palette")
            b.setCheckable(True)
            b.setStyleSheet(f"QPushButton {{ color: {color}; }}")
            b.clicked.connect(lambda checked=False, k=key: self._select_palette(k))
            self.palette_group.addButton(b)
            self.palette_buttons[key] = b
            colors.addWidget(b, i // 2, i % 2)
        self.palette_buttons["gold"].setChecked(True)
        settings_layout.addLayout(colors)
        custom_row = QHBoxLayout()
        self.custom_button = button("+  Свой цвет", "segment")
        self.custom_button.setCheckable(True)
        self.palette_group.addButton(self.custom_button)
        self.custom_button.clicked.connect(self.choose_custom_color)
        custom_row.addWidget(self.custom_button, 1)
        self.bg_button = button("Фон  ●", "segment")
        self.bg_button.setToolTip("Выбрать цвет фона")
        self.bg_button.clicked.connect(self.choose_background)
        custom_row.addWidget(self.bg_button)
        settings_layout.addLayout(custom_row)
        settings_layout.addWidget(line())
        self.contrast_slider = self._slider(settings_layout, "КОНТРАСТ", 50, 220, 120, lambda v: f"{v}%", "Контраст")
        self.brightness_slider = self._slider(settings_layout, "ЯРКОСТЬ", -40, 40, 4, lambda v: f"{v:+d}", "Яркость")
        self.edges_slider = self._slider(settings_layout, "КОНТУРЫ", 0, 100, 25, lambda v: f"{v}%", "Выразительность контуров")
        self.invert_check = QCheckBox("Инверсия яркости")
        self.invert_check.toggled.connect(self._settings_changed)
        settings_layout.addWidget(self.invert_check)
        settings_layout.addStretch()
        scroll.setWidget(self.settings_body)
        panel_layout.addWidget(scroll, 1)

        self.export_settings = QWidget()
        el = QVBoxLayout(self.export_settings)
        el.setContentsMargins(20, 0, 20, 0)
        el.setSpacing(11)
        el.addWidget(line())
        el.addWidget(label("03  /  ЭКСПОРТ", "eyebrow"))
        export_row = QHBoxLayout()
        self.size_combo = StudioComboBox()
        self.size_combo.setAccessibleName("Размер экспортируемого видео")
        for text, value in RESOLUTION_PRESETS:
            self.size_combo.addItem(text, value)
        self.size_combo.setToolTip("720p, 1080p, QHD или 4K UHD (3840 × 2160). Для увеличения исходника включите флажок ниже. Пропорции сохраняются.")
        export_row.addWidget(self.size_combo, 1)
        self.fps_combo = StudioComboBox()
        self.fps_combo.setAccessibleName("Частота кадров экспорта")
        self.fps_combo.addItem("FPS авто", 0)
        for value in FPS_PRESETS:
            self.fps_combo.addItem(f"{value} fps", value)
        self.fps_combo.setToolTip("Авто сохраняет частоту исходника до 240 fps. Для новых промежуточных кадров включите интерполяцию; иначе кадры повторяются.")
        export_row.addWidget(self.fps_combo)
        el.addLayout(export_row)
        self.export_hint = label("До 4K  ·  240 fps  ·  5 минут", "muted")
        el.addWidget(self.export_hint)
        self.size_combo.currentIndexChanged.connect(self._update_export_hint)
        self.fps_combo.currentIndexChanged.connect(self._update_export_hint)
        self.upscale_check = QCheckBox("Увеличивать до выбранного размера")
        self.upscale_check.setToolTip("Символы рисуются сразу в выбранном разрешении. Например, HD можно сохранить в 4K. В режиме «Авто» увеличение отключено.")
        self.upscale_check.toggled.connect(self._update_export_hint)
        el.addWidget(self.upscale_check)
        self.interpolate_check = QCheckBox("Интерполяция при экспорте")
        self.interpolate_check.setToolTip("Создаёт промежуточные кадры при FPS выше исходного. Экспорт медленнее; на сложном движении возможны искажения. Предпросмотр показывает исходное движение.")
        self.interpolate_check.toggled.connect(self._update_export_hint)
        el.addWidget(self.interpolate_check)
        self.motion_note = label("Плавность — в готовом видео", "muted")
        self.motion_note.hide()
        el.addWidget(self.motion_note)
        self.audio_check = QCheckBox("Сохранить исходный звук")
        self.audio_check.setChecked(True)
        el.addWidget(self.audio_check)
        panel_layout.addWidget(self.export_settings)
        content.addWidget(self.panel)
        layout.addLayout(content, 1)

        self.progress_panel = QWidget()
        pl = QVBoxLayout(self.progress_panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        pr = QHBoxLayout()
        self.progress_label = label("Подготовка экспорта…", "value")
        pr.addWidget(self.progress_label)
        pr.addStretch()
        self.cancel_button = button("Отменить", "ghost")
        self.cancel_button.clicked.connect(self.cancel_export)
        pr.addWidget(self.cancel_button)
        self.result_button = button("Открыть видео", "ghost")
        self.result_button.clicked.connect(self.open_result)
        pr.addWidget(self.result_button)
        self.folder_button = button("Открыть папку", "ghost", "folder")
        self.folder_button.clicked.connect(self.open_result_folder)
        pr.addWidget(self.folder_button)
        pl.addLayout(pr)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 1000)
        pl.addWidget(self.progress_bar)
        self.progress_panel.hide()
        layout.addWidget(self.progress_panel)
        footer = QHBoxLayout()
        footer.addWidget(label("Всё остаётся на вашем компьютере", "muted"))
        footer.addStretch()
        footer.addWidget(label(f"ASCII STUDIO  /  {__version__}", "section"))
        layout.addLayout(footer)

    def _slider(self, layout, title, minimum, maximum, value, formatter, accessible_name):
        group = QVBoxLayout()
        group.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(label(title, "section"))
        heading.addStretch()
        value_label = label(formatter(value), "value")
        heading.addWidget(value_label)
        group.addLayout(heading)
        slider = SeekSlider(Qt.Orientation.Horizontal)
        slider.setAccessibleName(accessible_name)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.setMinimumHeight(20)
        slider.valueChanged.connect(lambda v: value_label.setText(formatter(v)))
        slider.valueChanged.connect(self._settings_changed)
        group.addWidget(slider)
        layout.addLayout(group)
        return slider

    def render_settings(self):
        return RenderSettings(
            columns=self.detail_slider.value(), palette=self.palette_key,
            custom_color=self.custom_color, background=self.background_color,
            charset=self.charset_combo.currentData(), contrast=self.contrast_slider.value() / 100,
            brightness=self.brightness_slider.value() / 100, edges=self.edges_slider.value() / 100,
            invert=self.invert_check.isChecked(),
        )

    def _settings_changed(self, *args):
        if self._building or self._closing:
            return
        self._revision += 1
        settings = self.render_settings()
        self.grid_label.setText(f"{settings.columns} КОЛОНОК")
        glyphs = CHARSETS[settings.charset].strip()
        self.characters.setText(" ".join(glyphs) if len(glyphs) < 14 else "a b c x y z 0 1 # % & @")
        self.detail_presets.setExclusive(False)
        for b in self.detail_presets.buttons():
            b.setChecked(self.detail_presets.id(b) == settings.columns)
        self.detail_presets.setExclusive(True)
        self._debounce.start()

    def _select_palette(self, key):
        self.palette_key = key
        self._settings_changed()

    def choose_custom_color(self):
        color = QColorDialog.getColor(QColor(self.custom_color), self, "Цвет символов")
        if color.isValid():
            self.custom_color = color.name()
            self.palette_key = "custom"
            self.custom_button.setText("●  " + self.custom_color.upper())
            self.custom_button.setStyleSheet(f"color: {self.custom_color};")
            self._settings_changed()
        if self.palette_key != "custom":
            self.palette_buttons[self.palette_key].setChecked(True)

    def choose_background(self):
        color = QColorDialog.getColor(QColor(self.background_color), self, "Цвет фона")
        if color.isValid():
            self.background_color = color.name()
            self.bg_button.setText("Фон  ●")
            self.bg_button.setToolTip("Фон: " + self.background_color.upper())
            self._settings_changed()

    def reset_settings(self):
        self._auto_undo = None
        self.auto_undo_button.hide()
        self._building = True
        self.detail_slider.setValue(150)
        self.contrast_slider.setValue(120)
        self.brightness_slider.setValue(4)
        self.edges_slider.setValue(25)
        self.charset_combo.setCurrentIndex(0)
        self.invert_check.setChecked(False)
        self.palette_key = "gold"
        self.background_color = "#000000"
        self.custom_color = "#dbb86c"
        self.custom_button.setText("+  Свой цвет")
        self.custom_button.setStyleSheet("")
        self.bg_button.setToolTip("Выбрать цвет фона")
        self.palette_buttons["gold"].setChecked(True)
        self._building = False
        self._settings_changed()

    def _exporting(self):
        return self._export_thread is not None and self._export_thread.isRunning()

    def _analysing(self):
        return self._auto_thread is not None and self._auto_thread.isRunning()

    def auto_tune(self):
        if self._analysing():
            self._auto_thread.cancel_event.set()
            self.auto_button.setEnabled(False)
            self.status.setText("Отменяем автонастройку…")
            return
        if not self.info or self._loading() or self._exporting() or self._closing:
            return
        self.pause()
        self._auto_snapshot = self.render_settings()
        worker = AutoTuneWorker(self.info, self._auto_snapshot, self)
        self._auto_thread = worker
        worker.progress.connect(lambda done, total: self.status.setText(f"Автонастройка: анализ кадров {done} / {total}…"))
        worker.completed.connect(self._auto_completed)
        worker.failed.connect(lambda message: self.status.setText(message))
        worker.cancelled.connect(lambda: self.status.setText("Автонастройка отменена. Прежние настройки сохранены."))
        worker.finished.connect(self._auto_finished)
        self.auto_button.setText("Отмена")
        self.status.setText("Подбираем изображение по всему ролику…")
        worker.start()
        self._set_controls()

    def _apply_tone(self, settings):
        self._building = True
        self.contrast_slider.setValue(round(settings.contrast * 100))
        self.brightness_slider.setValue(round(settings.brightness * 100))
        self.edges_slider.setValue(round(settings.edges * 100))
        self._building = False
        self._settings_changed()

    def _auto_completed(self, result):
        if self._closing:
            return
        if self._auto_thread.cancel_event.is_set():
            self.status.setText("Автонастройка отменена. Прежние настройки сохранены.")
            return
        self._auto_undo = self._auto_snapshot
        self._apply_tone(result.settings)
        self.auto_undo_button.show()
        self.status.setText(f"Авто по {result.frames_used} кадрам: контраст {result.settings.contrast:.0%}, яркость {result.settings.brightness * 100:+.0f}, контуры {result.settings.edges:.0%}. Можно настроить вручную.")

    def _auto_finished(self):
        worker = self._auto_thread
        self._auto_thread = None
        if worker:
            worker.deleteLater()
        self.auto_button.setText("Авто")
        self._set_controls()
        self._finish_close()

    def undo_auto(self):
        if self._auto_undo is not None and not self._analysing():
            self._apply_tone(self._auto_undo)
            self._auto_undo = None
            self.auto_undo_button.hide()
            self.status.setText("Возвращены настройки до «Авто».")

    def _update_export_hint(self, *args):
        if self._building:
            return
        self.upscale_check.setEnabled(bool(self.size_combo.currentData()))
        motion_possible = self.info is not None and min(self.info.width, self.info.height) >= 32 and export_fps(self.info.fps, self.fps_combo.currentData()) > self.info.fps + 0.01
        self.interpolate_check.setEnabled(motion_possible)
        if not motion_possible:
            self.interpolate_check.setChecked(False)
        self.motion_note.setVisible(motion_possible and self.interpolate_check.isChecked())
        if self.info:
            width, height = export_size(self.info.width, self.info.height, self.size_combo.currentData(), self.upscale_check.isChecked())
            fps = export_fps(self.info.fps, self.fps_combo.currentData())
            self.export_hint.setText(f"{width} × {height}  /  {fps:g} fps  /  до 5 мин")

    def _loading(self):
        return self._probe_thread is not None and self._probe_thread.isRunning()

    def _set_controls(self):
        busy = self._exporting() or self._loading() or self._analysing() or self._closing
        ready = self.info is not None and not busy
        self.open_button.setEnabled(not busy)
        self.demo_button.setEnabled(not busy)
        self.export_button.setEnabled(ready)
        self.play_button.setEnabled(ready)
        self.timeline.setEnabled(ready)
        self.original_view.setEnabled(self.info is not None)
        self.settings_body.setEnabled(not busy)
        self.reset_button.setEnabled(not busy)
        self.auto_button.setEnabled(not self._closing and (ready or self._analysing()))
        self.export_settings.setEnabled(not busy)
        self.audio_check.setEnabled(not busy and self.info is not None and self.info.has_audio)
        self._update_export_hint()

    def choose_file(self):
        if self._exporting() or self._loading() or self._analysing() or self._closing:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть видео", str(self.info.path.parent) if self.info else "",
            "Видео (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.mpeg *.mpg *.ts);;Все файлы (*)",
        )
        if path:
            self.load_video(path)

    def load_demo(self):
        path = resource_path("demo.mp4")
        if path.is_file():
            self.load_video(path)
        else:
            self._show_error("Демо не найдено", "Откройте собственный видеофайл.")

    def load_video(self, path):
        if self._exporting() or self._loading() or self._analysing() or self._closing:
            return
        self.pause()
        self._revision += 1
        self.status.setText("Читаем видео…")
        self.progress_panel.hide()
        worker = ProbeWorker(path, self)
        self._probe_thread = worker
        worker.loaded.connect(self._video_loaded)
        worker.failed.connect(self._load_failed)
        worker.finished.connect(self._load_finished)
        worker.start()
        self._set_controls()

    def _video_loaded(self, info):
        if self._closing:
            return
        self.info = info
        self._auto_undo = None
        self.auto_undo_button.hide()
        self._update_export_hint()
        self.position = 0.0
        self._preview_errors = False
        self._original_image = self._ascii_image = None
        self._revision += 1
        self.file_title.setText(info.path.name)
        self.file_title.setToolTip(str(info.path))
        self.file_meta.setText(f"{info.width} × {info.height}  /  {info.fps:g} fps  /  {time_text(info.duration)}")
        self.total_time.setText(time_text(info.duration))
        self.current_time.setText("00:00")
        self.timeline.blockSignals(True)
        self.timeline.setValue(0)
        self.timeline.blockSignals(False)
        self.preview_note.setText("Предпросмотр без звука")
        self.audio_check.setText("Сохранить исходный звук" if info.has_audio else "В исходнике нет звука")
        self.canvas.empty = False
        self.ascii_view.setChecked(True)
        self.status.setText("Пробел — воспроизведение  ·  ← → — перемотка на 5 секунд")
        self._request_preview()

    def _load_failed(self, message):
        if self._closing:
            return
        self.status.setText("Не удалось открыть видео. Выберите другой файл.")
        self._show_error("Не удалось открыть видео", message)

    def _load_finished(self):
        worker = self._probe_thread
        self._probe_thread = None
        if worker:
            worker.deleteLater()
        self._set_controls()
        self._finish_close()

    def _request_preview(self):
        if self._closing:
            return
        if not self.info:
            image = self._hero_renderer.render(self._hero_source, self.render_settings(), (960, 600))
            self.canvas.set_image(image_from_array(image), empty=True)
            return
        if self._preview_busy:
            self._preview_pending = True
            return
        self._request_id += 1
        self._issued_revision = self._revision
        self._preview_busy = True
        self._preview_pending = False
        size = fit_size(self.info.width, self.info.height, 1000)
        self._preview_thread.submit((
            self._request_id, self.info, self.position, self.render_settings(), size, self.playing,
        ))

    def _preview_ready(self, request_id, original, rendered):
        self._preview_busy = False
        if self._closing:
            return
        if request_id == self._request_id and self._issued_revision == self._revision:
            self._original_image, self._ascii_image = original, rendered
            self._view_changed()
        if self._preview_pending:
            QTimer.singleShot(0, self._request_preview)

    def _preview_failed(self, request_id, message):
        self._preview_busy = False
        if self._closing:
            return
        if request_id == self._request_id and self._issued_revision == self._revision:
            self.pause()
            self._preview_errors = True
            self.status.setText(message)
        if self._preview_pending:
            QTimer.singleShot(0, self._request_preview)

    def _view_changed(self, *args):
        image = self._original_image if self.original_view.isChecked() else self._ascii_image
        if image is not None:
            self.canvas.set_image(image)
        self.canvas_label.setText("ОРИГИНАЛ" if self.original_view.isChecked() else "ПРЕДПРОСМОТР")

    def _canvas_clicked(self):
        if self.info:
            self.toggle_play()
        else:
            self.choose_file()

    def toggle_play(self):
        if not self.info or self._exporting() or self._loading() or self._analysing() or self._closing:
            return
        if self.playing:
            self.pause()
            return
        if self.position >= self.info.duration - 1 / self.info.fps:
            self.position = 0.0
        self.playing = True
        self._play_origin = time.monotonic() - self.position
        self.play_button.setIcon(icon("pause"))
        self._play_timer.start()

    def pause(self):
        self.playing = False
        self._play_timer.stop()
        self.play_button.setIcon(icon("play"))

    def _tick(self):
        if not self.info:
            return
        self.position = min(time.monotonic() - self._play_origin, max(0, self.info.duration - 1 / self.info.fps))
        if time.monotonic() - self._play_origin >= self.info.duration:
            self.pause()
        self._sync_timeline()
        self._request_preview()

    def _sync_timeline(self):
        self.current_time.setText(time_text(self.position))
        self.timeline.blockSignals(True)
        self.timeline.setValue(round(self.position / self.info.duration * 10000))
        self.timeline.blockSignals(False)

    def _seek(self, value):
        if not self.info:
            return
        self.position = min(value / 10000 * self.info.duration, max(0, self.info.duration - 1 / self.info.fps))
        if self.playing:
            self._play_origin = time.monotonic() - self.position
        self.current_time.setText(time_text(self.position))
        self._revision += 1
        self._debounce.start()

    def seek_relative(self, amount):
        if self.info and not self._exporting():
            position = max(0, min(self.info.duration, self.position + amount))
            self.timeline.setValue(round(position / self.info.duration * 10000))

    def choose_export(self):
        if not self.info or self._exporting() or self._loading() or self._analysing() or self._closing:
            return
        self.pause()
        # Demo is bundled in the application folder; save it beside the user's application.
        if self.info.path == resource_path("demo.mp4").resolve():
            folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        else:
            folder = self.info.path.parent
        initial = folder / f"{self.info.path.stem}_ascii.mp4"
        destination, _ = QFileDialog.getSaveFileName(self, "Сохранить ASCII-видео", str(initial), "Видео MP4 (*.mp4)")
        if destination:
            selected = Path(destination)
            target = selected.with_suffix(".mp4")
            # The native dialog confirms the typed name; also confirm a changed suffix.
            if target != selected and target.exists():
                dialog = QMessageBox(self)
                dialog.setWindowTitle("Файл уже существует")
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setText(f"Заменить файл «{target.name}»?")
                replace_button = dialog.addButton("Заменить", QMessageBox.ButtonRole.AcceptRole)
                dialog.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
                dialog.exec()
                if dialog.clickedButton() != replace_button:
                    return
            destination = str(target)
            self.start_export(destination)

    def start_export(self, destination):
        if not self.info or self._exporting() or self._loading() or self._analysing() or self._closing:
            return
        if Path(destination).resolve() == self.info.path.resolve():
            self._show_error("Выберите другое имя", "Сохраните результат в новый файл, чтобы оставить исходное видео.")
            return
        self.pause()
        self.progress_panel.show()
        self.result_button.hide()
        self.folder_button.hide()
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Отменить")
        self.progress_label.setText("Подготовка экспорта…")
        self.progress_bar.setValue(0)
        options = ExportOptions(self.size_combo.currentData(), self.fps_combo.currentData(), self.audio_check.isChecked(),
                                upscale=self.upscale_check.isChecked(), interpolate=self.interpolate_check.isChecked())
        worker = ExportWorker(self.info, destination, self.render_settings(), options, self)
        self._export_thread = worker
        worker.progress.connect(self._export_progress)
        worker.completed.connect(self._export_completed)
        worker.failed.connect(self._export_failed)
        worker.cancelled.connect(self._export_cancelled)
        worker.finished.connect(self._export_finished)
        worker.start()
        self.status.setText("Рассчитываем промежуточные кадры и собираем ASCII-видео…" if options.interpolate else "Собираем ASCII-видео. Готовый файл появится после завершения экспорта.")
        self._set_controls()

    def _export_progress(self, count, total, elapsed):
        fraction = min(0.995, count / max(1, total))
        self.progress_bar.setValue(round(fraction * 1000))
        rate = count / max(elapsed, 0.01)
        eta = max(0, total - count) / max(rate, 0.01)
        self.progress_label.setText(f"Экспорт  {fraction:.0%}   /   {rate:.1f} кадр/с   /   осталось ≈ {time_text(eta)}")

    def _export_completed(self, path):
        self._last_export = Path(path)
        self.progress_bar.setValue(1000)
        self.progress_label.setText("Готово  /  " + Path(path).name)
        self.cancel_button.hide()
        self.result_button.show()
        self.folder_button.show()
        self.status.setText("Сохранено: " + path)

    def _export_failed(self, message):
        self.progress_label.setText("Экспорт не завершён")
        self.cancel_button.hide()
        self.status.setText("Измените настройки или папку сохранения и повторите экспорт.")
        if not self._closing:
            self._show_error("Не удалось экспортировать видео", message)

    def _export_cancelled(self):
        self.progress_label.setText("Экспорт отменён")
        self.cancel_button.hide()
        self.progress_bar.setValue(0)
        self.status.setText("Экспорт отменён. Настройки сохранены, можно попробовать ещё раз.")

    def _export_finished(self):
        worker = self._export_thread
        self._export_thread = None
        if worker:
            worker.deleteLater()
        self._set_controls()
        self._finish_close()

    def cancel_export(self):
        if self._exporting():
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Отмена…")
            self._export_thread.job.cancel()

    def open_result(self):
        if self._last_export and self._last_export.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_export)))

    def open_result_folder(self):
        if self._last_export:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_export.parent)))

    def _show_error(self, title, message):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(message)
        dialog.exec()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not (self._exporting() or self._loading() or self._analysing()):
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].isLocalFile():
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.load_video(urls[0].toLocalFile())
            event.acceptProposedAction()

    def closeEvent(self, event):
        if not self._closing and self._exporting():
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Экспорт ещё идёт")
            dialog.setText("Прервать экспорт и закрыть приложение?")
            stop = dialog.addButton("Прервать и закрыть", QMessageBox.ButtonRole.DestructiveRole)
            dialog.addButton("Продолжить экспорт", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() != stop:
                event.ignore()
                return
        self._closing = True
        self.pause()
        self._debounce.stop()
        self._preview_thread.stop()
        if self._exporting():
            self._export_thread.job.cancel()
        if self._analysing():
            self._auto_thread.cancel_event.set()
        if self._loading() or self._exporting() or self._analysing() or self._preview_thread.isRunning():
            self.status.setText("Завершаем работу…")
            self._set_controls()
            event.ignore()
            QTimer.singleShot(150, self._finish_close)
        else:
            event.accept()

    def _finish_close(self):
        if self._closing and not (self._loading() or self._exporting() or self._analysing() or self._preview_thread.isRunning()):
            self.close()
