"""Интерактивный свечной график OI & Delta Regime на чистом QPainter."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.services.oi_regime.models import (
    SPOT_STATUS_DIV,
    SPOT_STATUS_SYNC,
    CandleBar,
    RegimeBundle,
    RegimeParams,
)
from app.tools.oi_regime.labels import regime_badge_text
from app.ui.theme import FONT_FAMILY_MONO, FONT_FAMILY_UI, PALETTE

DEFAULT_VIEW_BARS = 110
MIN_VIEW_BARS = 20
ANOMALY_ALPHA = 38
ABSORPTION_COLOR = "#D97706"

Z_Y_MAX_FLOOR = 4.5
Z_Y_MAX_CEIL = 6.0
Z_Y_MAX_FACTOR = 2.2
Z_BAR_LOW_POSITIVE = "#A5B4FC"
Z_BAR_LOW_NEGATIVE = "#93C5FD"
Z_BAR_HIGH_POSITIVE = "#059669"
Z_BAR_HIGH_NEGATIVE = "#DC2626"


def _fmt_num(value: float | None, digits: int = 2, signed: bool = True) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    pattern = f"{{:+.{digits}f}}" if signed else f"{{:,.{digits}f}}"
    text = pattern.format(value)
    if signed and abs(value) >= 1000:
        sign = "+" if value >= 0 else "-"
        text = sign + f"{abs(value):,.{digits}f}"
    return text


def _fmt_z(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:+.2f}σ"


def _fmt_price(value: float, step: float) -> str:
    decimals = 0
    if step > 0:
        decimals = max(0, min(8, int(-math.floor(math.log10(step))) if step < 1 else 0))
    return f"{value:,.{decimals}f}"


class OIRegimeChart(QWidget):
    """Свечи, подсветка аномалий, маркеры режимов и подграфик Z-score."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bundle: RegimeBundle | None = None
        self._bars: list[CandleBar] = []
        self._params: RegimeParams | None = None
        self._show_zscore = True
        self._threshold_oi = 2.0
        self._threshold_delta = 1.5
        self._only_absorption = False
        self._hide_weak_spot = False
        self._visible_count = float(DEFAULT_VIEW_BARS)
        self._anchor_from_end = 0.0
        self._hover_index: int | None = None
        self._hover_pos: QPointF | None = None
        self._panning = False
        self._pan_start_x = 0.0
        self._pan_anchor = 0.0
        self._flash_index: int | None = None

        self.setMouseTracking(True)
        self.setMinimumHeight(280)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

        self._tooltip = QFrame(self)
        self._tooltip.setObjectName("OiChartTooltip")
        self._tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        tooltip_lay = QVBoxLayout(self._tooltip)
        tooltip_lay.setContentsMargins(10, 8, 10, 8)
        self._tooltip_label = QLabel("")
        self._tooltip_label.setTextFormat(Qt.TextFormat.RichText)
        self._tooltip_label.setWordWrap(True)
        self._tooltip_label.setMaximumWidth(360)
        self._tooltip_label.setStyleSheet(
            "color: #F8FAFC; font-size: 12px; background: transparent;"
        )
        tooltip_lay.addWidget(self._tooltip_label)
        self._tooltip.hide()

    # ==================== ПУБЛИЧНЫЙ API ====================

    def set_bundle(self, bundle: RegimeBundle, *, reset_view: bool = False) -> None:
        old_key = (
            (self._bundle.symbol, self._bundle.timeframe) if self._bundle is not None else None
        )
        new_key = (bundle.symbol, bundle.timeframe)
        self._bundle = bundle
        self._bars = list(bundle.bars)
        self._params = bundle.params
        self._threshold_oi = bundle.params.threshold_oi
        self._threshold_delta = bundle.params.threshold_delta
        if reset_view or old_key != new_key or self._visible_count <= 0:
            self.reset_view()
        else:
            self._visible_count = min(self._visible_count, float(max(1, len(self._bars))))
            self._clamp_view()
        self._hover_index = None
        self._tooltip.hide()
        self.update()

    def set_show_zscore(self, enabled: bool) -> None:
        self._show_zscore = bool(enabled)
        self.update()

    def set_thresholds(self, threshold_oi: float, threshold_delta: float) -> None:
        self._threshold_oi = float(threshold_oi)
        self._threshold_delta = float(threshold_delta)
        self.update()

    def set_filters(self, only_absorption: bool, hide_weak_spot: bool) -> None:
        self._only_absorption = bool(only_absorption)
        self._hide_weak_spot = bool(hide_weak_spot)
        self.update()

    def center_on_timestamp(self, ts_ms: object) -> None:
        """Мягко центрирует вид на свече с ближайшим open_time_ms и подсвечивает её."""
        if not self._bars:
            return
        try:
            target_ms = int(ts_ms)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        index = min(
            range(len(self._bars)),
            key=lambda i: abs(self._bars[i].open_time_ms - target_ms),
        )
        count = max(float(MIN_VIEW_BARS), min(self._visible_count, float(len(self._bars))))
        self._visible_count = count
        self._anchor_from_end = (len(self._bars) - 1 - index) - count / 2.0
        self._clamp_view()
        self._flash_index = index
        self._flash_timer.start(1400)
        self.update()

    def reset_view(self) -> None:
        count = min(float(DEFAULT_VIEW_BARS), float(max(1, len(self._bars))))
        self._visible_count = max(float(MIN_VIEW_BARS), count)
        self._visible_count = min(self._visible_count, float(max(1, len(self._bars))))
        self._anchor_from_end = 0.0
        self._clamp_view()
        self.update()

    # ==================== ГЕОМЕТРИЯ ====================

    def _clamp_view(self) -> None:
        n = len(self._bars)
        if n <= 0:
            return
        self._visible_count = max(1.0, min(self._visible_count, float(n)))
        max_anchor = max(0.0, float(n) - self._visible_count)
        self._anchor_from_end = max(0.0, min(self._anchor_from_end, max_anchor))

    def _view_start_end(self) -> tuple[float, float]:
        n = len(self._bars)
        if n <= 0:
            return 0.0, 0.0
        end = (n - 1) - self._anchor_from_end
        start = end - self._visible_count + 1.0
        return start, end

    def _rects(self) -> tuple[QRectF, QRectF | None]:
        left = 10.0
        right = max(left + 40.0, self.width() - 70.0)
        top = 12.0
        bottom = max(top + 40.0, self.height() - 26.0)
        total = bottom - top

        if self._show_zscore and total >= 220:
            gap = 8.0
            price_h = total * 0.72
            z_rect = QRectF(left, top + price_h + gap, right - left, total - price_h - gap)
            price_rect = QRectF(left, top, right - left, price_h)
            return price_rect, z_rect
        return QRectF(left, top, right - left, total), None

    def _is_bar_marked(self, bar: CandleBar) -> bool:
        if not bar.is_anomaly:
            return False
        if self._only_absorption and not bar.is_absorption:
            return False
        if self._hide_weak_spot and bar.spot_status not in (
            SPOT_STATUS_SYNC,
            SPOT_STATUS_DIV,
        ):
            return False
        return True

    def _visible_indices(self) -> tuple[int, int]:
        start, end = self._view_start_end()
        first = max(0, int(math.floor(start)))
        last = min(len(self._bars) - 1, int(math.ceil(end)))
        if last < first:
            return 0, -1
        return first, last

    def _index_at_x(self, x: float, rect: QRectF) -> int | None:
        if not self._bars or rect.width() <= 0:
            return None
        start, _ = self._view_start_end()
        slot = rect.width() / max(1.0, self._visible_count)
        index = int(start + (x - rect.left()) / slot)
        if index < 0 or index >= len(self._bars):
            return None
        return index

    def _x_for_index(self, index: int, rect: QRectF) -> float:
        start, _ = self._view_start_end()
        slot = rect.width() / max(1.0, self._visible_count)
        return rect.left() + (index - start + 0.5) * slot

    def _slot_width(self, rect: QRectF) -> float:
        return rect.width() / max(1.0, self._visible_count)

    def _price_range(self, first: int, last: int) -> tuple[float, float, float]:
        highs = [self._bars[i].high for i in range(first, last + 1)]
        lows = [self._bars[i].low for i in range(first, last + 1)]
        high = max(highs) if highs else 1.0
        low = min(lows) if lows else 0.0
        if high <= low:
            high = low + max(1e-8, abs(low) * 0.01)
        padding = (high - low) * 0.07
        return low - padding, high + padding, high - low

    @staticmethod
    def _nice_step(span: float, target_ticks: int = 5) -> float:
        if not math.isfinite(span) or span <= 0:
            return 1.0
        raw = span / max(1, target_ticks)
        exponent = math.floor(math.log10(raw))
        fraction = raw / (10**exponent)
        if fraction <= 1:
            nice = 1.0
        elif fraction <= 2:
            nice = 2.0
        elif fraction <= 5:
            nice = 5.0
        else:
            nice = 10.0
        return nice * (10**exponent)

    def _format_time(self, ts_ms: int) -> str:
        tz = self._params.tz_offset_hours if self._params is not None else 0
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc) + timedelta(hours=tz)
        first, last = self._visible_indices()
        span_ms = 0
        if 0 <= first <= last < len(self._bars):
            span_ms = self._bars[last].open_time_ms - self._bars[first].open_time_ms
        if span_ms > 24 * 3600 * 1000:
            return dt.strftime("%d.%m %H:%M")
        return dt.strftime("%H:%M")

    def _format_datetime(self, ts_ms: int) -> str:
        tz = self._params.tz_offset_hours if self._params is not None else 0
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc) + timedelta(hours=tz)
        return dt.strftime("%d.%m.%Y %H:%M")


    # ==================== ОТРИСОВКА ====================

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(PALETTE["surface"]))

        if not self._bars:
            painter.setPen(QColor(PALETTE["text_muted"]))
            painter.setFont(QFont(FONT_FAMILY_UI, 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Нет данных для отображения")
            return

        price_rect, z_rect = self._rects()
        if price_rect.width() <= 10 or price_rect.height() <= 10:
            return
        first, last = self._visible_indices()
        if last < first:
            return

        low, high, span = self._price_range(first, last)
        self._draw_price_grid(painter, price_rect, low, high)
        self._draw_time_axis(painter, price_rect, z_rect, first, last)
        self._draw_anomaly_bands(painter, price_rect, z_rect, first, last)
        self._draw_candles(painter, price_rect, first, last, low, high, span)
        self._draw_markers(painter, price_rect, first, last, low, high, span)
        if z_rect is not None:
            self._draw_zscore(painter, z_rect, first, last)
        self._draw_flash(painter, price_rect, z_rect)
        self._draw_crosshair(painter, price_rect, z_rect, low, high)

    def _price_to_y(self, price: float, rect: QRectF, low: float, high: float) -> float:
        if high <= low:
            return rect.center().y()
        ratio = (price - low) / (high - low)
        return rect.bottom() - ratio * rect.height()

    def _draw_price_grid(
        self, painter: QPainter, rect: QRectF, low: float, high: float
    ) -> None:
        step = self._nice_step(high - low, 6)
        start = math.floor(low / step) * step
        painter.setFont(QFont(FONT_FAMILY_MONO, 8))
        value = start
        while value <= high + step * 0.5:
            if low <= value <= high:
                y = self._price_to_y(value, rect, low, high)
                painter.setPen(QPen(QColor(PALETTE["border"]), 1, Qt.PenStyle.SolidLine))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                painter.setPen(QColor(PALETTE["text_muted"]))
                text = _fmt_price(value, step)
                label_y = max(2.0, min(y - 8.0, self.height() - 18.0))
                painter.drawText(
                    QRectF(rect.right() + 6, label_y, 62, 16),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    text,
                )
            value += step

        painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1))
        painter.drawRect(rect)


    def _draw_time_axis(
        self,
        painter: QPainter,
        price_rect: QRectF,
        z_rect: QRectF | None,
        first: int,
        last: int,
    ) -> None:
        axis_y = (z_rect.bottom() if z_rect is not None else price_rect.bottom())
        painter.setFont(QFont(FONT_FAMILY_MONO, 8))
        painter.setPen(QColor(PALETTE["text_muted"]))
        ticks = max(3, min(8, int(price_rect.width() / 120)))
        indexes = sorted(
            {
                int(round(first + (last - first) * i / max(1, ticks - 1)))
                for i in range(ticks)
            }
        )
        for index in indexes:
            if index < first or index > last:
                continue
            x = self._x_for_index(index, price_rect)
            painter.setPen(QPen(QColor(PALETTE["border"]), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(x, price_rect.top()), QPointF(x, axis_y))
            text = self._format_time(self._bars[index].open_time_ms)
            painter.setPen(QColor(PALETTE["text_muted"]))
            label_left = max(0.0, min(x - 46.0, self.width() - 92.0))
            painter.drawText(
                QRectF(label_left, axis_y + 4, 92, 16),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                text,
            )

    def _draw_anomaly_bands(
        self,
        painter: QPainter,
        price_rect: QRectF,
        z_rect: QRectF | None,
        first: int,
        last: int,
    ) -> None:
        slot = self._slot_width(price_rect)
        for index in range(first, last + 1):
            bar = self._bars[index]
            if not self._is_bar_marked(bar):
                continue
            x = self._x_for_index(index, price_rect)
            width = max(2.0, slot * 0.82)
            color = QColor(bar.color or PALETTE["accent"])
            color.setAlpha(ANOMALY_ALPHA)
            painter.fillRect(QRectF(x - width / 2, price_rect.top(), width, price_rect.height()), color)

            if bar.is_absorption:
                pen = QPen(QColor(ABSORPTION_COLOR), 1.6, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.drawRect(
                    QRectF(x - width / 2, price_rect.top(), width, price_rect.height())
                )
            if bar.is_live:
                pen = QPen(QColor(PALETTE["text"]), 1.2, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawRect(
                    QRectF(x - width / 2, price_rect.top(), width, price_rect.height())
                )
            if z_rect is not None:
                z_color = QColor(bar.color or PALETTE["accent"])
                z_color.setAlpha(22)
                painter.fillRect(
                    QRectF(x - width / 2, z_rect.top(), width, z_rect.height()), z_color
                )

    def _draw_candles(
        self,
        painter: QPainter,
        rect: QRectF,
        first: int,
        last: int,
        low: float,
        high: float,
        span: float,
    ) -> None:
        slot = self._slot_width(rect)
        body_w = max(1.0, min(12.0, slot * 0.62))
        for index in range(first, last + 1):
            bar = self._bars[index]
            x = self._x_for_index(index, rect)
            y_open = self._price_to_y(bar.open, rect, low, high)
            y_close = self._price_to_y(bar.close, rect, low, high)
            y_high = self._price_to_y(bar.high, rect, low, high)
            y_low = self._price_to_y(bar.low, rect, low, high)
            up = bar.close >= bar.open
            color = QColor(PALETTE["ok"] if up else PALETTE["err"])

            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, y_high), QPointF(x, y_low))

            top = min(y_open, y_close)
            height = max(1.0, abs(y_close - y_open))
            body = QRectF(x - body_w / 2, top, body_w, height)
            fill = QColor(color)
            fill.setAlpha(230)
            painter.fillRect(body, fill)
            painter.setPen(QPen(color, 1))
            painter.drawRect(body)

        if last >= first and self._bars[last].is_live:
            x = self._x_for_index(last, rect)
            painter.setFont(QFont(FONT_FAMILY_MONO, 8, QFont.Weight.Bold))
            painter.setPen(QColor(PALETTE["text"]))
            painter.drawText(
                QRectF(x - 24, rect.top() + 2, 60, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "LIVE",
            )


    def _draw_markers(
        self,
        painter: QPainter,
        rect: QRectF,
        first: int,
        last: int,
        low: float,
        high: float,
        span: float,
    ) -> None:
        font = QFont(FONT_FAMILY_MONO, 8, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        last_right: dict[int, float] = {}

        for index in range(first, last + 1):
            bar = self._bars[index]
            if not self._is_bar_marked(bar):
                continue
            text = regime_badge_text(bar.regime_key, bar.regime_label, bar.is_absorption)
            if not text:
                continue
            width = metrics.horizontalAdvance(text) + 10
            height = 15.0
            x = self._x_for_index(index, rect)
            level = 0
            while level < 4 and x - width / 2 < last_right.get(level, -1e9) + 3:
                level += 1
            if level >= 4:
                level = 3
            last_right[level] = x + width / 2

            y_high = self._price_to_y(bar.high, rect, low, high)
            badge_y = y_high - height - 4 - level * (height + 2)
            if badge_y < rect.top() + 1:
                y_low = self._price_to_y(bar.low, rect, low, high)
                badge_y = y_low + 4 + level * (height + 2)
            badge_y = min(max(rect.top() + 1, badge_y), rect.bottom() - height - 1)

            badge = QRectF(x - width / 2, badge_y, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            fill = QColor(bar.color or PALETTE["accent"])
            painter.setBrush(fill)
            painter.drawRoundedRect(badge, 4, 4)
            if bar.is_absorption:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(ABSORPTION_COLOR), 1.6))
                painter.drawRoundedRect(badge.adjusted(-1, -1, 1, 1), 4, 4)
            if bar.is_live:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#FFFFFF"), 1, Qt.PenStyle.DashLine))
                painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                badge,
                Qt.AlignmentFlag.AlignCenter,
                text,
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _z_soft_cap(threshold: float) -> float:
        """Визуальный предел шкалы Y для подграфика Z-score."""
        return min(Z_Y_MAX_CEIL, max(Z_Y_MAX_FLOOR, float(threshold) * Z_Y_MAX_FACTOR))

    def _draw_zscore(
        self, painter: QPainter, rect: QRectF, first: int, last: int
    ) -> None:
        painter.fillRect(rect, QColor(PALETTE["surface_alt"]))
        painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1))
        painter.drawRect(rect)

        gap = 6.0
        track_h = (rect.height() - gap) / 2.0
        tracks = [
            (QRectF(rect.left(), rect.top(), rect.width(), track_h), self._threshold_oi, "Z ΔOI", "doi"),
            (
                QRectF(rect.left(), rect.top() + track_h + gap, rect.width(), track_h),
                self._threshold_delta,
                "Z FutΔ",
                "fut",
            ),
        ]
        slot = self._slot_width(rect)
        bar_w = max(2.0, min(9.0, slot * 0.62))

        for track, threshold, label, key in tracks:
            y_max = self._z_soft_cap(threshold)
            zero_y = track.center().y()
            half = max(6.0, track.height() / 2.0 - 4.0)

            painter.setFont(QFont(FONT_FAMILY_MONO, 8))
            painter.setPen(QColor(PALETTE["text_secondary"]))
            painter.drawText(
                QRectF(track.left() + 4, track.top() + 1, 110, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                label,
            )

            painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1))
            painter.drawLine(QPointF(track.left(), zero_y), QPointF(track.right(), zero_y))

            upper_y = zero_y - (threshold / y_max) * half
            lower_y = zero_y + (threshold / y_max) * half
            painter.setPen(QPen(QColor("#94A3B8"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(track.left(), upper_y), QPointF(track.right(), upper_y))
            painter.drawLine(QPointF(track.left(), lower_y), QPointF(track.right(), lower_y))

            painter.setFont(QFont(FONT_FAMILY_MONO, 7))
            painter.setPen(QColor(PALETTE["text_muted"]))
            labels = [
                (upper_y, f"+{threshold:.1f}"),
                (zero_y, "0"),
                (lower_y, f"-{threshold:.1f}"),
            ]
            last_label_y: float | None = None
            for label_y, text in sorted(labels, key=lambda item: item[0]):
                if last_label_y is not None and label_y - last_label_y < 11.0:
                    continue
                painter.drawText(
                    QRectF(track.right() + 4, label_y - 7, 64, 14),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    text,
                )
                last_label_y = label_y

            for index in range(first, last + 1):
                bar = self._bars[index]
                value = bar.z_doi if key == "doi" else bar.z_fut_delta
                if value is None or not math.isfinite(value):
                    continue
                x = self._x_for_index(index, rect)
                breaking = abs(value) >= threshold
                clamped = max(-y_max, min(y_max, value))
                y = zero_y - (clamped / y_max) * half
                if breaking:
                    color = QColor(Z_BAR_HIGH_POSITIVE if value >= 0 else Z_BAR_HIGH_NEGATIVE)
                    color.setAlpha(238)
                else:
                    color = QColor(Z_BAR_LOW_POSITIVE if value >= 0 else Z_BAR_LOW_NEGATIVE)
                    color.setAlpha(175)
                painter.fillRect(
                    QRectF(x - bar_w / 2, min(y, zero_y), bar_w, max(1.5, abs(y - zero_y))),
                    color,
                )
                if abs(value) > y_max:
                    painter.setBrush(color)
                    painter.setPen(QPen(QColor("#FFFFFF"), 1.2))
                    painter.drawEllipse(QPointF(x, y), 3.0, 3.0)
                    painter.setBrush(Qt.BrushStyle.NoBrush)


    def _draw_flash(self, painter: QPainter, price_rect: QRectF, z_rect: QRectF | None) -> None:
        if self._flash_index is None or self._flash_index < 0 or self._flash_index >= len(self._bars):
            return
        x = self._x_for_index(self._flash_index, price_rect)
        slot = self._slot_width(price_rect)
        top = price_rect.top()
        bottom = (z_rect.bottom() if z_rect is not None else price_rect.bottom())
        color = QColor(PALETTE["accent"])
        color.setAlpha(45)
        painter.fillRect(QRectF(x - slot / 2, top, slot, bottom - top), color)
        painter.setPen(QPen(QColor(PALETTE["accent"]), 1.5))
        painter.drawRect(QRectF(x - slot / 2, top, slot, bottom - top))

    def _draw_crosshair(
        self,
        painter: QPainter,
        price_rect: QRectF,
        z_rect: QRectF | None,
        low: float,
        high: float,
    ) -> None:
        if self._hover_index is None or not (0 <= self._hover_index < len(self._bars)):
            return
        if self._hover_pos is None:
            return
        bar = self._bars[self._hover_index]
        x = self._x_for_index(self._hover_index, price_rect)
        bottom = z_rect.bottom() if z_rect is not None else price_rect.bottom()
        painter.setPen(QPen(QColor("#94A3B8"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, price_rect.top()), QPointF(x, bottom))

        y = self._hover_pos.y()
        if price_rect.top() <= y <= price_rect.bottom():
            painter.drawLine(QPointF(price_rect.left(), y), QPointF(price_rect.right(), y))
            ratio = (price_rect.bottom() - y) / max(1.0, price_rect.height())
            price = low + ratio * (high - low)
            tag = QRectF(price_rect.right() + 2, y - 8, 68, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(PALETTE["text"]))
            painter.drawRoundedRect(tag, 3, 3)
            painter.setPen(QColor("#F8FAFC"))
            painter.setFont(QFont(FONT_FAMILY_MONO, 8))
            painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, _fmt_price(price, (high - low) / 5))

        time_text = self._format_datetime(bar.open_time_ms)
        width = 132
        tag = QRectF(x - width / 2, bottom + 2, width, 16)
        tag.moveLeft(max(price_rect.left(), min(tag.left(), self.width() - width - 2)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["text"]))
        painter.drawRoundedRect(tag, 3, 3)
        painter.setPen(QColor("#F8FAFC"))
        painter.setFont(QFont(FONT_FAMILY_MONO, 8))
        painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, time_text)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    # ==================== TOOLTIP ====================

    def _tooltip_html(self, bar: CandleBar) -> str:
        params = self._params
        base = self._bundle.base_asset if self._bundle is not None else ""
        regime_color = bar.color or PALETTE["text_muted"]
        regime = bar.regime_label or "Обычная свеча"
        live = ' <span style="color:#FBBF24;">LIVE</span>' if bar.is_live else ""
        est = (
            ' <span style="color:#FBBF24;">(старт OI оценочный)</span>'
            if bar.live_start_estimated
            else ""
        )
        rows = [
            f'<b style="color:#F8FAFC;">{self._format_datetime(bar.open_time_ms)}</b>'
            f' <span style="color:#94A3B8;">{params.timeframe if params else ""}</span>{live}{est}',
        ]
        badge = regime_badge_text(bar.regime_key, bar.regime_label, bar.is_absorption)
        if badge:
            rows.append(
                f'<span style="color:{regime_color};"><b>{badge}</b> '
                f'<span style="color:#94A3B8;">{regime}</span></span>'
            )
        else:
            rows.append(f'<span style="color:#94A3B8;">{regime}</span>')
        base_suffix = f" {base}" if base else ""
        if bar.delta_oi_total is not None:
            rows.append(
                f'ΔOI: <b>{_fmt_num(bar.delta_oi_total)}{base_suffix}</b> '
                f'<span style="color:#94A3B8;">({_fmt_z(bar.z_doi)})</span>'
            )
            rows.append(
                f'<span style="color:#94A3B8;">Binance: '
                f'{_fmt_num(bar.delta_oi_binance)}{base_suffix} · Bybit: '
                f'{_fmt_num(bar.delta_oi_bybit)}{base_suffix}</span>'
            )
        else:
            rows.append('<span style="color:#94A3B8;">ΔOI: нет данных</span>')
        rows.append(
            f'Фьюч. дельта: <b>{_fmt_num(bar.fut_delta)}{base_suffix}</b> '
            f'<span style="color:#94A3B8;">({_fmt_z(bar.z_fut_delta)})</span>'
        )
        if bar.spot_available:
            status_map = {
                SPOT_STATUS_SYNC: '<span style="color:#34D399;">✓ Подтверждение</span>',
                SPOT_STATUS_DIV: '<span style="color:#FBBF24;">⚡ Дивергенция</span>',
            }
            status = status_map.get(bar.spot_status, '<span style="color:#94A3B8;">нейтрально</span>')
            rows.append(
                f'Спот дельта: <b>{_fmt_num(bar.spot_delta)}{base_suffix}</b> '
                f'<span style="color:#94A3B8;">({_fmt_z(bar.z_spot_delta)})</span> {status}'
            )
        else:
            rows.append('<span style="color:#94A3B8;">Спот: недоступен</span>')
        if bar.is_absorption:
            rows.append(
                f'<span style="color:#FBBF24;">⚠ Поглощение: {bar.absorption_reason or "да"}</span>'
            )
        else:
            rows.append('<span style="color:#94A3B8;">Поглощение: нет</span>')
        return "<br>".join(rows)

    def _update_tooltip(self) -> None:
        if self._hover_index is None or not (0 <= self._hover_index < len(self._bars)):
            self._tooltip.hide()
            return
        bar = self._bars[self._hover_index]
        self._tooltip_label.setText(self._tooltip_html(bar))
        self._tooltip.adjustSize()
        self._position_tooltip()
        self._tooltip.show()
        self._tooltip.raise_()

    def _position_tooltip(self) -> None:
        if self._hover_pos is None or not self._tooltip.isVisible():
            return
        x = int(self._hover_pos.x() + 14)
        y = int(self._hover_pos.y() + 14)
        x = max(4, min(x, self.width() - self._tooltip.width() - 4))
        y = max(4, min(y, self.height() - self._tooltip.height() - 4))
        self._tooltip.move(x, y)


    # ==================== ИНТЕРАКТИВНОСТЬ ====================

    def _clear_flash(self) -> None:
        self._flash_index = None
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        if not self._bars:
            return
        price_rect, _ = self._rects()
        if not price_rect.contains(event.position()):
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 0.82 if delta > 0 else 1.22
        old_count = self._visible_count
        new_count = max(
            float(MIN_VIEW_BARS),
            min(float(len(self._bars)), old_count * factor),
        )
        if abs(new_count - old_count) < 0.01:
            return

        start, _ = self._view_start_end()
        fraction = (event.position().x() - price_rect.left()) / max(1.0, price_rect.width())
        anchor_index = start + fraction * old_count
        new_start = anchor_index - fraction * new_count
        new_end = new_start + new_count - 1
        self._visible_count = new_count
        self._anchor_from_end = (len(self._bars) - 1) - new_end
        self._clamp_view()
        self._update_hover_from_pos(event.position())
        self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self._bars:
            price_rect, _ = self._rects()
            if price_rect.contains(event.position()):
                self._panning = True
                self._pan_start_x = event.position().x()
                self._pan_anchor = self._anchor_from_end
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._tooltip.hide()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._panning:
            price_rect, _ = self._rects()
            slot = self._slot_width(price_rect)
            delta_bars = (event.position().x() - self._pan_start_x) / max(1.0, slot)
            self._anchor_from_end = self._pan_anchor + delta_bars
            self._clamp_view()
            self._tooltip.hide()
            self.update()
            event.accept()
            return
        self._update_hover_from_pos(event.position())
        event.accept()

    def _update_hover_from_pos(self, pos: QPointF) -> None:
        price_rect, z_rect = self._rects()
        in_chart = price_rect.contains(pos) or (
            z_rect is not None and z_rect.contains(pos)
        )
        if not in_chart:
            self._hover_index = None
            self._hover_pos = None
            self._tooltip.hide()
            self.update()
            return
        index = self._index_at_x(pos.x(), price_rect)
        changed = index != self._hover_index
        self._hover_index = index
        self._hover_pos = pos
        if changed:
            self._update_tooltip()
        else:
            self._position_tooltip()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._hover_index = None
        self._hover_pos = None
        self._panning = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._tooltip.hide()
        self.update()
        super().leaveEvent(event)

