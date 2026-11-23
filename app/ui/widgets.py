from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import PALETTE

try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:  # pragma: no cover - QtSvg входит в стандартный PySide6
    QSvgRenderer = None  # type: ignore[assignment,misc]


def copy_to_clipboard(value: str) -> None:
    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(str(value))


def repolish(widget: QWidget) -> None:
    """Переприменяет QSS после изменения динамических свойств виджета."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


class ExchangeDot(QFrame):
    """Цветной индикатор типа площадки (Spot / Futures)."""

    def __init__(self, exchange_key: str, size: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        color = PALETTE["spot"] if exchange_key == "binance_spot" else PALETTE["fut"]
        radius = size // 2
        self.setStyleSheet(
            f"background-color: {color}; border-radius: {radius}px; border: none;"
        )


class TfBadge(QLabel):
    """Цветной бейдж таймфрейма (1h, 15m, 5m)."""

    def __init__(
        self,
        text: str,
        tf_key: str | None = None,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("TfBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        normalized = (tf_key or text).strip().lower()
        if normalized in ("1h", "15m", "5m"):
            self.setProperty("tf", normalized)
        else:
            self.setProperty("tf", "other")

        if compact:
            self.setProperty("compact", True)
        repolish(self)


class MetricBox(QFrame):
    """Блок метрики (Масштаб — кликабельный primary, Шаг цены — информационный secondary)."""

    copied = Signal(str, str)

    def __init__(
        self,
        title: str,
        value: str,
        copy_label: str = "",
        primary: bool = False,
        copyable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._value = str(value)
        self._copy_label = copy_label
        self._copyable = copyable

        self.setObjectName("MetricBox")
        self.setProperty("variant", "primary" if primary else "secondary")
        self.setProperty("copyable", copyable)

        if copyable:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.setToolTip(f"Нажмите, чтобы скопировать «{title}»")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        lbl_title = QLabel(title.upper())
        lbl_title.setObjectName("MetricLabelPrimary" if primary else "MetricLabelSecondary")
        lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        top_row.addWidget(lbl_title)
        top_row.addStretch(1)

        if copyable:
            lbl_icon = QLabel("⧉")
            lbl_icon.setObjectName("MetricLabelPrimary" if primary else "MetricLabelSecondary")
            lbl_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            top_row.addWidget(lbl_icon)

        lbl_val = QLabel(self._value)
        lbl_val.setObjectName("MetricValuePrimary" if primary else "MetricValueSecondary")
        lbl_val.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout.addLayout(top_row)
        layout.addWidget(lbl_val)

        repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._copyable and event.button() == Qt.MouseButton.LeftButton:
            copy_to_clipboard(self._value)
            self.copied.emit(self._copy_label, self._value)
            event.accept()
            return
        super().mousePressEvent(event)


class ThresholdButton(QPushButton):
    """Кликабельная кнопка-порог для таблицы фильтров ClusterSearch."""

    copied = Signal(str, str)

    def __init__(
        self,
        value: str,
        metric_type: str,
        copy_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"{value}  ⧉", parent)
        self._value = str(value)
        self._copy_label = copy_label

        self.setObjectName("ThresholdPill")
        self.setProperty("metricType", metric_type)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(f"Нажмите, чтобы скопировать порог ({copy_label})")
        self.clicked.connect(self._on_click)
        repolish(self)

    def _on_click(self) -> None:
        copy_to_clipboard(self._value)
        self.copied.emit(self._copy_label, self._value)


class ToastNotification(QLabel):
    """Всплывающее уведомление в правом нижнем углу главного окна."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ToastNotification")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, duration_ms: int = 1800) -> None:
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin_x = 24
        margin_y = 22
        x = max(margin_x, parent.width() - self.width() - margin_x)
        y = max(margin_y, parent.height() - self.height() - margin_y)
        self.move(x, y)


class LogoMark(QWidget):
    """Векторный логотип приложения с чётким рендером на HiDPI."""

    def __init__(
        self,
        svg_path: Path | None,
        size: int = 36,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._renderer = None
        if svg_path is not None and QSvgRenderer is not None:
            renderer = QSvgRenderer(str(svg_path))
            if renderer.isValid():
                self._renderer = renderer
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        if self._renderer is not None:
            self._renderer.render(painter, rect)
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2563EB"))
        radius = self.width() * 0.28
        painter.drawRoundedRect(rect, radius, radius)
