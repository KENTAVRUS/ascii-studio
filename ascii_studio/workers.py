from __future__ import annotations

import threading
from pathlib import Path

import cv2
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .engine import AsciiRenderer, ExportCancelled, ExportJob, probe_video
from .autotune import AutoTuneCancelled, analyze_video


def image_from_array(rgb):
    height, width = rgb.shape[:2]
    return QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()


class ProbeWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.loaded.emit(probe_video(self.path))
        except Exception as error:
            self.failed.emit(str(error))


class AutoTuneWorker(QThread):
    completed = Signal(object)
    progress = Signal(int, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, info, settings, parent=None):
        super().__init__(parent)
        self.info, self.settings = info, settings
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = analyze_video(self.info, self.settings, self.cancel_event, self.progress.emit)
            if self.cancel_event.is_set():
                raise AutoTuneCancelled()
            self.completed.emit(result)
        except AutoTuneCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class PreviewWorker(QThread):
    rendered = Signal(int, QImage, QImage)
    failed = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condition = threading.Condition()
        self._pending = None
        self._stopping = False

    def submit(self, request):
        with self._condition:
            self._pending = request
            self._condition.notify()

    def stop(self):
        with self._condition:
            self._stopping = True
            self._pending = None
            self._condition.notify()

    def run(self):
        cap, current_path = None, None
        last_time = -1.0
        renderer = AsciiRenderer()
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._stopping:
                        self._condition.wait()
                    if self._stopping:
                        return
                    request = self._pending
                    self._pending = None
                request_id, info, position, settings, size, playing = request
                try:
                    if current_path != info.path:
                        if cap:
                            cap.release()
                        cap = cv2.VideoCapture(str(info.path))
                        current_path = info.path
                        last_time = -1.0
                    target_frame = max(0, min(max(0, info.frames - 1), round(position * info.fps)))
                    next_frame = round(cap.get(cv2.CAP_PROP_POS_FRAMES))
                    if target_frame < next_frame or target_frame - next_frame > 4:
                        cap.set(cv2.CAP_PROP_POS_MSEC, position * 1000)
                    else:
                        while next_frame < target_frame:
                            cap.grab()
                            next_frame += 1
                    success, bgr = cap.read()
                    if not success:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target_frame - 1))
                        success, bgr = cap.read()
                    if not success:
                        raise ValueError("Не удалось прочитать этот кадр.")
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
                    continuous = playing and 0 < position - last_time < 0.3
                    rendered = renderer.render(rgb, settings, size, temporal=continuous)
                    last_time = position
                    self.rendered.emit(request_id, image_from_array(rgb), image_from_array(rendered))
                except Exception as error:
                    self.failed.emit(request_id, str(error))
        finally:
            if cap:
                cap.release()


class ExportWorker(QThread):
    progress = Signal(int, int, float)
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, info, destination, settings, options, parent=None):
        super().__init__(parent)
        self.info, self.destination = info, Path(destination)
        self.settings, self.options = settings, options
        self.job = ExportJob()

    def run(self):
        try:
            result = self.job.run(self.info, self.destination, self.settings, self.options, self.progress.emit)
            self.completed.emit(str(result))
        except ExportCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))
