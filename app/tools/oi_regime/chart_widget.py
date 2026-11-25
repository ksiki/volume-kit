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
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.services.oi_regime.models import (
    SPOT_STATUS_DIV,
    SPOT_STATUS_PASSIVE,
    SPOT_STATUS_SYNC,
    CandleBar,
    MultiBarZone,
    RegimeBundle,
    RegimeParams,
)
from app.tools.oi_regime.labels import (
    REGIME_SHORT,
    multi_zone_short,
    multi_zone_title,
    regime_badge_text,
    regime_compact_text,
)
from app.ui.theme import FONT_FAMILY_MONO, FONT_FAMILY_UI, PALETTE

DEFAULT_VIEW_BARS = 110
MIN_VIEW_BARS = 20
ANOMALY_ALPHA_MIN = 24
ANOMALY_ALPHA_MAX = 68
ABSORPTION_COLOR = "#D97706"
STANDALONE_COLOR = "#B45309"
SUPPORT_COLOR = "#059669"
RESISTANCE_COLOR = "#DC2626"
MULTI_RIBBON_HEIGHT = 22.0
MULTI_RIBBON_FULL_LABEL_MIN = 52.0
MULTI_RIBBON_COMPACT_LABEL_MIN = 34.0

Z_Y_MAX_FLOOR = 4.5
Z_Y_MAX_CEIL = 6.0
Z_Y_MAX_FACTOR = 2.2
Z_BAR_LOW_POSITIVE = "#A5B4FC"
Z_BAR_LOW_NEGATIVE = "#93C5FD"
Z_BAR_HIGH_POSITIVE = "#059669"
Z_BAR_HIGH_NEGATIVE = "#DC2626"
Z_BAR_SPOT = "#D97706"


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


def _fmt_duration(ms: int) -> str:
    """Компактная человекочитаемая длительность для линейки измерений."""
    minutes = int(max(0, ms) // 60_000)
    if minutes >= 1440:
        return f"{minutes // 1440}д {minutes % 1440 // 60}ч"
    if minutes >= 60:
        return f"{minutes // 60}ч {minutes % 60}м"
    return f"{minutes}м"


def _plural_bars(count: int) -> str:
    """Склонение слова «свеча» для подсказок."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} свеча"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} свечи"
    return f"{count} свечей"


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
        self._show_multi_bar = False
        self._show_trap_levels = False
        self._show_spot_z = False
        self._show_standalone_absorption = False
        self._spot_track_available = False
        self._visible_count = float(DEFAULT_VIEW_BARS)
        self._anchor_from_end = 0.0
        self._hover_index: int | None = None
        self._hover_pos: QPointF | None = None
        self._panning = False
        self._pan_start_x = 0.0
        self._pan_anchor = 0.0
        self._flash_index: int | None = None
        self._selected_index: int | None = None
        self._selected_ts: int | None = None
        self._ribbon_rect: QRectF | None = None
        self._hover_zone: MultiBarZone | None = None
        self._measuring = False
        self._measure_start: QPointF | None = None
        self._measure_end: QPointF | None = None

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
        self._spot_track_available = bool(bundle.spot_available) and any(
            bar.z_spot_delta is not None for bar in self._bars
        )
        if old_key != new_key:
            self._selected_index = None
            self._selected_ts = None
        elif self._selected_ts is not None and self._bars:
            self._selected_index = min(
                range(len(self._bars)),
                key=lambda i: abs(self._bars[i].open_time_ms - int(self._selected_ts)),
            )
            self._selected_ts = self._bars[self._selected_index].open_time_ms
        if reset_view or old_key != new_key or self._visible_count <= 0:
            self.reset_view()
        else:
            self._visible_count = min(self._visible_count, float(max(1, len(self._bars))))
            self._clamp_view()
        self._hover_index = None
        self._hover_zone = None
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

    def set_overlays(
        self,
        *,
        show_multi_bar: bool,
        show_trap_levels: bool,
        show_spot_z: bool,
        show_standalone_absorption: bool,
    ) -> None:
        """Включает/выключает ненавязчивые оверлеи графика."""
        self._show_multi_bar = bool(show_multi_bar)
        self._show_trap_levels = bool(show_trap_levels)
        self._show_spot_z = bool(show_spot_z)
        self._show_standalone_absorption = bool(show_standalone_absorption)
        if not self._show_multi_bar:
            self._hover_zone = None
        self.update()

    def clear_selection(self) -> None:
        self._selected_index = None
        self._selected_ts = None
        self.update()

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

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
        self._selected_index = index
        self._selected_ts = self._bars[index].open_time_ms
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
        has_z = self._show_zscore and total >= 220
        gap = 8.0
        price_h = total * 0.72 if has_z else total
        ribbon_h = MULTI_RIBBON_HEIGHT if self._show_multi_bar and price_h > 80 else 0.0
        price_top = top + ribbon_h
        price_body_h = max(20.0, price_h - ribbon_h)
        price_rect = QRectF(left, price_top, right - left, price_body_h)
        ribbon_rect: QRectF | None = (
            QRectF(left, top, right - left, ribbon_h) if ribbon_h > 0 else None
        )
        z_rect: QRectF | None = None
        if has_z:
            z_top = price_top + price_body_h + gap
            z_rect = QRectF(left, z_top, right - left, max(20.0, bottom - z_top))
        self._ribbon_rect = ribbon_rect
        return price_rect, z_rect

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
        self._draw_oi_unavailable_zone(painter, price_rect, first)
        self._draw_anomaly_bands(painter, price_rect, z_rect, first, last, low, high)
        self._draw_trap_levels(painter, price_rect, first, last, low, high)
        self._draw_candles(painter, price_rect, first, last, low, high, span)
        self._draw_multi_zones_price(painter, price_rect, first, last, low, high)
        self._draw_markers(painter, price_rect, first, last, low, high, span)
        if self._ribbon_rect is not None:
            self._draw_multi_bar_ribbon(painter, self._ribbon_rect, first, last)
        if z_rect is not None:
            self._draw_zscore(painter, z_rect, first, last)
        self._draw_selection(painter, price_rect, z_rect, first, last)
        self._draw_flash(painter, price_rect, z_rect)
        self._draw_measure(painter, price_rect, z_rect, low, high)
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

    def _draw_oi_unavailable_zone(
        self, painter: QPainter, price_rect: QRectF, first: int
    ) -> None:
        """Едва заметная зона левее первого бара с доступной историей OI."""
        bundle = self._bundle
        if bundle is None or not bundle.oi_unavailable_before_index:
            return
        boundary = int(bundle.oi_unavailable_before_index)
        if boundary <= 0 or boundary >= len(self._bars) or boundary <= first:
            return
        x_boundary = self._x_for_index(boundary, price_rect)
        width = x_boundary - price_rect.left()
        if width <= 2:
            return
        fill = QColor(PALETTE["text_muted"])
        fill.setAlpha(16)
        painter.fillRect(
            QRectF(price_rect.left(), price_rect.top(), width, price_rect.height()), fill
        )
        painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1, Qt.PenStyle.DashLine))
        painter.drawLine(
            QPointF(x_boundary, price_rect.top()),
            QPointF(x_boundary, price_rect.bottom()),
        )
        painter.setFont(QFont(FONT_FAMILY_UI, 8))
        painter.setPen(QColor(PALETTE["text_muted"]))
        painter.drawText(
            QRectF(price_rect.left() + 6, price_rect.top() + 4, max(60.0, width - 8), 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Нет истории OI (>30д)",
        )

    def _draw_anomaly_bands(
        self,
        painter: QPainter,
        price_rect: QRectF,
        z_rect: QRectF | None,
        first: int,
        last: int,
        low: float,
        high: float,
    ) -> None:
        slot = self._slot_width(price_rect)
        threshold = max(0.5, float(self._threshold_oi))
        ceiling = max(6.0, threshold * 2.0)
        for index in range(first, last + 1):
            bar = self._bars[index]
            if not self._is_bar_marked(bar):
                continue
            x = self._x_for_index(index, price_rect)
            width = max(2.0, slot * 0.82)
            color = QColor(bar.color or PALETTE["accent"])
            strength = max(abs(bar.z_doi or 0.0), abs(bar.z_fut_delta or 0.0))
            ratio = max(0.0, min(1.0, (strength - threshold) / max(0.1, ceiling - threshold)))
            color.setAlpha(
                int(round(ANOMALY_ALPHA_MIN + (ANOMALY_ALPHA_MAX - ANOMALY_ALPHA_MIN) * ratio))
            )
            band = QRectF(x - width / 2, price_rect.top(), width, price_rect.height())
            painter.fillRect(band, color)

            if bar.is_absorption:
                # Аккуратная засечка у свечи + тонкий пунктир вместо жирной рамки.
                painter.setPen(QPen(QColor(ABSORPTION_COLOR), 1, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(band)
                y_ref = (
                    self._price_to_y(bar.high, price_rect, low, high)
                    if bar.absorption_side == "LONG_TRAP"
                    else self._price_to_y(bar.low, price_rect, low, high)
                )
                painter.fillRect(
                    QRectF(x - width / 2, y_ref - 1.5, width, 3.0),
                    QColor(ABSORPTION_COLOR),
                )
            if bar.is_live:
                pen = QPen(QColor(PALETTE["text"]), 1.2, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(band)
            if z_rect is not None:
                z_color = QColor(bar.color or PALETTE["accent"])
                z_color.setAlpha(22)
                painter.fillRect(
                    QRectF(x - width / 2, z_rect.top(), width, z_rect.height()), z_color
                )

    def _draw_trap_levels(
        self,
        painter: QPainter,
        rect: QRectF,
        first: int,
        last: int,
        low: float,
        high: float,
    ) -> None:
        """Полупрозрачные лучи уровней «застрявших» участников."""
        if not self._show_trap_levels or self._bundle is None:
            return
        levels = self._bundle.trap_levels
        if not levels:
            return
        painter.setFont(QFont(FONT_FAMILY_MONO, 7))
        for level in levels:
            if level.end_index < first or level.start_index > last:
                continue
            start = max(level.start_index, first)
            x1 = self._x_for_index(start, rect) - self._slot_width(rect) / 2.0
            x2 = (
                self._x_for_index(min(level.end_index, last), rect)
                + self._slot_width(rect) / 2.0
                if level.end_index <= last
                else rect.right()
            )
            if x2 <= x1:
                continue
            color = QColor(SUPPORT_COLOR if level.side == "SUPPORT" else RESISTANCE_COLOR)
            top_y = self._price_to_y(level.price_high, rect, low, high)
            bottom_y = self._price_to_y(level.price_low, rect, low, high)
            band_color = QColor(color)
            band_color.setAlpha(26)
            painter.fillRect(
                QRectF(x1, min(top_y, bottom_y), x2 - x1, max(2.0, abs(bottom_y - top_y))),
                band_color,
            )
            mid_y = self._price_to_y(level.price_mid, rect, low, high)
            painter.setPen(QPen(color, 1.1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x1, mid_y), QPointF(x2, mid_y))
            painter.setPen(QColor(color.red(), color.green(), color.blue(), 220))
            painter.drawText(
                QRectF(rect.right() + 6, mid_y - 7, 62, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                _fmt_price(level.price_mid, (high - low) / 5),
            )

    def _visible_zones(self, first: int, last: int) -> list[MultiBarZone]:
        """Зоны мульти-бар режимов, пересекающиеся с видимым окном."""
        if not self._show_multi_bar or self._bundle is None:
            return []
        return [
            zone
            for zone in self._bundle.multi_zones
            if zone.end_index >= first and zone.start_index <= last
        ]

    def _zone_span(
        self, zone: MultiBarZone, rect: QRectF, first: int, last: int
    ) -> tuple[float, float] | None:
        """Пиксельный диапазон зоны с обрезкой по видимому окну."""
        start = max(zone.start_index, first)
        end = min(zone.end_index, last)
        if start > end:
            return None
        slot = self._slot_width(rect)
        x1 = max(rect.left(), self._x_for_index(start, rect) - slot / 2.0)
        x2 = min(rect.right(), self._x_for_index(end, rect) + slot / 2.0)
        if x2 - x1 < 1.0:
            return None
        return x1, x2

    def _zone_for_index(self, index: int | None) -> MultiBarZone | None:
        """Зона, покрывающая бар (для hover-подсветки и тултипа)."""
        if index is None or not self._show_multi_bar or self._bundle is None:
            return None
        for zone in self._bundle.multi_zones:
            if zone.start_index <= index <= zone.end_index:
                return zone
        return None

    def _draw_multi_zones_price(
        self,
        painter: QPainter,
        rect: QRectF,
        first: int,
        last: int,
        low: float,
        high: float,
    ) -> None:
        """Спокойные границы зон и мягкая подсветка коридора при наведении."""
        zones = self._visible_zones(first, last)
        if not zones:
            return
        hovered = self._hover_zone

        for zone in zones:
            span = self._zone_span(zone, rect, first, last)
            if span is None:
                continue
            x1, x2 = span
            is_hovered = (
                hovered is not None
                and hovered.regime_key == zone.regime_key
                and hovered.start_index == zone.start_index
                and hovered.end_index == zone.end_index
            )
            if is_hovered:
                y_top = self._price_to_y(zone.price_high, rect, low, high)
                y_bottom = self._price_to_y(zone.price_low, rect, low, high)
                corridor = QRectF(
                    x1,
                    min(y_top, y_bottom),
                    max(1.0, x2 - x1),
                    max(1.0, abs(y_bottom - y_top)),
                ).intersected(rect)
                if corridor.width() > 1 and corridor.height() > 1:
                    fill = QColor(zone.color)
                    fill.setAlpha(20)
                    painter.fillRect(corridor, fill)
                    pen = QPen(QColor(zone.color), 1.2, Qt.PenStyle.DashLine)
                    pen.setCosmetic(True)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(corridor)
            else:
                color = QColor(zone.color)
                color.setAlpha(52)
                pen = QPen(color, 1, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLine(QPointF(x1, rect.top()), QPointF(x1, rect.bottom()))
                painter.drawLine(QPointF(x2, rect.top()), QPointF(x2, rect.bottom()))
        painter.setBrush(Qt.BrushStyle.NoBrush)


    def _draw_multi_bar_ribbon(
        self, painter: QPainter, rect: QRectF, first: int, last: int
    ) -> None:
        """Выделенная верхняя дорожка с цельными плашками многобарных зон."""
        painter.fillRect(rect, QColor(PALETTE["surface_alt"]))
        painter.setPen(QPen(QColor(PALETTE["border"]), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        painter.setFont(QFont(FONT_FAMILY_UI, 8))
        painter.setPen(QColor(PALETTE["text_muted"]))
        painter.drawText(
            QRectF(rect.left() + 6, rect.top(), 90, rect.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Мульти-бар",
        )

        for zone in self._visible_zones(first, last):
            span = self._zone_span(zone, rect, first, last)
            if span is None:
                continue
            x1, x2 = span
            width = x2 - x1
            is_hovered = (
                self._hover_zone is not None
                and self._hover_zone.regime_key == zone.regime_key
                and self._hover_zone.start_index == zone.start_index
                and self._hover_zone.end_index == zone.end_index
            )
            plaque = QRectF(
                x1 + 1.0, rect.top() + 3.0, max(2.0, width - 2.0), rect.height() - 6.0
            )
            color = QColor(zone.color)

            fill = QColor(color)
            fill.setAlpha(120 if is_hovered else 52)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(plaque, 4, 4)

            border = QColor(color)
            border.setAlpha(235 if is_hovered else 150)
            pen = QPen(border, 1.4 if is_hovered else 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(
                QPointF(plaque.left() + 3, plaque.top()),
                QPointF(plaque.right() - 3, plaque.top()),
            )
            painter.drawLine(
                QPointF(plaque.left(), plaque.top() + 2),
                QPointF(plaque.left(), plaque.bottom() - 2),
            )
            painter.drawLine(
                QPointF(plaque.right(), plaque.top() + 2),
                QPointF(plaque.right(), plaque.bottom() - 2),
            )

            label = ""
            if width >= MULTI_RIBBON_FULL_LABEL_MIN:
                label = f"{multi_zone_short(zone.regime_key)} · {zone.bar_count}б"
            elif width >= MULTI_RIBBON_COMPACT_LABEL_MIN:
                compact = REGIME_SHORT.get(zone.regime_key, "")
                if compact:
                    label = f"{compact} ×{zone.bar_count}"
            if label:
                text_color = QColor(zone.color)
                text_color.setAlpha(255)
                font_size = 7 if width < MULTI_RIBBON_FULL_LABEL_MIN else 8
                painter.setFont(
                    QFont(FONT_FAMILY_MONO, font_size, QFont.Weight.Bold)
                )
                painter.setPen(text_color)
                painter.drawText(
                    plaque.adjusted(3, 0, -3, 0),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_selection(
        self,
        painter: QPainter,
        price_rect: QRectF,
        z_rect: QRectF | None,
        first: int,
        last: int,
    ) -> None:
        """Тонкая вертикальная линия на выбранной из ленты свече."""
        if self._selected_index is None:
            return
        if not (0 <= self._selected_index < len(self._bars)):
            return
        if not (first <= self._selected_index <= last):
            return
        x = self._x_for_index(self._selected_index, price_rect)
        bottom = z_rect.bottom() if z_rect is not None else price_rect.bottom()
        pen = QPen(QColor(PALETTE["accent"]), 1.0, Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, price_rect.top()), QPointF(x, bottom))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["accent"]))
        painter.drawEllipse(QPointF(x, price_rect.top() + 3.0), 2.5, 2.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_measure(
        self,
        painter: QPainter,
        price_rect: QRectF,
        z_rect: QRectF | None,
        low: float,
        high: float,
    ) -> None:
        """Измерительная линейка (Shift + ЛКМ drag)."""
        if self._measure_start is None or self._measure_end is None:
            return
        start = self._measure_start
        end = self._measure_end
        x1, x2 = sorted((start.x(), end.x()))
        y1, y2 = sorted((start.y(), end.y()))
        rect = QRectF(x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1)).intersected(price_rect)
        if rect.width() <= 1 or rect.height() <= 1:
            return
        fill = QColor(PALETTE["accent"])
        fill.setAlpha(26)
        painter.fillRect(rect, fill)
        painter.setPen(QPen(QColor(PALETTE["accent"]), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        slot = self._slot_width(price_rect)
        bars = max(1, int(round(abs(end.x() - start.x()) / max(1.0, slot))))
        step_ms = self._bundle.step_ms if self._bundle is not None else 3_600_000
        price_start = low + (
            (price_rect.bottom() - start.y()) / max(1.0, price_rect.height())
        ) * (high - low)
        price_end = low + (
            (price_rect.bottom() - end.y()) / max(1.0, price_rect.height())
        ) * (high - low)
        delta = price_end - price_start
        pct = delta / price_start * 100.0 if price_start else 0.0
        text = (
            f"{pct:+.2f}% · {_fmt_num(delta, 2)} · "
            f"{bars} бар · {_fmt_duration(bars * step_ms)}"
        )
        painter.setFont(QFont(FONT_FAMILY_MONO, 8, QFont.Weight.Bold))
        metrics = QFontMetrics(painter.font())
        width = metrics.horizontalAdvance(text) + 14
        badge_x = min(
            max(price_rect.left() + 2, rect.center().x() - width / 2),
            price_rect.right() - width,
        )
        badge_y = max(price_rect.top() + 2, rect.top() - 20)
        badge = QRectF(badge_x, badge_y, width, 17)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["text"]))
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        painter.setBrush(Qt.BrushStyle.NoBrush)

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
            bar = self._bars[last]
            x = self._x_for_index(last, rect)
            painter.setFont(QFont(FONT_FAMILY_MONO, 8, QFont.Weight.Bold))
            painter.setPen(QColor(PALETTE["text"]))
            progress = max(0, min(100, int(round(bar.live_progress * 100.0))))
            painter.drawText(
                QRectF(x - 24, rect.top() + 2, 70, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"LIVE {progress}%",
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
        entries = [
            (index, self._bars[index])
            for index in range(first, last + 1)
            if self._is_bar_marked(self._bars[index])
        ]
        standalone = (
            [
                index
                for index in range(first, last + 1)
                if self._bars[index].is_standalone_absorption
                and (
                    not self._hide_weak_spot
                    or self._bars[index].spot_status
                    in (SPOT_STATUS_SYNC, SPOT_STATUS_DIV)
                )
            ]
            if self._show_standalone_absorption
            else []
        )
        if not entries and not standalone:
            return

        slot = self._slot_width(rect)
        compact = slot < 26.0
        if not compact and entries:
            probe_font = QFont(FONT_FAMILY_MONO, 8, QFont.Weight.Bold)
            probe_metrics = QFontMetrics(probe_font)
            probe_right: dict[int, float] = {}
            for index, bar in entries:
                text = regime_badge_text(bar.regime_key, bar.regime_label, bar.is_absorption)
                if not text:
                    continue
                width = probe_metrics.horizontalAdvance(text) + 10
                x = self._x_for_index(index, rect)
                level = 0
                while level < 4 and x - width / 2 < probe_right.get(level, -1e9) + 3:
                    level += 1
                if level >= 2:
                    compact = True
                    break
                probe_right[level] = x + width / 2

        font = QFont(FONT_FAMILY_MONO, 7 if compact else 8, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        height = 13.0 if compact else 15.0
        last_right: dict[int, float] = {}

        for index, bar in entries:
            if compact:
                text = regime_compact_text(bar.regime_key, bar.is_absorption)
            else:
                text = regime_badge_text(bar.regime_key, bar.regime_label, bar.is_absorption)
            if not text:
                continue
            width = metrics.horizontalAdvance(text) + (6 if compact else 10)
            x = self._x_for_index(index, rect)
            level = 0
            while level < 3 and x - width / 2 < last_right.get(level, -1e9) + 2:
                level += 1
            last_right[level] = x + width / 2

            y_high = self._price_to_y(bar.high, rect, low, high)
            badge_y = y_high - height - 3 - level * (height + 1)
            if badge_y < rect.top() + 1:
                y_low = self._price_to_y(bar.low, rect, low, high)
                badge_y = y_low + 2 + level * (height + 1)
            badge_y = min(max(rect.top() + 1, badge_y), rect.bottom() - height - 1)

            badge = QRectF(x - width / 2, badge_y, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            fill = QColor(bar.color or PALETTE["accent"])
            painter.setBrush(fill)
            painter.drawRoundedRect(badge, 3, 3)
            if bar.is_absorption:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(ABSORPTION_COLOR), 1.2, Qt.PenStyle.DotLine))
                painter.drawRoundedRect(badge.adjusted(-1, -1, 1, 1), 3, 3)
            if bar.is_live:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#FFFFFF"), 1, Qt.PenStyle.DashLine))
                painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

        if standalone:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(STANDALONE_COLOR))
            for index in standalone:
                bar = self._bars[index]
                x = self._x_for_index(index, rect)
                y = self._price_to_y(bar.high, rect, low, high) - 7.0
                y = max(rect.top() + 3.0, y)
                diamond = QPolygonF(
                    [
                        QPointF(x, y - 4.0),
                        QPointF(x + 4.0, y),
                        QPointF(x, y + 4.0),
                        QPointF(x - 4.0, y),
                    ]
                )
                painter.drawPolygon(diamond)

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

        show_spot = self._show_spot_z and self._spot_track_available
        track_count = 3 if show_spot else 2
        gap = 6.0
        track_h = (rect.height() - gap * (track_count - 1)) / track_count
        tracks: list[tuple[QRectF, float, str, str]] = [
            (
                QRectF(rect.left(), rect.top(), rect.width(), track_h),
                self._threshold_oi,
                "Z ΔOI",
                "doi",
            ),
            (
                QRectF(
                    rect.left(),
                    rect.top() + track_h + gap,
                    rect.width(),
                    track_h,
                ),
                self._threshold_delta,
                "Z FutΔ",
                "fut",
            ),
        ]
        if show_spot:
            spot_threshold = self._params.spot_sync_min_z if self._params is not None else 1.0
            tracks.append(
                (
                    QRectF(
                        rect.left(),
                        rect.top() + 2 * (track_h + gap),
                        rect.width(),
                        track_h,
                    ),
                    spot_threshold,
                    "Z SpotΔ",
                    "spot",
                )
            )
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
                if key == "doi":
                    value = bar.z_doi
                elif key == "fut":
                    value = bar.z_fut_delta
                else:
                    value = bar.z_spot_delta
                if value is None or not math.isfinite(value):
                    continue
                x = self._x_for_index(index, rect)
                breaking = abs(value) >= threshold
                clamped = max(-y_max, min(y_max, value))
                y = zero_y - (clamped / y_max) * half
                if key == "spot":
                    color = QColor(Z_BAR_SPOT)
                    color.setAlpha(235 if breaking else 160)
                elif breaking:
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
        live = ""
        if bar.is_live:
            progress = max(0, min(100, int(round(bar.live_progress * 100.0))))
            live = f' <span style="color:#FBBF24;">LIVE {progress}%</span>'
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
            pct_part = (
                f' · <b>{bar.delta_oi_pct:+.2f}%</b>'
                if bar.delta_oi_pct is not None
                else ""
            )
            bybit_led = (
                ' <span style="color:#F472B6;">(в основном Bybit)</span>'
                if bar.is_bybit_led
                else ""
            )
            rows.append(
                f'ΔOI: <b>{_fmt_num(bar.delta_oi_total)}{base_suffix}</b>{pct_part} '
                f'<span style="color:#94A3B8;">({_fmt_z(bar.z_doi)})</span>{bybit_led}'
            )
            share_part = ""
            if bar.bybit_oi_share is not None:
                share_part = f" · доля Bybit {bar.bybit_oi_share * 100.0:.0f}%"
            rows.append(
                f'<span style="color:#94A3B8;">Binance: '
                f'{_fmt_num(bar.delta_oi_binance)}{base_suffix} · Bybit: '
                f'{_fmt_num(bar.delta_oi_bybit)}{base_suffix}{share_part}</span>'
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
                SPOT_STATUS_PASSIVE: '<span style="color:#D97706;">○ Пассивный спот</span>',
            }
            status = status_map.get(bar.spot_status, '<span style="color:#94A3B8;">нейтрально</span>')
            rows.append(
                f'Спот дельта: <b>{_fmt_num(bar.spot_delta)}{base_suffix}</b> '
                f'<span style="color:#94A3B8;">({_fmt_z(bar.z_spot_delta)})</span> {status}'
            )
        else:
            rows.append('<span style="color:#94A3B8;">Спот: недоступен</span>')
        if bar.is_absorption:
            suffix = " (без всплеска OI)" if bar.is_standalone_absorption else ""
            rows.append(
                f'<span style="color:#FBBF24;">⚠ Поглощение{suffix}: '
                f'{bar.absorption_reason or "да"}</span>'
            )
        else:
            rows.append('<span style="color:#94A3B8;">Поглощение: нет</span>')
        if bar.basis_pct is not None or bar.funding_rate is not None:
            basis = f"{bar.basis_pct:+.3f}%" if bar.basis_pct is not None else "—"
            funding = (
                f"{bar.funding_rate:+.4f}%" if bar.funding_rate is not None else "—"
            )
            rows.append(
                f'<span style="color:#94A3B8;">Базис: {basis} · Фандинг: {funding}</span>'
            )
        zone = self._hover_zone
        if zone is None:
            zone = self._zone_for_index(self._hover_index)
        if zone is not None:
            rows.append(self._multi_zone_tooltip_html(zone))
        return "<br>".join(rows)

    def _zone_time_range(self, zone: MultiBarZone) -> str:
        """Диапазон времени зоны по границам свечей."""
        tz = self._params.tz_offset_hours if self._params is not None else 0
        start = datetime.fromtimestamp(zone.start_time_ms / 1000.0, tz=timezone.utc) + timedelta(
            hours=tz
        )
        end = datetime.fromtimestamp(
            (zone.end_time_ms + 1) / 1000.0, tz=timezone.utc
        ) + timedelta(hours=tz)
        if start.date() == end.date():
            return f"{start:%H:%M}–{end:%H:%M}"
        return f"{start:%d.%m %H:%M}–{end:%d.%m %H:%M}"

    def _multi_zone_tooltip_html(self, zone: MultiBarZone) -> str:
        """Понятный структурированный блок многобарной зоны для тултипа."""
        base = self._bundle.base_asset if self._bundle is not None else ""
        base_suffix = f" {base}" if base else ""
        title = multi_zone_title(zone.regime_key)
        span = self._zone_time_range(zone)
        header = (
            f'<span style="color:{zone.color};"><b>Мульти-бар '
            f"({_plural_bars(zone.bar_count)} · {span}): {title}</b></span>"
        )
        pct_part = (
            f" ({zone.sum_delta_oi_pct:+.2f}%)"
            if zone.sum_delta_oi_pct is not None
            else ""
        )
        body = (
            f'<span style="color:#CBD5E1;">ΣΔOI: '
            f"<b>{_fmt_num(zone.sum_delta_oi)}{base_suffix}</b>{pct_part} "
            f"(Z {_fmt_z(zone.peak_z_doi)}) · ΣΔ: "
            f"<b>{_fmt_num(zone.sum_fut_delta)}{base_suffix}</b> "
            f"(Z {_fmt_z(zone.peak_z_delta)})</span>"
        )
        return header + "<br>" + body

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
                if bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    self._measuring = True
                    self._measure_start = event.position()
                    self._measure_end = event.position()
                    self._tooltip.hide()
                    self.setCursor(Qt.CursorShape.CrossCursor)
                    self.update()
                    event.accept()
                    return
                self._measure_start = None
                self._measure_end = None
                self._selected_index = None
                self._selected_ts = None
                self._panning = True
                self._pan_start_x = event.position().x()
                self._pan_anchor = self._anchor_from_end
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._tooltip.hide()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._measuring:
            price_rect, _ = self._rects()
            pos = event.position()
            self._measure_end = QPointF(
                min(max(pos.x(), price_rect.left()), price_rect.right()),
                min(max(pos.y(), price_rect.top()), price_rect.bottom()),
            )
            self.update()
            event.accept()
            return
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
        ribbon_rect = self._ribbon_rect
        in_chart = (
            price_rect.contains(pos)
            or (z_rect is not None and z_rect.contains(pos))
            or (ribbon_rect is not None and ribbon_rect.contains(pos))
        )
        if not in_chart:
            self._hover_index = None
            self._hover_pos = None
            self._hover_zone = None
            self._tooltip.hide()
            self.update()
            return
        index = self._index_at_x(pos.x(), price_rect)
        zone = self._zone_for_index(index)
        changed = index != self._hover_index or zone != self._hover_zone
        self._hover_index = index
        self._hover_pos = pos
        self._hover_zone = zone
        if changed:
            self._update_tooltip()
        else:
            self._position_tooltip()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._measuring and event.button() == Qt.MouseButton.LeftButton:
            self._measuring = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            event.accept()
            return
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
        self._hover_zone = None
        self._panning = False
        self._measuring = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._tooltip.hide()
        self.update()
        super().leaveEvent(event)

