"""Правая лента аномалий OI & Delta Regime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.oi_regime.models import (
    SPOT_STATUS_DIV,
    SPOT_STATUS_PASSIVE,
    SPOT_STATUS_SYNC,
    AnomalyEvent,
)
from app.tools.oi_regime.labels import regime_badge_text
from app.ui.widgets import repolish

MAX_FEED_CARDS = 50


def _fmt_z_signed(value: float, digits: int = 1) -> str:
    """Z-score со знаком и единицей σ."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number or number in (float("inf"), float("-inf")):
        return "—"
    return f"{number:+.{digits}f}σ"


def _event_time(event: AnomalyEvent, tz_offset_hours: int) -> str:
    dt = datetime.fromtimestamp(event.open_time_ms / 1000.0, tz=timezone.utc) + timedelta(
        hours=tz_offset_hours
    )
    return dt.strftime("%d.%m %H:%M")


class AnomalyCard(QFrame):
    """Кликабельная карточка одного события."""

    clicked = Signal(object)

    def __init__(
        self,
        event: AnomalyEvent,
        tz_offset_hours: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._event = event
        self.setObjectName("OiFeedCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Нажмите, чтобы перейти к свече на графике")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 10, 0)
        root.setSpacing(9)

        strip = QFrame()
        strip.setFixedWidth(4)
        strip.setStyleSheet(
            f"background-color: {event.color}; border-radius: 2px; border: none;"
        )
        root.addWidget(strip)

        body = QVBoxLayout()
        body.setContentsMargins(0, 9, 0, 9)
        body.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        time_lbl = QLabel(_event_time(event, tz_offset_hours))
        time_lbl.setObjectName("OiFeedTime")
        top.addWidget(time_lbl)

        if event.is_live:
            progress = max(0, min(100, int(round(event.live_progress * 100.0))))
            live_lbl = QLabel(f"LIVE {progress}%")
            live_lbl.setObjectName("OiPill")
            live_lbl.setProperty("variant", "live")
            top.addWidget(live_lbl)

        top.addStretch(1)
        body.addLayout(top)

        pct_suffix = ""
        if event.delta_oi_pct is not None:
            pct_suffix = f" · ΔOI {event.delta_oi_pct:+.2f}%"
        strength = QLabel(
            f"OI {_fmt_z_signed(event.z_doi)} · Δ {_fmt_z_signed(event.z_fut_delta)}"
            f"{pct_suffix}"
        )
        strength.setObjectName("OiFeedStrength")

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(6)

        badge_text = regime_badge_text(
            event.regime_key, event.regime_label, event.is_absorption
        )
        badge = QLabel(badge_text)
        badge.setObjectName("OiRegimeBadge")
        badge.setStyleSheet(
            f"background-color: {event.color}; color: #FFFFFF; border: none;"
        )
        badge.setToolTip(event.regime_label)
        middle.addWidget(badge)
        middle.addStretch(1)
        middle.addWidget(strength)
        body.addLayout(middle)

        pills = QHBoxLayout()
        pills.setContentsMargins(0, 0, 0, 0)
        pills.setSpacing(5)
        if event.is_absorption:
            absorption = QLabel(
                "⚠ Поглощение без OI" if event.is_standalone_absorption else "⚠ Поглощение"
            )
            absorption.setObjectName("OiPill")
            absorption.setProperty("variant", "warn")
            if event.absorption_reason:
                absorption.setToolTip(event.absorption_reason)
            pills.addWidget(absorption)
        if event.spot_status == SPOT_STATUS_SYNC:
            sync = QLabel("✓ Спот")
            sync.setObjectName("OiPill")
            sync.setProperty("variant", "ok")
            pills.addWidget(sync)
        elif event.spot_status == SPOT_STATUS_DIV:
            div = QLabel("⚡ Диверг.")
            div.setObjectName("OiPill")
            div.setProperty("variant", "div")
            pills.addWidget(div)
        elif event.spot_status == SPOT_STATUS_PASSIVE:
            passive = QLabel("○ Слабый спот")
            passive.setObjectName("OiPill")
            passive.setProperty("variant", "passive")
            passive.setToolTip("Спот пассивен при сильном изменении OI")
            pills.addWidget(passive)
        if event.is_single_exchange_led and event.dominant_exchange:
            led_label = {
                "bybit": "Bybit-led",
                "okx": "OKX-led",
                "binance": "Binance-led",
            }.get(event.dominant_exchange, f"{event.dominant_exchange}-led")
            led = QLabel(led_label)
            led.setObjectName("OiPill")
            share_text = (
                f" ({event.dominant_share * 100.0:.0f}%)"
                if event.dominant_share is not None
                else ""
            )
            led.setToolTip(
                f"Основной прирост/сброс OI прошёл на "
                f"{event.dominant_exchange.upper()}{share_text}"
            )
            pills.addWidget(led)
        pills.addStretch(1)
        body.addLayout(pills)

        root.addLayout(body, 1)

        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def event(self) -> AnomalyEvent:
        return self._event

    def set_selected(self, selected: bool) -> None:
        """Визуальное состояние выбранной карточки в ленте."""
        if bool(self.property("selected")) == bool(selected):
            return
        self.setProperty("selected", bool(selected))
        repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._event.open_time_ms)
            event.accept()
            return
        super().mousePressEvent(event)


class AnomalyFeed(QWidget):
    """Скроллируемая лента аномалий (свежие сверху) с клавиатурной навигацией."""

    event_clicked = Signal(object)
    selection_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tz_offset = 0
        self._signature: tuple[tuple[object, ...], ...] | None = None
        self._events: list[AnomalyEvent] = []
        self._cards: list[AnomalyCard] = []
        self._selected_index: int | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("OiFeedHeader")
        head_lay = QHBoxLayout(header)
        head_lay.setContentsMargins(14, 10, 14, 10)
        head_lay.setSpacing(8)

        title = QLabel("Лента аномалий")
        title.setObjectName("OiPaneTitle")

        self._count_lbl = QLabel("0")
        self._count_lbl.setObjectName("OiFeedCount")

        head_lay.addWidget(title)
        head_lay.addStretch(1)
        head_lay.addWidget(self._count_lbl)
        root.addWidget(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setObjectName("OiFeedScroll")

        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(10, 10, 10, 10)
        self._content_lay.setSpacing(8)
        self._content_lay.addStretch(1)

        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

    def set_tz_offset(self, hours: int) -> None:
        self._tz_offset = int(hours)

    def set_events(
        self,
        events: list[AnomalyEvent],
        total_count: int | None = None,
    ) -> None:
        limited = events[:MAX_FEED_CARDS]
        total = len(limited) if total_count is None else max(len(limited), int(total_count))
        self._count_lbl.setText(
            f"{len(limited)} / {total}" if total > len(limited) else str(len(limited))
        )
        signature = tuple(
            (
                event.open_time_ms,
                event.regime_key,
                event.is_absorption,
                event.spot_status,
                event.dominant_exchange,
                event.is_single_exchange_led,
                event.is_standalone_absorption,
                round(event.live_progress, 2),
                round(event.z_doi, 2),
                round(event.z_fut_delta, 2),
            )
            for event in limited
        )
        if signature == self._signature:
            return
        self._signature = signature

        keep_ts: int | None = None
        if (
            self._selected_index is not None
            and 0 <= self._selected_index < len(self._events)
        ):
            keep_ts = self._events[self._selected_index].open_time_ms

        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._events = list(limited)
        self._cards = []
        self._selected_index = None
        if not limited:
            empty = QLabel("Аномалий не найдено")
            empty.setObjectName("SettingsHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_lay.addWidget(empty)
        else:
            for index, event in enumerate(limited):
                card = AnomalyCard(event, self._tz_offset)
                card.clicked.connect(
                    lambda _ts, i=index: self._activate_index(i)
                )
                self._content_lay.addWidget(card)
                self._cards.append(card)
        self._content_lay.addStretch(1)

        if keep_ts is not None:
            self.select_timestamp(keep_ts, emit=False)
        else:
            self._scroll.verticalScrollBar().setValue(0)

    # -------------------- ВЫДЕЛЕНИЕ И НАВИГАЦИЯ --------------------

    def _activate_index(self, index: int) -> None:
        self.select_index(index, emit=False)
        self.setFocus()
        if 0 <= index < len(self._events):
            ts = self._events[index].open_time_ms
            self.selection_changed.emit(ts)
            self.event_clicked.emit(ts)

    def select_index(self, index: int, *, emit: bool = True) -> None:
        """Программно выбирает карточку в ленте и прокручивает к ней."""
        if not self._cards:
            return
        index = max(0, min(int(index), len(self._cards) - 1))
        for position, card in enumerate(self._cards):
            card.set_selected(position == index)
        self._selected_index = index
        self._scroll.ensureWidgetVisible(self._cards[index], 0, 24)
        if emit and 0 <= index < len(self._events):
            self.event_clicked.emit(self._events[index].open_time_ms)

    def select_timestamp(self, ts_ms: object, *, emit: bool = False) -> None:
        """Выбирает карточку по open_time_ms (ближайшую)."""
        try:
            target = int(ts_ms)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if not self._events:
            return
        index = min(
            range(len(self._events)),
            key=lambda i: abs(self._events[i].open_time_ms - target),
        )
        self.select_index(index, emit=emit)

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if not self._cards:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if key == Qt.Key.Key_Up else 1
            base = self._selected_index if self._selected_index is not None else 0
            target = base + step
            if self._selected_index is None:
                target = 0
            self._activate_index(max(0, min(target, len(self._cards) - 1)))
            event.accept()
            return
        if key == Qt.Key.Key_Home:
            self._activate_index(0)
            event.accept()
            return
        if key == Qt.Key.Key_End:
            self._activate_index(len(self._cards) - 1)
            event.accept()
            return
        super().keyPressEvent(event)


