"""Правая лента аномалий OI & Delta Regime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
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
    SPOT_STATUS_SYNC,
    AnomalyEvent,
)
from app.tools.oi_regime.labels import regime_badge_text

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
            live_lbl = QLabel("LIVE")
            live_lbl.setObjectName("OiPill")
            live_lbl.setProperty("variant", "live")
            top.addWidget(live_lbl)

        top.addStretch(1)
        body.addLayout(top)

        strength = QLabel(
            f"OI {_fmt_z_signed(event.z_doi)} · Δ {_fmt_z_signed(event.z_fut_delta)}"
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
            absorption = QLabel("⚠ Поглощение")
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
        pills.addStretch(1)
        body.addLayout(pills)

        root.addLayout(body, 1)

        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._event.open_time_ms)
            event.accept()
            return
        super().mousePressEvent(event)


class AnomalyFeed(QWidget):
    """Скроллируемая лента аномалий (свежие сверху)."""

    event_clicked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tz_offset = 0
        self._signature: tuple[tuple[int, str, bool, str, float, float], ...] = ()
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
                round(event.z_doi, 2),
                round(event.z_fut_delta, 2),
            )
            for event in limited
        )
        if signature == self._signature:
            return
        self._signature = signature

        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not limited:
            empty = QLabel("Аномалий не найдено")
            empty.setObjectName("SettingsHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_lay.addWidget(empty)
        else:
            for event in limited:
                card = AnomalyCard(event, self._tz_offset)
                card.clicked.connect(self.event_clicked.emit)
                self._content_lay.addWidget(card)
        self._content_lay.addStretch(1)
        self._scroll.verticalScrollBar().setValue(0)

