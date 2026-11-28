from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
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


def prepare_combo_box(combo: QComboBox, max_visible_items: int = 10) -> None:
    """Задаёт явный QListView: стабильный светлый попап и корректная позиция списка."""
    view = QListView()
    view.setUniformItemSizes(True)
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    combo.setView(view)
    combo.setMaxVisibleItems(max_visible_items)


class ProxyPillButton(QPushButton):
    """Компактная пилюля статуса прокси в топбаре."""

    right_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName("ProxyPill")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(32)
        self.setMinimumWidth(124)

        self._state = "off"
        self._dot = QFrame(self)
        self._dot.setObjectName("ProxyStatusDot")
        self._dot.setFixedSize(8, 8)
        self._dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._text_label = QLabel("Прокси: Выкл", self)
        self._text_label.setObjectName("ProxyPillText")
        self._text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 11, 0)
        layout.setSpacing(7)
        layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._text_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._apply_state("off")

    def _apply_state(self, state: str) -> None:
        self._state = state
        self.setProperty("state", state)
        self._dot.setProperty("state", state)
        repolish(self)
        repolish(self._dot)

    def set_status(self, text: str, state: str = "off", tooltip: str = "") -> None:
        self._text_label.setText(text)
        if state != self._state:
            self._apply_state(state)
        self.setToolTip(tooltip)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


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
    """Цветной бейдж таймфрейма (4h, 1h, 15m, 5m)."""

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
        if normalized in ("4h", "1h", "15m", "5m"):
            self.setProperty("tf", normalized)
        else:
            self.setProperty("tf", "other")

        if compact:
            self.setProperty("compact", True)
        repolish(self)


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
