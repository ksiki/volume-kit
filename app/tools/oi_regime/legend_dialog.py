"""Немодальный справочник «Легенда и обозначения» для OI & Delta Regime."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.oi_regime.models import (
    REGIME_META,
    SPOT_STATUS_DIV,
    SPOT_STATUS_PASSIVE,
    SPOT_STATUS_SYNC,
)
from app.tools.oi_regime.chart_widget import (
    ABSORPTION_COLOR,
    ANOMALY_ALPHA_MAX,
    ANOMALY_ALPHA_MIN,
    RESISTANCE_COLOR,
    STANDALONE_COLOR,
    SUPPORT_COLOR,
    Z_BAR_HIGH_NEGATIVE,
    Z_BAR_HIGH_POSITIVE,
    Z_BAR_LOW_NEGATIVE,
    Z_BAR_LOW_POSITIVE,
    Z_THRESHOLD_COLOR,
)
from app.tools.oi_regime.labels import (
    ABSORPTION_MARK,
    LAYER_OPTIONS,
    REGIME_COMPACT,
    REGIME_SHORT,
    SENSITIVITY_PRESETS,
    SPOT_SHORT,
)
from app.ui.theme import FONT_FAMILY_MONO, FONT_FAMILY_UI, PALETTE

PREVIEW_WIDTH = 128
MINI_WIDTH = 108
MINI_HEIGHT = 38


class _MiniPreview(QWidget):
    """Компактная QPainter-копия графического элемента chart_widget.py."""

    def __init__(self, kind: str, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._text = text
        self.setFixedSize(MINI_WIDTH, MINI_HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        frame = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        painter.setPen(QPen(QColor(PALETTE["border"]), 1))
        painter.setBrush(QColor(PALETTE["surface_alt"]))
        painter.drawRoundedRect(frame, 7, 7)
        inner = frame.adjusted(6.0, 5.0, -6.0, -5.0)
        handler = getattr(self, f"_paint_{self._kind}", None)
        if handler is not None:
            handler(painter, inner)

    def _paint_anomaly(self, painter: QPainter, inner: QRectF) -> None:
        color = QColor(REGIME_META["LONG_BUILD"][2])
        for offset, alpha in ((0.0, ANOMALY_ALPHA_MIN), (14.0, ANOMALY_ALPHA_MAX)):
            band = QColor(color)
            band.setAlpha(alpha)
            painter.fillRect(
                QRectF(
                    inner.left() + 8.0 + offset,
                    inner.top() + 1.0,
                    10.0,
                    inner.height() - 2.0,
                ),
                band,
            )
        painter.setFont(QFont(FONT_FAMILY_MONO, 7))
        painter.setPen(QColor(PALETTE["text_muted"]))
        painter.drawText(
            QRectF(inner.right() - 26.0, inner.top(), 26.0, inner.height()),
            Qt.AlignmentFlag.AlignCenter,
            "σ",
        )

    def _paint_absorption(self, painter: QPainter, inner: QRectF) -> None:
        band = QRectF(inner.left() + 12.0, inner.top() + 1.0, 20.0, inner.height() - 2.0)
        fill = QColor(REGIME_META["LONG_BUILD"][2])
        fill.setAlpha(ANOMALY_ALPHA_MIN)
        painter.fillRect(band, fill)
        pen = QPen(QColor(ABSORPTION_COLOR), 1, Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(band)

        center_x = band.center().x()
        painter.setPen(QPen(QColor(PALETTE["ok"]), 1))
        painter.drawLine(
            QPointF(center_x, band.top() + 5.0), QPointF(center_x, band.bottom() - 5.0)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["ok"]))
        painter.drawRect(QRectF(center_x - 3.0, band.center().y() - 3.0, 6.0, 8.0))
        painter.setBrush(QColor(ABSORPTION_COLOR))
        painter.drawRect(QRectF(center_x - 7.0, band.top() + 2.0, 14.0, 3.0))

        painter.setFont(QFont(FONT_FAMILY_MONO, 7, QFont.Weight.Bold))
        painter.setPen(QColor(ABSORPTION_COLOR))
        painter.drawText(
            QRectF(inner.left() + 52.0, inner.top(), 32.0, inner.height()),
            Qt.AlignmentFlag.AlignCenter,
            ABSORPTION_MARK,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_standalone(self, painter: QPainter, inner: QRectF) -> None:
        center_x = inner.left() + 24.0
        high = inner.top() + 10.0
        low = inner.bottom() - 3.0
        painter.setPen(QPen(QColor(PALETTE["ok"]), 1))
        painter.drawLine(QPointF(center_x, high), QPointF(center_x, low))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["ok"]))
        painter.drawRect(QRectF(center_x - 3.0, high + 3.0, 6.0, 9.0))

        tip_y = high - 5.0
        diamond = QPolygonF(
            [
                QPointF(center_x, tip_y - 4.0),
                QPointF(center_x + 4.0, tip_y),
                QPointF(center_x, tip_y + 4.0),
                QPointF(center_x - 4.0, tip_y),
            ]
        )
        painter.setBrush(QColor(STANDALONE_COLOR))
        painter.drawPolygon(diamond)

        painter.setFont(QFont(FONT_FAMILY_MONO, 6))
        painter.setPen(QColor(PALETTE["text_muted"]))
        painter.drawText(
            QRectF(inner.left() + 52.0, inner.top(), 40.0, inner.height()),
            Qt.AlignmentFlag.AlignCenter,
            "без OI",
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_ribbon(self, painter: QPainter, inner: QRectF) -> None:
        ribbon = QRectF(inner.left(), inner.top(), inner.width(), 9.0)
        painter.fillRect(ribbon, QColor(PALETTE["surface_alt"]))
        painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1))
        painter.drawLine(ribbon.bottomLeft(), ribbon.bottomRight())

        color = QColor(REGIME_META["LONG_BUILD"][2])
        plaque = QRectF(ribbon.left() + 4.0, ribbon.top() + 1.5, ribbon.width() - 42.0, 6.0)
        fill = QColor(color)
        fill.setAlpha(52)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(plaque, 2, 2)
        border = QColor(color)
        border.setAlpha(150)
        painter.setPen(QPen(border, 1))
        painter.drawLine(
            QPointF(plaque.left() + 2, plaque.top()),
            QPointF(plaque.right() - 2, plaque.top()),
        )
        painter.setFont(QFont(FONT_FAMILY_MONO, 6, QFont.Weight.Bold))
        painter.setPen(QColor(color))
        painter.drawText(
            plaque,
            Qt.AlignmentFlag.AlignCenter,
            REGIME_COMPACT.get("LONG_BUILD", ""),
        )

        price_top = ribbon.bottom() + 3.0
        corridor = QRectF(plaque.left(), price_top, plaque.width(), inner.bottom() - price_top)
        corridor_fill = QColor(color)
        corridor_fill.setAlpha(14)
        painter.fillRect(corridor, corridor_fill)
        pen = QPen(
            QColor(color.red(), color.green(), color.blue(), 120),
            1,
            Qt.PenStyle.DashLine,
        )
        painter.setPen(pen)
        for x in (corridor.left(), corridor.right()):
            painter.drawLine(QPointF(x, price_top), QPointF(x, inner.bottom()))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_trap(self, painter: QPainter, inner: QRectF) -> None:
        rows = (
            (RESISTANCE_COLOR, "66.0k", inner.top() + 1.0),
            (SUPPORT_COLOR, "64.0k", inner.bottom() - 11.0),
        )
        painter.setFont(QFont(FONT_FAMILY_MONO, 5))
        for color_hex, label, top in rows:
            color = QColor(color_hex)
            band = QRectF(inner.left() + 2.0, top, inner.width() - 30.0, 10.0)
            fill = QColor(color)
            fill.setAlpha(26)
            painter.fillRect(band, fill)
            mid_y = band.center().y()
            painter.setPen(QPen(color, 1.1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(band.left(), mid_y), QPointF(band.right(), mid_y))
            painter.setPen(QColor(color))
            painter.drawText(
                QRectF(band.right() + 2.0, mid_y - 5.0, 27.0, 10.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )


    def _paint_zscore(self, painter: QPainter, inner: QRectF) -> None:
        zero_y = inner.center().y()
        painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1))
        painter.drawLine(QPointF(inner.left(), zero_y), QPointF(inner.right(), zero_y))
        painter.setPen(QPen(QColor(Z_THRESHOLD_COLOR), 1, Qt.PenStyle.DashLine))
        painter.drawLine(
            QPointF(inner.left(), zero_y - 8.0), QPointF(inner.right(), zero_y - 8.0)
        )
        painter.drawLine(
            QPointF(inner.left(), zero_y + 8.0), QPointF(inner.right(), zero_y + 8.0)
        )

        step = (inner.width() - 12.0) / 5.0
        specs = (
            (4.0, Z_BAR_LOW_POSITIVE, False),
            (-4.0, Z_BAR_LOW_NEGATIVE, False),
            (9.0, Z_BAR_HIGH_POSITIVE, False),
            (-9.0, Z_BAR_HIGH_NEGATIVE, False),
            (12.0, Z_BAR_HIGH_POSITIVE, True),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (value, color_hex, capped) in enumerate(specs):
            x = inner.left() + 6.0 + step * (index + 0.5)
            color = QColor(color_hex)
            color.setAlpha(238 if abs(value) >= 8.0 else 175)
            top = zero_y - value if value > 0 else zero_y
            painter.fillRect(QRectF(x - 3.0, top, 6.0, max(1.5, abs(value))), color)
            if capped:
                painter.setBrush(color)
                painter.setPen(QPen(QColor(PALETTE["surface"]), 1.2))
                painter.drawEllipse(QPointF(x, zero_y - value), 2.6, 2.6)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_oi_gap(self, painter: QPainter, inner: QRectF) -> None:
        region = QRectF(inner.left(), inner.top(), inner.width() * 0.58, inner.height())
        fill = QColor(PALETTE["text_muted"])
        fill.setAlpha(16)
        painter.fillRect(region, fill)
        boundary = region.right()
        painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(boundary, inner.top()), QPointF(boundary, inner.bottom()))
        painter.setFont(QFont(FONT_FAMILY_UI, 6))
        painter.setPen(QColor(PALETTE["text_muted"]))
        painter.drawText(
            QRectF(region.left() + 3.0, region.top() + 2.0, region.width() - 6.0, 12.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            ">30д",
        )

    def _paint_measure(self, painter: QPainter, inner: QRectF) -> None:
        box = inner.adjusted(3.0, 2.0, -3.0, -2.0)
        fill = QColor(PALETTE["accent"])
        fill.setAlpha(26)
        painter.fillRect(box, fill)
        pen = QPen(QColor(PALETTE["accent"]), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)

        badge = QRectF(box.center().x() - 17.0, box.top() + 2.0, 34.0, 11.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["text"]))
        painter.drawRoundedRect(badge, 2, 2)
        painter.setFont(QFont(FONT_FAMILY_MONO, 6, QFont.Weight.Bold))
        painter.setPen(QColor(PALETTE["surface_alt"]))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "+2.4%")
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_metric(self, painter: QPainter, inner: QRectF) -> None:
        painter.setFont(QFont(FONT_FAMILY_MONO, 9, QFont.Weight.Bold))
        painter.setPen(QColor(PALETTE["text_secondary"]))
        painter.drawText(
            inner,
            Qt.AlignmentFlag.AlignCenter,
            self._text,
        )


def _pill(text: str, variant: str | None = None) -> QLabel:
    """Пилюля с теми же objectName/variant, что и в правой ленте."""
    label = QLabel(text)
    label.setObjectName("OiPill")
    if variant:
        label.setProperty("variant", variant)
    return label


def _regime_badge(key: str) -> QLabel:
    """Бейдж режима: цвет и подпись берутся из REGIME_META/REGIME_SHORT."""
    label, _marker, color = REGIME_META[key]
    badge = QLabel(REGIME_SHORT.get(key, key))
    badge.setObjectName("OiRegimeBadge")
    badge.setStyleSheet(
        f"background-color: {color}; color: {PALETTE['surface']}; border: none;"
    )
    badge.setToolTip(label)
    return badge


def _badge_preview(widget: QWidget, compact: str | None = None) -> QWidget:
    """Строка из настоящих бейджей и компактного моно-кода."""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    lay.addWidget(widget)
    if compact:
        code = QLabel(compact)
        code.setFont(QFont(FONT_FAMILY_MONO, 9, QFont.Weight.Bold))
        code.setStyleSheet(f"color: {PALETTE['text_muted']};")
        lay.addWidget(code)
    lay.addStretch(1)
    return holder


class OIRegimeLegendDialog(QDialog):
    """Немодальное окно-справочник по всем обозначениям инструмента."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Легенда и обозначения · OI & Delta Regime")
        self.setModal(False)
        self.resize(720, 620)
        self.setMinimumSize(560, 440)
        self.setStyleSheet(self._dialog_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("Легенда и обозначения")
        title.setObjectName("PageTitle")
        hint = QLabel(
            "Живые образцы элементов графика и ленты. Окно можно держать открытым "
            "рядом с графиком и сверять обозначения в реальном времени."
        )
        hint.setObjectName("SettingsHint")
        hint.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(hint)

        tabs = QTabWidget()
        self._tabs = tabs
        tabs.addTab(self._build_modes_tab(), "Режимы и теги")
        tabs.addTab(self._build_layers_tab(), "График и слои")
        tabs.addTab(self._build_metrics_tab(), "Метрики и расчёты")
        tabs.addTab(self._build_controls_tab(), "Управление и навигация")
        root.addWidget(tabs, 1)

    @staticmethod
    def _dialog_qss() -> str:
        return f"""
QDialog {{
    background-color: {PALETTE["bg"]};
}}
QTabWidget::pane {{
    border: 1px solid {PALETTE["border"]};
    border-radius: 8px;
    background-color: {PALETTE["surface"]};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {PALETTE["text_secondary"]};
    padding: 7px 14px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {PALETTE["text"]};
}}
QTabBar::tab:selected {{
    background-color: {PALETTE["surface"]};
    border-color: {PALETTE["border"]};
    color: {PALETTE["accent_hover"]};
    font-weight: 700;
}}
"""

    @staticmethod
    def _make_scroll() -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)
        scroll.setWidget(content)
        return scroll, lay

    @staticmethod
    def _add_section(layout: QVBoxLayout, title: str) -> QVBoxLayout:
        card = QFrame()
        card.setObjectName("Card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("SettingsSectionTitle")
        card_lay.addWidget(heading)
        layout.addWidget(card)
        return card_lay

    @staticmethod
    def _add_row(
        layout: QVBoxLayout,
        preview: QWidget,
        title: str,
        description: str,
    ) -> None:
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(12)

        holder = QWidget()
        holder.setFixedWidth(PREVIEW_WIDTH)
        holder_lay = QHBoxLayout(holder)
        holder_lay.setContentsMargins(0, 0, 0, 0)
        holder_lay.setSpacing(0)
        holder_lay.addWidget(preview, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        holder_lay.addStretch(1)
        row_lay.addWidget(holder, 0, Qt.AlignmentFlag.AlignTop)

        text_lay = QVBoxLayout()
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(3)
        title_lbl = QLabel(title)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"font-size: 12.5px; font-weight: 700; color: {PALETTE['text']};"
        )
        desc_lbl = QLabel(description)
        desc_lbl.setObjectName("SettingsHint")
        desc_lbl.setWordWrap(True)
        text_lay.addWidget(title_lbl)
        text_lay.addWidget(desc_lbl)
        row_lay.addLayout(text_lay, 1)
        layout.addWidget(row)

    @staticmethod
    def _add_note(layout: QVBoxLayout, text: str) -> None:
        note = QLabel(text)
        note.setObjectName("SettingsHint")
        note.setWordWrap(True)
        layout.addWidget(note)

    def _build_modes_tab(self) -> QScrollArea:
        scroll, lay = self._make_scroll()

        modes = self._add_section(lay, "Матрица режимов OI & Delta")
        regime_rows = (
            (
                "LONG_BUILD",
                "ΔOI > 0, Δ > 0. Открываются новые лонги: покупатель набирает позицию. "
                "Инициатива покупателя — продолжение вверх при подтверждении спота.",
            ),
            (
                "LONG_CLOSE",
                "ΔOI < 0, Δ < 0. Лонги сокращаются: покупатель закрывает позицию. "
                "Разгрузка и фиксация без набора новых шортов — движение вниз.",
            ),
            (
                "SHORT_CLOSE",
                "ΔOI < 0, Δ > 0. Шорты сокращаются: продавец закрывает позицию. "
                "Закрытие шортов поддерживает движение вверх.",
            ),
            (
                "SHORT_BUILD",
                "ΔOI > 0, Δ < 0. Открываются новые шорты: продавец набирает позицию. "
                "Инициатива продавца — давление вниз.",
            ),
        )
        for key, meaning in regime_rows:
            preview = _badge_preview(_regime_badge(key), REGIME_COMPACT.get(key))
            self._add_row(
                modes,
                preview,
                f"{REGIME_SHORT.get(key, key)} · {REGIME_COMPACT.get(key, '')}",
                f"{REGIME_META[key][0]}. {meaning}",
            )
        self._add_note(
            modes,
            "При плотном масштабе бейджи автоматически сжимаются до "
            f"{REGIME_COMPACT['LONG_BUILD']} / {REGIME_COMPACT['SHORT_CLOSE']} "
            "и раскрываются обратно при увеличении графика.",
        )

        absorption = self._add_section(lay, "Поглощение (лимитная защита)")
        absorption_preview = _badge_preview(
            _pill(f"{ABSORPTION_MARK} Поглощение", "warn")
        )
        self._add_row(
            absorption,
            absorption_preview,
            f"{ABSORPTION_MARK} Поглощение",
            "Срабатывает при сильной фьючерсной дельте: (1) свеча отбивает против "
            "дельты или закрывается в противоположной трети диапазона; (2) «усилие без "
            "результата» — высокая дельта при сжатом диапазоне свечи относительно медианы. "
            "На графике появляются янтарная пунктирная рамка и засечка у экстремума.",
        )

        context = self._add_section(lay, "Контекст спота и бирж")
        self._add_row(
            context,
            _badge_preview(_pill(SPOT_SHORT[SPOT_STATUS_SYNC], "ok")),
            SPOT_SHORT[SPOT_STATUS_SYNC],
            "Спотовая дельта направлена в ту же сторону, что и фьючерсная, и превышает "
            "порог Z-score подтверждения: движение поддержано спотом.",
        )
        self._add_row(
            context,
            _badge_preview(_pill(SPOT_SHORT[SPOT_STATUS_DIV], "div")),
            SPOT_SHORT[SPOT_STATUS_DIV],
            "Активная дивергенция: спотовая дельта заметно против фьючерсной и превышает "
            "порог дивергенции — потоки расходятся.",
        )
        self._add_row(
            context,
            _badge_preview(_pill(SPOT_SHORT[SPOT_STATUS_PASSIVE], "passive")),
            SPOT_SHORT[SPOT_STATUS_PASSIVE],
            "Пассивный спот при сильном деривативном всплеске OI: спот почти не двигается, "
            "движение идёт только на фьючерсах. Скрывается чекбоксом «Скрыть слабые без Спота».",
        )
        self._add_row(
            context,
            _badge_preview(_pill("Bybit-led")),
            "Bybit-led",
            "Не менее 75% прироста или сброса OI на этой свече пришлось на Bybit. "
            "Фьючерсная дельта при этом считается по Binance Futures.",
        )
        self._add_row(
            context,
            _badge_preview(_pill("LIVE 46%", "live")),
            "LIVE XX%",
            "Текущая незакрытая свеча и доля прошедшего времени бара. Пунктирная рамка "
            "на графике напоминает, что значения ещё меняются до закрытия свечи.",
        )

        lay.addStretch(1)
        return scroll


    def _build_layers_tab(self) -> QScrollArea:
        scroll, lay = self._make_scroll()
        card = self._add_section(lay, "Элементы графика и слои")

        self._add_row(
            card,
            _MiniPreview("anomaly"),
            "Вертикальные полосы аномалий",
            "Выделяют свечи, где одновременно пробиты пороги по Z ΔOI и Z FutΔ. "
            "Чем выше сила события в σ, тем насыщеннее заливка: бледная у порога, "
            "плотная у сильного сигнала.",
        )
        self._add_row(
            card,
            _MiniPreview("absorption"),
            "Маркер поглощения на аномалии",
            "У свечи с поглощением появляется янтарная пунктирная рамка вокруг полосы "
            "аномалии и засечка у high или low. Значок ⚠ добавляется к бейджу режима.",
        )
        self._add_row(
            card,
            _MiniPreview("ribbon"),
            "Дорожка «М-бар» (мульти-бар режимы)",
            "Верхняя полоса над графиком показывает планомерное накопление или разгрузку "
            "за серию последовательных свечей; persistence-фильтр отсекает эхо одиночного "
            "всплеска. При наведении на плашку или свечу внутри зоны подсвечивается ценовой "
            "коридор, а в тултипе видны суммарные ΣΔOI и ΣΔ.",
        )
        self._add_row(
            card,
            _MiniPreview("trap"),
            "Уровни ловушек",
            "Горизонтальные зоны от экстремальных свечей набора или поглощения — вероятные "
            "зоны защиты крупного участника. Показываются до снятия (митигации): закрытия "
            "свечи ниже минимума бычьей зоны поддержки или выше максимума медвежьей зоны "
            "сопротивления.",
        )
        self._add_row(
            card,
            _MiniPreview("standalone"),
            "Поглощения без OI",
            "Компактные янтарные ромбы над свечами с сильной лимитной защитой по дельте "
            "и цене, где порог по изменению OI не был пробит. Включаются слоем "
            "«Поглощения без OI».",
        )
        self._add_row(
            card,
            _MiniPreview("zscore"),
            "Подграфик Z-score",
            "Дорожки Z ΔOI, Z FutΔ и опционально Z SpotΔ. Пунктиром отмечены текущие "
            "пороги; бледные столбики — значения в пределах нормы, яркие зелёные и красные — "
            "пробой порога. Белая точка на вершине означает, что реальное значение σ выше "
            "визуального потолка шкалы; точное число видно в тултипе.",
        )
        self._add_row(
            card,
            _MiniPreview("oi_gap"),
            "Область «Нет истории OI (>30д)»",
            "Полупрозрачная серая область и пунктирная граница показывают, где заканчивается "
            "30-дневный архив OI от Binance. На длинной истории, например на 4h, это помогает "
            "не путать отсутствие данных с нулевым изменением OI.",
        )
        self._add_row(
            card,
            _MiniPreview("measure"),
            "Линейка измерений (Shift + ЛКМ)",
            "Полупрозрачный прямоугольник и бейдж показывают ход цены в процентах, изменение "
            "цены, число свечей и длительность измеряемого участка.",
        )

        lay.addStretch(1)
        return scroll


    def _build_metrics_tab(self) -> QScrollArea:
        scroll, lay = self._make_scroll()
        card = self._add_section(lay, "Метрики и расчёты")

        self._add_row(
            card,
            _MiniPreview("metric", "Z-score\nσ, N"),
            "Z-score (σ) и окно N",
            "Во сколько раз текущий всплеск превышает типичный разброс за предыдущие N "
            "закрытых свечей. Используется робастная статистика на базе медианы и MAD, "
            "а текущая свеча не участвует в собственной базе — самозагрязнения нет.",
        )
        self._add_row(
            card,
            _MiniPreview("metric", "ΔOI\n%"),
            "ΔOI и ΔOI %",
            "Изменение открытого интереса за свечу: сумма Binance и Bybit при доступности "
            "Bybit, в монетах и в процентах от OI на открытии бара. Порог аномалии считается "
            "по робастному Z-score от ΔOI %.",
        )
        self._add_row(
            card,
            _MiniPreview("metric", "2×TB\n− V"),
            "Фьючерсная и спотовая дельта",
            "Разница между рыночными покупками и рыночными продажами: 2 × Taker Buy − Volume "
            "в базовом активе. Фьючерсная дельта считается по Binance Futures, спотовая — "
            "по Binance Spot при его доступности.",
        )
        self._add_row(
            card,
            _MiniPreview("metric", "Базис\n%"),
            "Базис (%)",
            "Премия или дисконт фьючерса относительно спота по индексу премии Binance. "
            "Положительный базис — премия и перегрев фьючерса, отрицательный — дисконт.",
        )
        self._add_row(
            card,
            _MiniPreview("metric", "Фандинг\n%"),
            "Фандинг (%)",
            "Ставка финансирования бессрочного контракта в процентах. Положительная — "
            "лонги платят шортам, отрицательная — шорты платят лонгам.",
        )
        self._add_row(
            card,
            _MiniPreview("metric", "3/6/12"),
            "Окно «Статистика»",
            "Историческая сводка отработки по текущим настройкам: количество событий и "
            "медианные максимальные движения вверх и вниз (MFE/MAE) и закрытие через "
            "3, 6 и 12 закрытых свечей после сигнала.",
        )

        lay.addStretch(1)
        return scroll

    def _build_controls_tab(self) -> QScrollArea:
        scroll, lay = self._make_scroll()

        presets = self._add_section(lay, "Пресеты чувствительности")
        effects = {
            "strict": "Только самые сильные и редкие события.",
            "standard": "Баланс чувствительности и шума.",
            "sensitive": "Больше событий, выше риск ложных сигналов.",
        }
        for key, text, toi, td in SENSITIVITY_PRESETS:
            self._add_row(
                presets,
                _MiniPreview("metric", f"{toi:.1f}σ\n{td:.1f}σ"),
                f"«{text}» · {toi:.1f}σ / {td:.1f}σ",
                f"Порог OI {toi:.1f}σ, порог фьючерсной дельты {td:.1f}σ. "
                f"{effects.get(key, '')}",
            )

        filters = self._add_section(lay, "Фильтры тулбара")
        self._add_row(
            filters,
            _MiniPreview("metric", "Abs"),
            "«Только с Поглощением»",
            "Оставляет в ленте и на графике только события с тегом поглощения.",
        )
        self._add_row(
            filters,
            _MiniPreview("metric", "Spot"),
            "«Скрыть слабые без Спота»",
            "Оставляет события с подтверждением или дивергенцией спота; также скрывает "
            "одиночные маркеры без OI, если спот по ним слабый.",
        )
        self._add_row(
            filters,
            _MiniPreview("metric", "Z"),
            "«Подграфик Z-score»",
            "Показывает или скрывает нижний подграфик Z ΔOI и Z FutΔ. Дорожка Z SpotΔ "
            "добавляется отдельным слоем.",
        )
        layer_names = ", ".join(
            f"«{label}»" for _config_key, label in LAYER_OPTIONS.values()
        )
        self._add_row(
            filters,
            _MiniPreview("metric", "Слои ▾"),
            "Меню «Слои ▾»",
            f"Включает дополнительные слои графика: {layer_names}.",
        )

        controls = self._add_section(lay, "Управление графиком и лентой")
        controls.addWidget(self._build_controls_table())

        lay.addStretch(1)
        return scroll

    @staticmethod
    def _build_controls_table() -> QTableWidget:
        rows = (
            (
                "Колесо мыши",
                "Масштабирование графика относительно положения курсора.",
            ),
            (
                "ЛКМ + перетаскивание",
                "Панорамирование графика по истории.",
            ),
            (
                "Shift + ЛКМ + перетаскивание",
                "Линейка измерений: % хода, Δ цены, число свечей и длительность.",
            ),
            (
                "Двойной клик ЛКМ",
                "Сброс масштаба к виду по умолчанию.",
            ),
            (
                "Клик по карточке в ленте",
                "Центрирование графика на свече и закрепление подсветки.",
            ),
            (
                "Стрелки ↑ / ↓ в ленте",
                "Быстрый перебор аномалий по истории.",
            ),
        )
        table = QTableWidget(len(rows), 2)
        table.setHorizontalHeaderLabels(["Действие", "Результат"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setWordWrap(True)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, (action, result) in enumerate(rows):
            action_item = QTableWidgetItem(action)
            action_item.setFont(QFont(FONT_FAMILY_MONO, 9, QFont.Weight.Bold))
            table.setItem(row, 0, action_item)
            table.setItem(row, 1, QTableWidgetItem(result))
        table.resizeRowsToContents()
        table.setMinimumHeight(240)
        return table

