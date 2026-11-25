"""Страница инструмента OI & Delta Regime: панель управления, график и лента."""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.config import DEFAULT_CONFIG, ConfigService
from app.services.oi_regime.models import (
    SPOT_STATUS_DIV,
    SPOT_STATUS_SYNC,
    AnomalyEvent,
    OutcomeStats,
    RegimeBundle,
    RegimeParams,
)
from app.tools.oi_regime.chart_widget import OIRegimeChart
from app.tools.oi_regime.feed_widget import AnomalyFeed
from app.tools.oi_regime.labels import (
    LAYER_OPTIONS as _LAYER_DEFAULTS,
    SENSITIVITY_PRESETS as _PRESETS,
)
from app.tools.oi_regime.legend_dialog import OIRegimeLegendDialog
from app.ui.widgets import repolish

log = logging.getLogger(__name__)

SAVE_DEBOUNCE_MS = 300

_OUTCOME_COLUMNS = ("3 бар", "6 бар", "12 бар")

_LAYER_SETTINGS_ATTR = {
    "multi_bar": "oi_show_multi_bar",
    "trap_levels": "oi_show_trap_levels",
    "standalone": "oi_show_standalone_absorption",
    "spot_z": "oi_show_spot_z",
}


class OutcomeStatsDialog(QDialog):
    """Лёгкое окно со статистикой отработки по режимам."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Статистика отработки · OI & Delta Regime")
        self.setModal(False)
        self.resize(760, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self._hint = QLabel(
            "Медианные движения в % после события: MFE (максимум вверх), "
            "MAE (максимум вниз), закрытие через H закрытых свечей."
        )
        self._hint.setObjectName("SettingsHint")
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)
        self._table = QTableWidget(0, 2 + len(_OUTCOME_COLUMNS) * 3)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        header = ["Режим", "N"]
        for column in _OUTCOME_COLUMNS:
            header.extend([f"MFE {column}", f"MAE {column}", f"Закр. {column}"])
        self._table.setHorizontalHeaderLabels(header)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        lay.addWidget(self._table, 1)
        self._close_btn = QPushButton("Закрыть")
        self._close_btn.setObjectName("SecondaryBtn")
        self._close_btn.clicked.connect(self.close)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self._close_btn)
        lay.addLayout(footer)

    def set_stats(self, stats: OutcomeStats | None) -> None:
        self._table.setRowCount(0)
        if stats is None or not stats.rows:
            self._hint.setText("Статистика недоступна: нет закрытых событий в видимом окне.")
            return
        self._hint.setText(
            f"Событий в выборке: {stats.generated_bars} закрытых баров. "
            "Медианные MFE/MAE и результат через 3/6/12 закрытых свечей."
        )
        for row in stats.rows:
            position = self._table.rowCount()
            self._table.insertRow(position)
            self._table.setItem(position, 0, QTableWidgetItem(row.label))
            self._table.setItem(position, 1, QTableWidgetItem(str(row.count)))
            column = 2
            by_horizon = {item.horizon: item for item in row.horizons}
            for horizon in (3, 6, 12):
                item = by_horizon.get(horizon)
                if item is None or item.n == 0:
                    values = ("—", "—", "—")
                else:
                    values = (
                        _fmt_pct(item.mfe_pct),
                        _fmt_pct(item.mae_pct),
                        _fmt_pct(item.close_pct),
                    )
                for value in values:
                    self._table.setItem(position, column, QTableWidgetItem(value))
                    column += 1


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


_TIMEFRAMES = ("5m", "15m", "1h", "4h")


class OIRegimePage(QWidget):
    """Компактная панель управления + график + лента аномалий."""

    refresh_requested = Signal()
    timeframe_changed = Signal(str)
    params_changed = Signal(dict)
    live_toggled = Signal(bool)
    visibility_changed = Signal(bool)
    defaults_reset = Signal(bool)  # True, если таймфрейм изменился
    toast_requested = Signal(str)

    def __init__(
        self,
        params: RegimeParams,
        refresh_sec: int = 20,
        parent: QWidget | None = None,
        config: ConfigService | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._params = params
        self._bundle: RegimeBundle | None = None
        self._updating = False
        self._refresh_sec = int(refresh_sec)
        self._tf = params.timeframe
        self._layer_actions: dict[str, QAction] = {}
        self._stats_dialog: OutcomeStatsDialog | None = None
        self._legend_dialog: OIRegimeLegendDialog | None = None
        self._params_timer = QTimer(self)
        self._params_timer.setSingleShot(True)
        self._params_timer.setInterval(110)
        self._params_timer.timeout.connect(self._emit_params)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_toolbar_state)

        self._build_ui()
        self.set_defaults(params, refresh_sec)

    # ==================== ПОСТРОЕНИЕ ====================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.chart = OIRegimeChart()
        self.feed = AnomalyFeed()
        self.feed.event_clicked.connect(self.chart.center_on_timestamp)

        root.addWidget(self._build_toolbar())

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)

        chart_panel = QFrame()
        chart_panel.setObjectName("OiChartPanel")
        chart_lay = QVBoxLayout(chart_panel)
        chart_lay.setContentsMargins(0, 0, 0, 0)
        chart_lay.addWidget(self.chart)
        self._splitter.addWidget(chart_panel)

        feed_panel = QFrame()
        feed_panel.setObjectName("OiFeedPanel")
        feed_lay = QVBoxLayout(feed_panel)
        feed_lay.setContentsMargins(0, 0, 0, 0)
        feed_lay.addWidget(self.feed)
        self._splitter.addWidget(feed_panel)

        self._splitter.setStretchFactor(0, 77)
        self._splitter.setStretchFactor(1, 23)
        self._splitter.setSizes([780, 240])
        root.addWidget(self._splitter, 1)

    def _make_chip(self, text: str, tooltip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("FilterChip")
        btn.setProperty("active", False)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setMinimumHeight(26)
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        repolish(btn)
        return btn

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("OiToolbar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        tf_label = QLabel("ТФ")
        tf_label.setObjectName("OiToolbarLabel")
        row1.addWidget(tf_label)

        self._tf_buttons: dict[str, QPushButton] = {}
        for tf in _TIMEFRAMES:
            btn = self._make_chip(tf, f"Таймфрейм {tf}")
            btn.setFixedWidth(42)
            btn.clicked.connect(lambda _checked=False, value=tf: self._on_tf_clicked(value))
            self._tf_buttons[tf] = btn
            row1.addWidget(btn)

        sep = QFrame()
        sep.setObjectName("SidebarDivider")
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        row1.addWidget(sep)

        preset_label = QLabel("Пресет")
        preset_label.setObjectName("OiToolbarLabel")
        row1.addWidget(preset_label)

        self._preset_buttons: dict[str, QPushButton] = {}
        for key, text, _toi, _td in _PRESETS:
            btn = self._make_chip(
                text,
                f"Пресет {text}: порог OI {_toi:.1f}σ / порог Δ {_td:.1f}σ",
            )
            btn.clicked.connect(lambda _checked=False, value=key: self._apply_preset(value))
            self._preset_buttons[key] = btn
            row1.addWidget(btn)

        row1.addStretch(1)

        self._status_lbl = QLabel("Готово к загрузке")
        self._status_lbl.setObjectName("OiStatusBadge")
        self._status_lbl.setProperty("state", "ok")
        self._status_lbl.setMinimumWidth(170)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        repolish(self._status_lbl)

        self._live_cb = QCheckBox("Live")
        self._live_cb.setToolTip("Автообновление по таймеру из настроек")
        self._live_cb.toggled.connect(self._on_live_toggled)

        self._refresh_btn = QPushButton("↻ Обновить")
        self._refresh_btn.setObjectName("SecondaryBtn")
        self._refresh_btn.setFixedHeight(28)
        self._refresh_btn.setToolTip("Полностью обновить данные Binance/Bybit")
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)

        self._reset_btn = QPushButton("↺ Сброс")
        self._reset_btn.setObjectName("SecondaryBtn")
        self._reset_btn.setFixedHeight(28)
        self._reset_btn.setToolTip(
            "Сбросить параметры и фильтры OI & Delta Regime к значениям по умолчанию"
        )
        self._reset_btn.clicked.connect(self._reset_toolbar_defaults)

        row1.addWidget(self._status_lbl)
        row1.addWidget(self._live_cb)
        row1.addWidget(self._refresh_btn)
        row1.addWidget(self._reset_btn)
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)

        self._abs_cb = QCheckBox("Только с Поглощением")
        self._abs_cb.setToolTip("Фильтровать ленту и маркеры графика")
        self._abs_cb.toggled.connect(self._on_filter_options_changed)
        self._weak_cb = QCheckBox("Скрыть слабые без Спота")
        self._weak_cb.setToolTip("Оставить только события с подтверждением или дивергенцией спота")
        self._weak_cb.toggled.connect(self._on_filter_options_changed)
        self._z_cb = QCheckBox("Подграфик Z-score")
        self._z_cb.setChecked(True)
        self._z_cb.toggled.connect(self._on_zscore_toggled)

        self._layers_btn = QPushButton("Слои ▾")
        self._layers_btn.setObjectName("FilterChip")
        self._layers_btn.setMinimumHeight(26)
        self._layers_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._layers_btn.setToolTip("Дополнительные слои графика")
        self._layers_menu = QMenu(self._layers_btn)
        for key, (config_key, label) in _LAYER_DEFAULTS.items():
            action = self._layers_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(bool(DEFAULT_CONFIG[config_key]))
            action.toggled.connect(
                lambda checked, layer=key: self._on_layer_toggled(layer, checked)
            )
            self._layer_actions[key] = action
        self._layers_btn.clicked.connect(self._show_layers_menu)
        repolish(self._layers_btn)

        self._stats_btn = QPushButton("📊 Статистика")
        self._stats_btn.setObjectName("SecondaryBtn")
        self._stats_btn.setFixedHeight(26)
        self._stats_btn.setToolTip("Статистика отработки по типам режимов")
        self._stats_btn.clicked.connect(self._show_stats)

        self._legend_btn = QPushButton("? Легенда")
        self._legend_btn.setObjectName("SecondaryBtn")
        self._legend_btn.setFixedHeight(26)
        self._legend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._legend_btn.setToolTip(
            "Справочник всех обозначений, слоёв и метрик инструмента"
        )
        self._legend_btn.clicked.connect(self._show_legend)

        row2.addWidget(self._abs_cb)
        row2.addWidget(self._weak_cb)
        row2.addWidget(self._z_cb)
        row2.addWidget(self._layers_btn)
        row2.addWidget(self._stats_btn)
        row2.addWidget(self._legend_btn)
        row2.addStretch(1)

        self._toi_spin = QDoubleSpinBox()
        self._toi_spin.setRange(0.5, 6.0)
        self._toi_spin.setSingleStep(0.1)
        self._toi_spin.setDecimals(1)
        self._toi_spin.setFixedWidth(66)
        self._toi_spin.setToolTip("Минимальный Z-score изменения OI (в σ)")
        self._toi_spin.valueChanged.connect(self._on_spin_changed)
        toi_label = QLabel("Порог OI")
        toi_label.setObjectName("OiToolbarLabel")
        toi_label.setToolTip("Минимальный Z-score изменения OI (в σ)")
        row2.addWidget(toi_label)
        row2.addWidget(self._toi_spin)

        self._tdelta_spin = QDoubleSpinBox()
        self._tdelta_spin.setRange(0.5, 6.0)
        self._tdelta_spin.setSingleStep(0.1)
        self._tdelta_spin.setDecimals(1)
        self._tdelta_spin.setFixedWidth(66)
        self._tdelta_spin.setToolTip("Минимальный Z-score фьючерсной дельты (в σ)")
        self._tdelta_spin.valueChanged.connect(self._on_spin_changed)
        tdelta_label = QLabel("Порог Δ")
        tdelta_label.setObjectName("OiToolbarLabel")
        tdelta_label.setToolTip("Минимальный Z-score фьючерсной дельты (в σ)")
        row2.addWidget(tdelta_label)
        row2.addWidget(self._tdelta_spin)

        self._window_spin = QSpinBox()
        self._window_spin.setRange(15, 200)
        self._window_spin.setFixedWidth(58)
        self._window_spin.setToolTip("Размер скользящего окна свечей N")
        self._window_spin.valueChanged.connect(self._on_spin_changed)
        window_label = QLabel("Окно")
        window_label.setObjectName("OiToolbarLabel")
        window_label.setToolTip("Размер скользящего окна свечей N")
        row2.addWidget(window_label)
        row2.addWidget(self._window_spin)
        outer.addLayout(row2)
        return bar


    # ==================== СОСТОЯНИЕ И ПАРАМЕТРЫ ====================

    @property
    def current_timeframe(self) -> str:
        return self._tf

    @property
    def current_params(self) -> RegimeParams:
        return self._params

    @property
    def bundle(self) -> RegimeBundle | None:
        return self._bundle

    def set_defaults(self, params: RegimeParams, refresh_sec: int) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._updating = True
        try:
            self._params = params
            self._refresh_sec = int(refresh_sec)
            self._set_timeframe_ui(params.timeframe)
            with QSignalBlocker(self._toi_spin):
                self._toi_spin.setValue(params.threshold_oi)
            with QSignalBlocker(self._tdelta_spin):
                self._tdelta_spin.setValue(params.threshold_delta)
            with QSignalBlocker(self._window_spin):
                self._window_spin.setValue(params.z_window)
            self._update_preset_state()
            self.chart.set_thresholds(params.threshold_oi, params.threshold_delta)
            self._restore_toolbar_state()
        finally:
            self._updating = False

    def _restore_toolbar_state(self) -> None:
        """Восстанавливает чекбоксы и фильтры из ConfigService без сигналов и записи на диск."""
        settings = self._config.current if self._config is not None else None
        only_absorption = (
            settings.oi_only_absorption
            if settings is not None
            else bool(DEFAULT_CONFIG["OI_ONLY_ABSORPTION"])
        )
        hide_weak = (
            settings.oi_hide_weak_no_spot
            if settings is not None
            else bool(DEFAULT_CONFIG["OI_HIDE_WEAK_NO_SPOT"])
        )
        show_z = (
            settings.oi_show_z_subplot
            if settings is not None
            else bool(DEFAULT_CONFIG["OI_SHOW_Z_SUBPLOT"])
        )
        live_enabled = (
            settings.oi_live_enabled
            if settings is not None
            else bool(DEFAULT_CONFIG["OI_LIVE_ENABLED"])
        )
        with QSignalBlocker(self._abs_cb):
            self._abs_cb.setChecked(bool(only_absorption))
        with QSignalBlocker(self._weak_cb):
            self._weak_cb.setChecked(bool(hide_weak))
        with QSignalBlocker(self._z_cb):
            self._z_cb.setChecked(bool(show_z))
        for key, (config_key, _label) in _LAYER_DEFAULTS.items():
            default = bool(DEFAULT_CONFIG[config_key])
            if settings is not None:
                value = bool(getattr(settings, _LAYER_SETTINGS_ATTR[key], default))
            else:
                value = default
            action = self._layer_actions.get(key)
            if action is not None:
                with QSignalBlocker(action):
                    action.setChecked(value)
        self.chart.set_show_zscore(bool(show_z))
        self._apply_layers()
        self._apply_filters()
        self._update_live_availability()
        with QSignalBlocker(self._live_cb):
            self._live_cb.setChecked(bool(live_enabled) and self._refresh_sec > 0)

    def set_live_checked(self, checked: bool) -> None:
        with QSignalBlocker(self._live_cb):
            self._live_cb.setChecked(bool(checked))

    def is_live_checked(self) -> bool:
        return self._live_cb.isChecked()

    def _update_live_availability(self) -> None:
        enabled = self._refresh_sec > 0
        self._live_cb.setEnabled(enabled)
        if not enabled:
            with QSignalBlocker(self._live_cb):
                self._live_cb.setChecked(False)
            self._live_cb.setToolTip("Автообновление отключено (OI_REFRESH_SEC = 0)")
        else:
            self._live_cb.setToolTip(
                f"Автообновление каждые {self._refresh_sec} сек (пауза вне вкладки)"
            )

    def set_refresh_sec(self, refresh_sec: int) -> None:
        self._refresh_sec = int(refresh_sec)
        self._update_live_availability()

    def _set_timeframe_ui(self, timeframe: str) -> None:
        tf = timeframe if timeframe in _TIMEFRAMES else "1h"
        self._tf = tf
        for key, btn in self._tf_buttons.items():
            btn.setProperty("active", key == tf)
            repolish(btn)

    def _on_tf_clicked(self, timeframe: str) -> None:
        if timeframe == self._tf:
            return
        self._set_timeframe_ui(timeframe)
        self._params = replace(self._params, timeframe=timeframe)
        self._save_toolbar_state()
        self.timeframe_changed.emit(timeframe)

    def _update_preset_state(self) -> None:
        toi = round(self._toi_spin.value(), 2)
        td = round(self._tdelta_spin.value(), 2)
        for key, _text, preset_toi, preset_td in _PRESETS:
            active = abs(toi - preset_toi) < 1e-6 and abs(td - preset_td) < 1e-6
            self._preset_buttons[key].setProperty("active", active)
            repolish(self._preset_buttons[key])

    def _apply_preset(self, key: str) -> None:
        preset = next((item for item in _PRESETS if item[0] == key), None)
        if preset is None:
            return
        _key, _text, toi, td = preset
        self._updating = True
        try:
            with QSignalBlocker(self._toi_spin):
                self._toi_spin.setValue(toi)
            with QSignalBlocker(self._tdelta_spin):
                self._tdelta_spin.setValue(td)
            self._update_preset_state()
        finally:
            self._updating = False
        self.chart.set_thresholds(toi, td)
        self._save_toolbar_state()
        self._params_timer.start()

    def _on_spin_changed(self, _value: float | int) -> None:
        if self._updating:
            return
        self._update_preset_state()
        self.chart.set_thresholds(self._toi_spin.value(), self._tdelta_spin.value())
        self._params_timer.start()
        self._save_timer.start()

    def _on_filter_options_changed(self, _checked: bool = False) -> None:
        if self._updating:
            return
        self._apply_filters()
        self._save_toolbar_state()

    def _on_zscore_toggled(self, enabled: bool) -> None:
        self.chart.set_show_zscore(enabled)
        if self._updating:
            return
        self._save_toolbar_state()

    def _show_layers_menu(self) -> None:
        self._layers_menu.exec(
            self._layers_btn.mapToGlobal(self._layers_btn.rect().bottomLeft())
        )

    def _on_layer_toggled(self, _key: str, _checked: bool) -> None:
        self._apply_layers()
        if self._updating:
            return
        self._save_toolbar_state()

    def _apply_layers(self) -> None:
        flags = {
            key: bool(action.isChecked()) for key, action in self._layer_actions.items()
        }
        self.chart.set_overlays(
            show_multi_bar=flags.get("multi_bar", False),
            show_trap_levels=flags.get("trap_levels", False),
            show_spot_z=flags.get("spot_z", False),
            show_standalone_absorption=flags.get("standalone", False),
        )

    def _show_stats(self) -> None:
        if self._stats_dialog is None:
            self._stats_dialog = OutcomeStatsDialog(self)
        stats = self._bundle.outcome_stats if self._bundle is not None else None
        self._stats_dialog.set_stats(stats)
        self._stats_dialog.show()
        self._stats_dialog.raise_()
        self._stats_dialog.activateWindow()

    def _show_legend(self) -> None:
        """Открывает немодальный справочник обозначений, не блокируя график."""
        if self._legend_dialog is None:
            self._legend_dialog = OIRegimeLegendDialog(self)
        self._legend_dialog.show()
        self._legend_dialog.raise_()
        self._legend_dialog.activateWindow()

    def _toolbar_state_payload(self) -> dict[str, object]:
        """Актуальное состояние тулбара в формате ключей config.json."""
        payload: dict[str, object] = {
            "OI_DEFAULT_TIMEFRAME": self._tf,
            "OI_THRESHOLD_OI": round(float(self._toi_spin.value()), 2),
            "OI_THRESHOLD_DELTA": round(float(self._tdelta_spin.value()), 2),
            "OI_Z_WINDOW": int(self._window_spin.value()),
            "OI_ONLY_ABSORPTION": self._abs_cb.isChecked(),
            "OI_HIDE_WEAK_NO_SPOT": self._weak_cb.isChecked(),
            "OI_SHOW_Z_SUBPLOT": self._z_cb.isChecked(),
            "OI_LIVE_ENABLED": self._live_cb.isChecked(),
        }
        for key, (config_key, _label) in _LAYER_DEFAULTS.items():
            action = self._layer_actions.get(key)
            payload[config_key] = (
                bool(action.isChecked())
                if action is not None
                else bool(DEFAULT_CONFIG[config_key])
            )
        return payload

    def _save_toolbar_state(self) -> None:
        """Тихо сохраняет состояние тулбара в config.json (без toast и спама в логах)."""
        if self._config is None:
            return
        try:
            self._config.patch_raw(self._toolbar_state_payload())
        except Exception:
            log.warning("Не удалось сохранить состояние тулбара OI Regime", exc_info=True)

    def _reset_toolbar_defaults(self) -> None:
        """Сбрасывает параметры и фильтры тулбара к значениям из DEFAULT_CONFIG."""
        self._params_timer.stop()
        self._save_timer.stop()
        old_tf = self._tf
        default_tf = str(DEFAULT_CONFIG["OI_DEFAULT_TIMEFRAME"])
        default_toi = float(DEFAULT_CONFIG["OI_THRESHOLD_OI"])
        default_tdelta = float(DEFAULT_CONFIG["OI_THRESHOLD_DELTA"])
        default_window = int(DEFAULT_CONFIG["OI_Z_WINDOW"])

        self._updating = True
        try:
            self._set_timeframe_ui(default_tf)
            self._params = replace(self._params, timeframe=default_tf)
            with QSignalBlocker(self._toi_spin):
                self._toi_spin.setValue(default_toi)
            with QSignalBlocker(self._tdelta_spin):
                self._tdelta_spin.setValue(default_tdelta)
            with QSignalBlocker(self._window_spin):
                self._window_spin.setValue(default_window)
            with QSignalBlocker(self._abs_cb):
                self._abs_cb.setChecked(bool(DEFAULT_CONFIG["OI_ONLY_ABSORPTION"]))
            with QSignalBlocker(self._weak_cb):
                self._weak_cb.setChecked(bool(DEFAULT_CONFIG["OI_HIDE_WEAK_NO_SPOT"]))
            with QSignalBlocker(self._z_cb):
                self._z_cb.setChecked(bool(DEFAULT_CONFIG["OI_SHOW_Z_SUBPLOT"]))
            self.chart.set_show_zscore(self._z_cb.isChecked())
            for key, (config_key, _label) in _LAYER_DEFAULTS.items():
                action = self._layer_actions.get(key)
                if action is not None:
                    with QSignalBlocker(action):
                        action.setChecked(bool(DEFAULT_CONFIG[config_key]))
            self._apply_layers()
            self._update_preset_state()
            self._update_live_availability()
            with QSignalBlocker(self._live_cb):
                self._live_cb.setChecked(
                    bool(DEFAULT_CONFIG["OI_LIVE_ENABLED"]) and self._refresh_sec > 0
                )
        finally:
            self._updating = False

        self._save_toolbar_state()
        self._apply_filters()
        self._emit_params()
        self.defaults_reset.emit(old_tf != default_tf)
        self.toast_requested.emit("Параметры OI & Delta Regime сброшены к дефолтам")

    def _emit_params(self) -> None:
        self._params = replace(
            self._params,
            threshold_oi=round(self._toi_spin.value(), 2),
            threshold_delta=round(self._tdelta_spin.value(), 2),
            z_window=int(self._window_spin.value()),
        )
        self.chart.set_thresholds(self._params.threshold_oi, self._params.threshold_delta)
        self.params_changed.emit(
            {
                "threshold_oi": self._params.threshold_oi,
                "threshold_delta": self._params.threshold_delta,
                "z_window": self._params.z_window,
            }
        )


    # ==================== ДАННЫЕ И СТАТУС ====================

    def apply_bundle(self, bundle: RegimeBundle) -> None:
        old_key = (self._bundle.symbol, self._bundle.timeframe) if self._bundle else None
        new_key = (bundle.symbol, bundle.timeframe)
        self._bundle = bundle
        self._params = bundle.params
        self.chart.set_bundle(bundle, reset_view=old_key != new_key or old_key is None)
        self.feed.set_tz_offset(bundle.params.tz_offset_hours)
        self._apply_filters()
        if self._stats_dialog is not None and self._stats_dialog.isVisible():
            self._stats_dialog.set_stats(bundle.outcome_stats)
        self._set_status(
            f"{bundle.sources_label} · {bundle.updated_at}",
            "warn" if bundle.warnings else "ok",
            warnings=bundle.warnings,
        )
        self._refresh_btn.setEnabled(True)

    def set_loading(self, stage: str = "Загрузка...") -> None:
        self.set_status_text(stage, "loading")
        self._refresh_btn.setEnabled(False)

    def set_error(self, message: str) -> None:
        self._set_status(f"Ошибка: {message}", "error", warnings=[message])
        self._refresh_btn.setEnabled(True)

    def set_status_text(self, text: str, state: str = "ok", warnings: list[str] | None = None) -> None:
        if state == "loading" and "агрузка" in text:
            text = "Загрузка..."
        self._set_status(text, state, warnings=warnings)

    @property
    def status_state(self) -> str:
        return str(self._status_lbl.property("state") or "")

    def _set_status(
        self, text: str, state: str, warnings: list[str] | None = None
    ) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setProperty("state", state)
        self._status_lbl.setToolTip("\n".join(warnings or []))
        repolish(self._status_lbl)

    def _filtered_events(self) -> list[AnomalyEvent]:
        if self._bundle is None:
            return []
        only_absorption = self._abs_cb.isChecked()
        hide_weak = self._weak_cb.isChecked()
        result: list[AnomalyEvent] = []
        for event in self._bundle.events:
            if only_absorption and not event.is_absorption:
                continue
            if hide_weak and event.spot_status not in (
                SPOT_STATUS_SYNC,
                SPOT_STATUS_DIV,
            ):
                continue
            result.append(event)
        return result

    def _apply_filters(self, _checked: bool | None = None) -> None:
        self.chart.set_filters(self._abs_cb.isChecked(), self._weak_cb.isChecked())
        total = len(self._bundle.events) if self._bundle is not None else 0
        self.feed.set_events(self._filtered_events(), total_count=total)

    def _on_live_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        self._save_toolbar_state()
        self.live_toggled.emit(bool(checked))

    # ==================== ВИДИМОСТЬ ====================

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().hideEvent(event)
        self.visibility_changed.emit(False)
        if self._params_timer.isActive():
            self._params_timer.stop()
            self._emit_params()

