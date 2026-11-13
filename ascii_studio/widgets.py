from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QSlider, QStyle, QWidget

from .theme import GOLD


def icon(kind: str, color=GOLD, size=20) -> QIcon:
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.scale(size / 20, size / 20)
    if kind == "folder":
        painter.drawPolyline(QPolygonF([QPointF(2, 6), QPointF(2, 4), QPointF(8, 4), QPointF(10, 6), QPointF(17, 6)]))
        painter.drawRoundedRect(QRectF(2, 7, 16, 10), 2, 2)
    elif kind == "export":
        painter.drawLine(QPointF(10, 2), QPointF(10, 13))
        painter.drawPolyline(QPolygonF([QPointF(6, 9), QPointF(10, 13), QPointF(14, 9)]))
        painter.drawPolyline(QPolygonF([QPointF(3, 13), QPointF(3, 17), QPointF(17, 17), QPointF(17, 13)]))
    elif kind == "play":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(16, 10), QPointF(7, 16)]))
    elif kind == "pause":
        painter.setPen(QPen(QColor(color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(7, 5, 7, 15)
        painter.drawLine(13, 5, 13, 15)
    elif kind == "mark":
        painter.setPen(QPen(QColor(color), 1.2))
        for row, text in enumerate(["  /\\  ", " /::\\ ", "/_  _\\"]):
            font = QFont("Consolas", 6)
            painter.setFont(font)
            painter.drawText(QRectF(0, row * 6, 20, 8), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return QIcon(pixmap)


def label(text="", name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, name=None, icon_name=None):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if name:
        widget.setObjectName(name)
    if icon_name:
        widget.setIcon(icon(icon_name, "#18140c" if name == "primary" else GOLD))
        widget.setIconSize(QSize(18, 18))
    return widget


class SeekSlider(QSlider):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center_y = self.height() / 2
        available = max(1, self.width() - 14)
        position = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), self.value(), available)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#29292b"))
        painter.drawRoundedRect(QRectF(7, center_y - 1.5, available, 3), 1.5, 1.5)
        painter.setBrush(QColor("#b69756" if self.isEnabled() else "#51442b"))
        painter.drawRoundedRect(QRectF(7, center_y - 1.5, position, 3), 1.5, 1.5)
        painter.setBrush(QColor("#e2c384" if self.isEnabled() else "#776441"))
        painter.drawEllipse(QPointF(7 + position, center_y), 5.5, 5.5)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#6d5d3b"), 1))
            painter.drawEllipse(QPointF(7 + position, center_y), 8, 8)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            fraction = max(0.0, min(1.0, (event.position().x() - 6) / max(1, self.width() - 12)))
            self.setValue(round(self.minimum() + fraction * (self.maximum() - self.minimum())))
        super().mousePressEvent(event)


class StudioComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#a18d63"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x, y = self.width() - 15, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5)]))
        painter.end()


class PreviewCanvas(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(340, 230)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.image = None
        self.empty = True
        self.original = False

    def set_image(self, image, empty=False):
        self.image, self.empty = image, empty
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self.image is not None and not self.image.isNull():
            available = self.rect().adjusted(12, 8, -12, -8)
            if self.empty:
                available.adjust(8, 0, -8, -64)
            fitted = self.image.size().scaled(available.size(), Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRectF(
                available.center().x() - fitted.width() / 2,
                available.center().y() - fitted.height() / 2,
                fitted.width(), fitted.height(),
            )
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(rect, self.image)
        if self.empty:
            painter.setPen(QColor("#dad5ca"))
            painter.setFont(QFont("Segoe UI", 17, QFont.Weight.DemiBold))
            painter.drawText(QRectF(0, self.height() - 81, self.width(), 31), Qt.AlignmentFlag.AlignCenter,
                             "Движение. В новом языке.")
            painter.setPen(QColor("#737378"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(QRectF(0, self.height() - 44, self.width(), 24), Qt.AlignmentFlag.AlignCenter,
                             "Перетащите видео сюда или выберите файл")
        # Quiet viewfinder corners frame the canvas without a decorative grid.
        painter.setPen(QPen(QColor("#594b30"), 1))
        for x, y, dx, dy in [(17, 17, 1, 1), (self.width()-18, 17, -1, 1),
                              (17, self.height()-18, 1, -1), (self.width()-18, self.height()-18, -1, -1)]:
            painter.drawLine(x, y, x + dx * 10, y)
            painter.drawLine(x, y, x, y + dy * 10)
        painter.end()
