"""Страница Calibrator: две независимые карточки рынков и таблицы ТФ."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.config import ConfigService, get_config_service
from app.services.calibration import market_specs
from app.ui.widgets import ExchangeDot, TfBadge, copy_to_clipboard, repolish

# (заголовок, stretch, тип ячейки, вариант пилюли, ключ данных)
ColumnSpec = tuple[str, int, str, str | None, str | None]

SPOT_COLUMNS: tuple[ColumnSpec, ...] = (
    ("ТФ / ГЛУБИНА", 17, "tf", None, None),
    ("МАСШТАБ", 21, "pill", "amber", "scale"),
    ("MAXVOLUME (1%)", 21, "pill", "green", "vol_1"),
    ("MAXVOLUME (3%)", 21, "pill", "blue", "vol_3"),
    ("MAXDELTA (1%)", 20, "pill", "violet", "delta_1"),
)

FUTURES_COLUMNS: tuple[ColumnSpec, ...] = (
    ("ТФ / ГЛУБИНА", 20, "tf", None, None),
    ("МАСШТАБ", 22, "pill", "amber", "scale"),
    ("ШАГ ЦЕНЫ", 17, "value", None, "step_str"),
    ("ATR", 17, "value", None, "atr_str"),
    ("MAXVOLUME (3%)", 24, "pill", "blue", "vol_3"),
)

ROW_MARGINS = (20, 12, 20, 12)
HEADER_MARGINS = (20, 8, 20, 8)
COLUMN_SPACING = 14

_COPY_FEEDBACK_MS = 1000

_IGNORED_POLICY = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


def _ignored_wrap(widget: QWidget) -> QWidget:
    """Обёртка с горизонтальным Ignored: содержимое не диктует ширину колонки."""
    wrap = QWidget()
    wrap.setSizePolicy(_IGNORED_POLICY)
    wrap.setMinimumWidth(0)
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(widget)
    lay.addStretch(1)
    return wrap


class CopyPill(QPushButton):
    """Кликабельная пилюля: копирует значение и показывает галочку на ~1 секунду."""

    copied = Signal(str, str)

    def __init__(
        self,
        variant: str,
        copy_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("", parent)
        self.setObjectName("CopyPill")
        self.setProperty("variant", variant)
        self.setProperty("copied", False)
        self._value = ""
        self._copy_label = copy_label

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reset_feedback)

        self.clicked.connect(self._on_click)
        self.set_placeholder()
        repolish(self)

    def set_value(self, value: str) -> None:
        self._value = str(value)
        self.setEnabled(True)
        self.setToolTip(f"Нажмите, чтобы скопировать ({self._copy_label})")
        self._render_value()

    def set_placeholder(self) -> None:
        self._value = ""
        self.setText("—")
        self.setEnabled(False)
        self.setToolTip("")

    def _render_value(self) -> None:
        self.setText(f"{self._value}  ⧉")

    def _on_click(self) -> None:
        if not self._value:
            return
        copy_to_clipboard(self._value)
        self.copied.emit(self._copy_label, self._value)

        self.setText("✓")
        self.setProperty("copied", True)
        repolish(self)
        self._timer.start(_COPY_FEEDBACK_MS)

    def _reset_feedback(self) -> None:
        self.setProperty("copied", False)
        repolish(self)
        if self._value:
            self._render_value()


class CalibRow(QFrame):
    """Строка таблицы: один таймфрейм с бейджем, глубиной и метриками."""

    copied = Signal(str, str)

    def __init__(
        self,
        columns: tuple[ColumnSpec, ...],
        row_spec: dict[str, Any],
        is_last: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CalibTableRow")
        self.setProperty("last", is_last)
        self.setProperty("hovered", False)

        self._columns = columns
        self._timeframe = str(row_spec.get("timeframe", ""))
        self._days = int(row_spec.get("days", 0) or 0)
        self._half_life = int(row_spec.get("half_life_days", 0) or 0)

        self._pill_keys: list[str] = []
        self._pills: list[CopyPill] = []
        self._metas: list[QLabel] = []
        self._value_keys: list[str] = []
        self._values: list[QLabel] = []
        self._cells: list[QWidget] = []
        self._error_lbl: QLabel | None = None

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(*ROW_MARGINS)
        self._grid.setHorizontalSpacing(COLUMN_SPACING)
        self._grid.setVerticalSpacing(2)

        for index, (label, stretch, kind, variant, key) in enumerate(columns):
            self._grid.setColumnStretch(index, stretch)
            if kind == "tf":
                cell = self._build_tf_cell()
            elif kind == "pill":
                cell = self._build_pill_cell(label, variant or "blue", key or "")
            else:
                cell = self._build_value_cell(key or "")
            self._cells.append(cell)
            self._grid.addWidget(cell, 0, index, Qt.AlignmentFlag.AlignVCenter)

        self.set_placeholder()
        self._install_hover_tracking()
        repolish(self)

    # ==================== ПОСТРОЕНИЕ ЯЧЕЕК ====================

    def _build_tf_cell(self) -> QWidget:
        wrap = QWidget()
        wrap.setSizePolicy(_IGNORED_POLICY)
        wrap.setMinimumWidth(0)

        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self._tf_badge = TfBadge(self._timeframe, compact=True)
        self._depth_lbl = QLabel("")
        self._depth_lbl.setObjectName("CalibCellMeta")

        lay.addWidget(self._tf_badge, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._depth_lbl, 0, Qt.AlignmentFlag.AlignLeft)
        return wrap

    def _build_pill_cell(self, label: str, variant: str, key: str) -> QWidget:
        wrap = QWidget()
        wrap.setSizePolicy(_IGNORED_POLICY)
        wrap.setMinimumWidth(0)

        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        pill = CopyPill(variant, copy_label=f"{label} {self._timeframe}")
        pill.copied.connect(self.copied.emit)
        meta = QLabel("—")
        meta.setObjectName("CalibCellMeta")

        lay.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(meta, 0, Qt.AlignmentFlag.AlignLeft)

        self._pill_keys.append(key)
        self._pills.append(pill)
        self._metas.append(meta)
        return wrap

    def _build_value_cell(self, key: str) -> QWidget:
        wrap = QWidget()
        wrap.setSizePolicy(_IGNORED_POLICY)
        wrap.setMinimumWidth(0)

        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        value = QLabel("—")
        value.setObjectName("CalibCellValue")
        lay.addWidget(value, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)

        self._value_keys.append(key)
        self._values.append(value)
        return wrap


    # ==================== СОСТОЯНИЯ СТРОКИ ====================

    def set_placeholder(self) -> None:
        """Пустое состояние: аккуратные прочерки без нулей."""
        self._show_cells()
        self._depth_lbl.setText(f"{self._days}д • HL {self._half_life}д")
        self._depth_lbl.setToolTip("")
        for pill, meta in zip(self._pills, self._metas):
            pill.set_placeholder()
            meta.setText("—")
        for value in self._values:
            value.setText("—")

    def set_row_data(self, data: dict[str, Any]) -> None:
        error = data.get("error")
        if error:
            self.set_error(str(error))
            return

        self._show_cells()
        self._days = int(data.get("days", self._days) or self._days)
        self._half_life = int(data.get("half_life_days", self._half_life) or self._half_life)
        self._depth_lbl.setText(f"{self._days}д • HL {self._half_life}д")

        missing_days = int(data.get("missing_days", 0) or 0)
        if missing_days > 0:
            self._depth_lbl.setToolTip(
                f"В кэше отсутствует {missing_days} дн. из {self._days} — "
                "расчёт по доступным данным"
            )
        else:
            self._depth_lbl.setToolTip("")

        for index, key in enumerate(self._pill_keys):
            payload = data.get(key)
            if not isinstance(payload, dict) or not payload.get("value"):
                self._pills[index].set_placeholder()
                self._metas[index].setText("—")
                continue

            self._pills[index].set_value(str(payload["value"]))
            meta_text = str(payload.get("meta") or "")
            if not meta_text:
                parts = [str(payload[name]) for name in ("usd", "pos") if payload.get(name)]
                meta_text = " • ".join(parts)
            self._metas[index].setText(meta_text or "—")

        for index, key in enumerate(self._value_keys):
            value = data.get(key)
            self._values[index].setText(str(value) if value else "—")

    def set_error(self, message: str) -> None:
        """Компактный статус ошибки в строке; подробности — в тултипе и логе."""
        for cell in self._cells[1:]:
            cell.hide()

        if self._error_lbl is None:
            self._error_lbl = QLabel("")
            self._error_lbl.setObjectName("CalibRowError")
            self._error_lbl.setWordWrap(True)
            self._error_lbl.installEventFilter(self)
            self._grid.addWidget(
                self._error_lbl,
                0,
                1,
                1,
                max(1, len(self._columns) - 1),
                Qt.AlignmentFlag.AlignVCenter,
            )

        self._error_lbl.setText("⚠ Не удалось рассчитать — подробности в логе")
        self._error_lbl.setToolTip(message)
        self._error_lbl.show()

    def _show_cells(self) -> None:
        for cell in self._cells:
            cell.show()
        if self._error_lbl is not None:
            self._error_lbl.hide()

    def apply_column_widths(self, widths: list[int]) -> None:
        """Фиксирует ширины колонок, чтобы шапка и все строки совпадали пиксель-в-пиксель."""
        for index, width in enumerate(widths):
            self._grid.setColumnMinimumWidth(index, width)

    # ==================== HOVER-ПОДСВЕТКА СТРОКИ ====================

    def _install_hover_tracking(self) -> None:
        """QSS :hover на контейнере не срабатывает над дочерними виджетами."""
        for widget in (self, *self.findChildren(QWidget)):
            widget.installEventFilter(self)

    def _set_hovered(self, hovered: bool) -> None:
        if bool(self.property("hovered")) == hovered:
            return
        self.setProperty("hovered", hovered)
        repolish(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Enter:
            self._set_hovered(True)
        elif event.type() == QEvent.Type.Leave:
            if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
                self._set_hovered(False)
        return super().eventFilter(watched, event)


class MarketCard(QFrame):
    """Полноширинная карточка рынка с чистой табличной сеткой по таймфреймам."""

    copied = Signal(str, str)

    def __init__(
        self,
        spec: dict[str, Any],
        columns: tuple[ColumnSpec, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._columns = columns
        self._rows: dict[str, CalibRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header(spec))
        root.addWidget(self._build_table_header())

        row_specs = list(spec.get("rows") or [])
        last_index = len(row_specs) - 1
        for index, row_spec in enumerate(row_specs):
            row = CalibRow(columns, row_spec, is_last=index == last_index)
            row.copied.connect(self.copied.emit)
            self._rows[str(row_spec.get("timeframe", ""))] = row
            root.addWidget(row)

    def _build_header(self, spec: dict[str, Any]) -> QFrame:
        header = QFrame()
        header.setObjectName("CalibCardHeader")

        lay = QHBoxLayout(header)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(10)

        dot = ExchangeDot(str(spec.get("exchange_key", "")), size=9)
        title = QLabel(str(spec.get("title", "")))
        title.setObjectName("CalibMarketTitle")

        self._summary_lbl = QLabel(str(spec.get("summary", "")))
        self._summary_lbl.setObjectName("CalibMarketMeta")

        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        lay.addWidget(self._summary_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _build_table_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("CalibTableHeader")

        self._header_grid = QGridLayout(header)
        self._header_grid.setContentsMargins(*HEADER_MARGINS)
        self._header_grid.setHorizontalSpacing(COLUMN_SPACING)

        for index, (label, stretch, _kind, _variant, _key) in enumerate(self._columns):
            cell = QLabel(label)
            cell.setObjectName("TableHead")
            cell.setSizePolicy(_IGNORED_POLICY)
            cell.setMinimumWidth(0)
            self._header_grid.addWidget(cell, 0, index, Qt.AlignmentFlag.AlignVCenter)
            self._header_grid.setColumnStretch(index, stretch)

        return header

    # ==================== ДАННЫЕ И ГЕОМЕТРИЯ ====================

    def set_placeholder(self) -> None:
        for row in self._rows.values():
            row.set_placeholder()

    def set_market(self, market: dict[str, Any]) -> None:
        summary = str(market.get("summary") or "")
        if summary:
            self._summary_lbl.setText(summary)

        rows_by_tf = {
            str(row.get("timeframe", "")): row
            for row in market.get("rows") or []
            if isinstance(row, dict)
        }
        for timeframe, row in self._rows.items():
            data = rows_by_tf.get(timeframe)
            if data is None:
                row.set_placeholder()
            else:
                row.set_row_data(data)

    def _column_widths(self) -> list[int]:
        total = self.contentsRect().width()
        margins = ROW_MARGINS[0] + ROW_MARGINS[2]
        spacing = COLUMN_SPACING * max(0, len(self._columns) - 1)
        usable = max(0, total - margins - spacing)
        stretch_total = sum(column[1] for column in self._columns) or 1

        widths = [
            int(usable * column[1] / stretch_total)
            for column in self._columns[:-1]
        ]
        widths.append(max(0, usable - sum(widths)))
        return widths

    def _sync_column_widths(self) -> None:
        """Одинаковые ширины колонок для шапки и строк — без сдвигов сетки."""
        if not hasattr(self, "_header_grid"):
            return
        widths = self._column_widths()
        if not widths:
            return
        for index, width in enumerate(widths):
            self._header_grid.setColumnMinimumWidth(index, width)
        for row in self._rows.values():
            row.apply_column_widths(widths)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._sync_column_widths()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._sync_column_widths()


class CalibratorPage(QWidget):
    """Инструмент Calibrator: Binance Spot и ByBit Futures."""

    copied = Signal(str, str)
    recalc_requested = Signal()

    def __init__(
        self,
        config: ConfigService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config if config is not None else get_config_service()
        self._has_report = False
        self._cards: dict[str, MarketCard] = {}
        self._build_ui()
        self.set_idle()

    # ==================== ПОСТРОЕНИЕ ИНТЕРФЕЙСА ====================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        controls_wrap = QWidget()
        controls_lay = QVBoxLayout(controls_wrap)
        controls_lay.setContentsMargins(24, 18, 24, 0)
        controls_lay.addWidget(self._build_controls())
        root.addWidget(controls_wrap)

        banner_wrap = QWidget()
        banner_lay = QVBoxLayout(banner_wrap)
        banner_lay.setContentsMargins(24, 12, 24, 0)
        banner_lay.addWidget(self._build_error_banner())
        root.addWidget(banner_wrap)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        content = QVBoxLayout(container)
        content.setContentsMargins(24, 22, 24, 28)
        content.setSpacing(18)

        settings = self._config.current
        for spec in market_specs(settings):
            market_id = str(spec.get("id", ""))
            columns = SPOT_COLUMNS if market_id == "binance_spot" else FUTURES_COLUMNS
            card = MarketCard(spec, columns)
            card.copied.connect(self.copied.emit)
            self._cards[market_id] = card
            content.addWidget(card)

        content.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    def _build_controls(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("CalibControls")

        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("CalibStatusLabel")
        self._status_lbl.setProperty("status", "idle")
        repolish(self._status_lbl)

        self._progress = QProgressBar()
        self._progress.setObjectName("ThinProgress")
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        self._progress.setFixedWidth(180)
        self._progress.hide()

        self._recalc_btn = QPushButton("Пересчитать")
        self._recalc_btn.setObjectName("PrimaryBtn")
        self._recalc_btn.setFixedHeight(32)
        self._recalc_btn.setToolTip("Перечитать config.json и обновить расчёт Calibrator")
        self._recalc_btn.clicked.connect(self.recalc_requested.emit)

        lay.addWidget(self._status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        lay.addWidget(self._recalc_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _build_error_banner(self) -> QFrame:
        self._error_banner = QFrame()
        self._error_banner.setObjectName("ErrorBanner")
        self._error_banner.hide()

        lay = QHBoxLayout(self._error_banner)
        lay.setContentsMargins(14, 10, 14, 10)

        self._error_lbl = QLabel("")
        self._error_lbl.setObjectName("ErrorText")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        lay.addWidget(self._error_lbl, 1)
        return self._error_banner


    # ==================== ПУБЛИЧНЫЕ МЕТОДЫ СОСТОЯНИЯ ====================

    def set_running(self, symbol: str, progress: int, stage: str) -> None:
        """Выполняется пересчёт: кнопка блокируется, показывается прогресс и этап."""
        self._error_banner.hide()
        self._recalc_btn.setEnabled(False)
        self._progress.setValue(max(0, min(100, int(progress))))
        self._progress.show()
        self._set_status("running", stage)

    def set_idle(self, hint: str = "Готов к расчёту — нажмите «Пересчитать»") -> None:
        """Нейтральное состояние готовности без запуска расчёта."""
        self._recalc_btn.setEnabled(True)
        self._progress.hide()
        self._set_status("idle", hint)

    def mark_stale(self) -> None:
        """Параметры изменились — расчёт не запускаем, просим подтвердить."""
        self._error_banner.hide()
        self._progress.hide()
        self._recalc_btn.setEnabled(True)
        if self._has_report:
            self._set_status("stale", "Параметры изменены — нажмите «Пересчитать»")
        else:
            self.set_idle("Параметры изменены — нажмите «Пересчитать»")

    def update_progress(self, progress: int, stage: str) -> None:
        self._progress.setValue(max(0, min(100, int(progress))))
        self._set_status("running", stage)

    def set_report(self, report: dict[str, Any]) -> None:
        """Заполняет карточки результатами; прошлые значения не сбрасываются при ошибке."""
        self._has_report = True
        self._error_banner.hide()
        self._progress.hide()
        self._recalc_btn.setEnabled(True)

        for market in report.get("markets") or []:
            if not isinstance(market, dict):
                continue
            card = self._cards.get(str(market.get("id", "")))
            if card is not None:
                card.set_market(market)

        elapsed = float(report.get("elapsed_sec") or 0.0)
        updated_at = str(report.get("updated_at") or "")
        status_text = f"Готово за {elapsed:.1f}с"
        if updated_at:
            status_text += f" • {updated_at}"
        self._set_status("done", status_text)

    def set_error(self, error_msg: str) -> None:
        self._progress.hide()
        self._recalc_btn.setEnabled(True)
        self._set_status("error", "Ошибка расчёта")
        self._error_lbl.setText(f"Ошибка расчёта: {error_msg}")
        self._error_lbl.setToolTip(error_msg)
        self._error_banner.show()

    def _set_status(self, status_key: str, text: str) -> None:
        self._status_lbl.setProperty("status", status_key)
        self._status_lbl.setText(text)
        repolish(self._status_lbl)
