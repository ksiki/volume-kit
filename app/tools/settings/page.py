import os
import subprocess
import sys
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.config import ConfigService
from app.ui.widgets import prepare_combo_box


def _make_section_card(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("Card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 16, 20, 18)
    lay.setSpacing(12)

    head = QVBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(2)

    lbl_title = QLabel(title)
    lbl_title.setObjectName("SettingsSectionTitle")
    head.addWidget(lbl_title)

    if subtitle:
        lbl_sub = QLabel(subtitle)
        lbl_sub.setObjectName("SettingsHint")
        lbl_sub.setWordWrap(True)
        head.addWidget(lbl_sub)

    lay.addLayout(head)

    div = QFrame()
    div.setObjectName("SidebarDivider")
    lay.addWidget(div)

    return card, lay


class SettingsPage(QWidget):
    """Визуальный редактор config.json внутри Volume Kit."""

    saved = Signal(dict, bool)  # (config_dict, should_recalculate)
    toast_requested = Signal(str)

    def __init__(self, config: ConfigService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._build_ui()
        self.load_from_disk()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Верхняя панель действий настроек
        action_bar = QFrame()
        action_bar.setObjectName("WebToolbar")
        ab_lay = QHBoxLayout(action_bar)
        ab_lay.setContentsMargins(24, 10, 24, 10)
        ab_lay.setSpacing(10)

        path_lbl = QLabel(f"Файл: {self._config.config_path}")
        path_lbl.setObjectName("ScaleMeta")
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        btn_open_file = QPushButton("Открыть JSON ↗")
        btn_open_file.setObjectName("SecondaryBtn")
        btn_open_file.clicked.connect(self._open_config_file)

        btn_reset = QPushButton("Сбросить дефолты")
        btn_reset.setObjectName("DangerBtn")
        btn_reset.clicked.connect(self._reset_defaults)

        btn_save = QPushButton("Сохранить")
        btn_save.setObjectName("SecondaryBtn")
        btn_save.clicked.connect(lambda: self._save(recalculate=False))

        btn_save_recalc = QPushButton("Сохранить и пересчитать")
        btn_save_recalc.setObjectName("PrimaryBtn")
        btn_save_recalc.clicked.connect(lambda: self._save(recalculate=True))

        ab_lay.addWidget(path_lbl)
        ab_lay.addStretch(1)
        ab_lay.addWidget(btn_open_file)
        ab_lay.addWidget(btn_reset)
        ab_lay.addWidget(btn_save)
        ab_lay.addWidget(btn_save_recalc)
        root.addWidget(action_bar)

        # Скроллируемая область с карточками настроек
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        main_lay = QVBoxLayout(content)
        main_lay.setContentsMargins(24, 20, 24, 28)
        main_lay.setSpacing(18)

        # Строка 1: Основные + Периоды загрузки (2 колонки)
        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(18)
        top_grid.setVerticalSpacing(18)
        top_grid.addWidget(self._build_general_card(), 0, 0)
        top_grid.addWidget(self._build_days_card(), 0, 1)
        top_grid.setColumnStretch(0, 1)
        top_grid.setColumnStretch(1, 1)
        main_lay.addLayout(top_grid)

        # Строка 2: Параметры анализа + Системные настройки (2 колонки)
        mid_grid = QGridLayout()
        mid_grid.setHorizontalSpacing(18)
        mid_grid.setVerticalSpacing(18)
        mid_grid.addWidget(self._build_analytics_card(), 0, 0)
        mid_grid.addWidget(self._build_system_card(), 0, 1)
        mid_grid.setColumnStretch(0, 1)
        mid_grid.setColumnStretch(1, 1)
        main_lay.addLayout(mid_grid)

        # Строка 3: Таблицы параметров (Таймфреймы ATR/HL и Правила округления масштаба)
        tables_grid = QGridLayout()
        tables_grid.setHorizontalSpacing(18)
        tables_grid.setVerticalSpacing(18)
        tables_grid.addWidget(self._build_tf_maps_card(), 0, 0)
        tables_grid.addWidget(self._build_scale_rules_card(), 0, 1)
        tables_grid.setColumnStretch(0, 1)
        tables_grid.setColumnStretch(1, 1)
        main_lay.addLayout(tables_grid)

        # Строка 4: OI & Delta Regime
        main_lay.addWidget(self._build_oi_regime_card())

        main_lay.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _build_general_card(self) -> QFrame:
        card, lay = _make_section_card(
            "Основные параметры",
            "Инструмент, целевое число кластеров в свече и режим округления цены.",
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._symbol_edit = QLineEdit()
        self._symbol_edit.setPlaceholderText("BTCUSDT")

        self._spot_clusters_spin = QSpinBox()
        self._spot_clusters_spin.setRange(1, 500)

        self._fut_clusters_spin = QSpinBox()
        self._fut_clusters_spin.setRange(1, 500)

        self._cluster_mode_combo = QComboBox()
        prepare_combo_box(self._cluster_mode_combo)
        self._cluster_mode_combo.addItems(["round", "floor"])

        form.addRow("Тикер (SYMBOL):", self._symbol_edit)
        form.addRow("Кластеры Spot (SPOT_CLUSTERS):", self._spot_clusters_spin)
        form.addRow("Кластеры Futures (FUTURES_CLUSTERS):", self._fut_clusters_spin)
        form.addRow("Режим кластеризации (CLUSTER_MODE):", self._cluster_mode_combo)

        lay.addLayout(form)
        return card

    def _build_days_card(self) -> QFrame:
        card, lay = _make_section_card(
            "Глубина истории (в днях)",
            "Количество календарных дней, загружаемых для расчёта по каждой бирже и таймфрейму.",
        )

        def _days_spin() -> QSpinBox:
            box = QSpinBox()
            box.setRange(1, 365)
            return box

        self._days_bin_4h = _days_spin()
        self._days_bin_1h = _days_spin()
        self._days_bin_15m = _days_spin()
        self._days_bin_5m = _days_spin()
        self._days_byb_1h = _days_spin()
        self._days_byb_15m = _days_spin()
        self._days_byb_5m = _days_spin()

        spot_form = QFormLayout()
        spot_form.setSpacing(10)
        spot_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        spot_title = QLabel("Binance Spot")
        spot_title.setObjectName("SettingsSectionTitle")
        spot_form.addRow(spot_title)
        spot_form.addRow("4h (DAYS_BINANCE_4H):", self._days_bin_4h)
        spot_form.addRow("1h (DAYS_BINANCE_1H):", self._days_bin_1h)
        spot_form.addRow("15m (DAYS_BINANCE_15M):", self._days_bin_15m)
        spot_form.addRow("5m (DAYS_BINANCE_5M):", self._days_bin_5m)

        futures_form = QFormLayout()
        futures_form.setSpacing(10)
        futures_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        futures_title = QLabel("ByBit Futures")
        futures_title.setObjectName("SettingsSectionTitle")
        futures_form.addRow(futures_title)
        futures_form.addRow("1h (DAYS_BYBIT_1H):", self._days_byb_1h)
        futures_form.addRow("15m (DAYS_BYBIT_15M):", self._days_byb_15m)
        futures_form.addRow("5m (DAYS_BYBIT_5M):", self._days_byb_5m)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        grid.addLayout(spot_form, 0, 0)
        grid.addLayout(futures_form, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        lay.addLayout(grid)
        return card

    def _build_analytics_card(self) -> QFrame:
        card, lay = _make_section_card(
            "Robust ATR и фильтрация выбросов",
            "Параметры винзоризации ATR и понижения веса аномальных дней.",
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._atr_winsor = QDoubleSpinBox()
        self._atr_winsor.setRange(50.0, 100.0)
        self._atr_winsor.setDecimals(1)
        self._atr_winsor.setSingleStep(0.5)
        self._atr_winsor.setSuffix(" %")

        self._outlier_ratio = QDoubleSpinBox()
        self._outlier_ratio.setRange(1.0, 20.0)
        self._outlier_ratio.setDecimals(2)
        self._outlier_ratio.setSingleStep(0.1)

        self._outlier_min_weight = QDoubleSpinBox()
        self._outlier_min_weight.setRange(0.01, 1.0)
        self._outlier_min_weight.setDecimals(2)
        self._outlier_min_weight.setSingleStep(0.05)

        form.addRow("Перцентиль винзоризации (ATR_WINSOR_PCT):", self._atr_winsor)
        form.addRow("Порог выброса дня (OUTLIER_DAY_MAX_RATIO):", self._outlier_ratio)
        form.addRow("Мин. вес выброса (OUTLIER_DAY_MIN_WEIGHT):", self._outlier_min_weight)

        lay.addLayout(form)
        return card

    def _build_system_card(self) -> QFrame:
        card, lay = _make_section_card(
            "Загрузка и вывод",
            "Параллелизм скачивания данных, смещение часового пояса и экспорт CSV.",
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 32)

        self._tz_offset_spin = QSpinBox()
        self._tz_offset_spin.setRange(-12, 14)
        self._tz_offset_spin.setSuffix(" ч")

        self._console_top_spin = QSpinBox()
        self._console_top_spin.setRange(0, 500)

        self._save_csv_cb = QCheckBox("Сохранять отсортированные CSV-таблицы (SAVE_SORTED_CSV)")

        form.addRow("Потоки загрузки (DOWNLOAD_WORKERS):", self._workers_spin)
        form.addRow("Часовой пояс (TZ_OFFSET_HOURS):", self._tz_offset_spin)
        form.addRow("Топ строк в CLI (CONSOLE_TOP_ROWS):", self._console_top_spin)
        form.addRow("", self._save_csv_cb)

        lay.addLayout(form)
        return card

    def _build_tf_maps_card(self) -> QFrame:
        card, lay = _make_section_card(
            "Окна ATR и Half-Life по таймфреймам",
            "Соответствие таймфрейма (в минутах) окну ATR_LOOKBACK_DAYS и периоду полураспада HALF_LIFE_DAYS.",
        )

        self._tf_table = QTableWidget(0, 3)
        self._tf_table.setHorizontalHeaderLabels(["ТФ (мин)", "ATR Lookback (дн)", "Half-Life (дн)"])
        self._tf_table.verticalHeader().setVisible(False)
        self._tf_table.setAlternatingRowColors(True)
        self._tf_table.setMinimumHeight(210)
        hdr = self._tf_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._tf_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Добавить ТФ")
        btn_add.setObjectName("SecondaryBtn")
        btn_add.clicked.connect(self._add_tf_row)

        btn_del = QPushButton("Удалить строку")
        btn_del.setObjectName("SecondaryBtn")
        btn_del.clicked.connect(lambda: self._remove_selected_row(self._tf_table))

        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        return card

    def _build_scale_rules_card(self) -> QFrame:
        card, lay = _make_section_card(
            "Правила округления масштаба (SCALE_ROUNDING_RULES)",
            "Порог сырого масштаба (оставьте пустым для ∞) и шаг округления.",
        )

        self._rules_table = QTableWidget(0, 2)
        self._rules_table.setHorizontalHeaderLabels(["До значения (пусто = ∞)", "Шаг округления"])
        self._rules_table.verticalHeader().setVisible(False)
        self._rules_table.setAlternatingRowColors(True)
        self._rules_table.setMinimumHeight(210)
        hdr = self._rules_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._rules_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Добавить правило")
        btn_add.setObjectName("SecondaryBtn")
        btn_add.clicked.connect(self._add_rule_row)

        btn_del = QPushButton("Удалить строку")
        btn_del.setObjectName("SecondaryBtn")
        btn_del.clicked.connect(lambda: self._remove_selected_row(self._rules_table))

        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        return card

    def _build_oi_regime_card(self) -> QFrame:
        card, lay = _make_section_card(
            "OI & Delta Regime",
            "Межбиржевой OI (Binance Futures + Bybit), дельты и robust Z-score. "
            "Binance OI history доступен только за последние 30 дней.",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(10)

        instrument_form = QFormLayout()
        instrument_form.setSpacing(9)
        instrument_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._oi_tf_combo = QComboBox()
        prepare_combo_box(self._oi_tf_combo)
        self._oi_tf_combo.addItems(["5m", "15m", "1h", "4h"])

        self._oi_z_window = QSpinBox()
        self._oi_z_window.setRange(15, 200)

        self._oi_visible = QSpinBox()
        self._oi_visible.setRange(60, 400)

        self._oi_refresh = QSpinBox()
        self._oi_refresh.setRange(0, 120)
        self._oi_refresh.setSuffix(" сек")
        self._oi_refresh.setSpecialValueText("выкл")

        self._oi_use_bybit = QCheckBox("Использовать Bybit OI (OI_USE_BYBIT)")

        instrument_form.addRow("ТФ по умолчанию (OI_DEFAULT_TIMEFRAME):", self._oi_tf_combo)
        instrument_form.addRow("Окно Z-score (OI_Z_WINDOW):", self._oi_z_window)
        instrument_form.addRow("Видимых баров (OI_VISIBLE_BARS):", self._oi_visible)
        instrument_form.addRow("Автообновление (OI_REFRESH_SEC):", self._oi_refresh)
        instrument_form.addRow("", self._oi_use_bybit)

        thresholds_form = QFormLayout()
        thresholds_form.setSpacing(9)
        thresholds_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._oi_threshold_oi = QDoubleSpinBox()
        self._oi_threshold_oi.setRange(0.5, 6.0)
        self._oi_threshold_oi.setDecimals(2)
        self._oi_threshold_oi.setSingleStep(0.1)

        self._oi_threshold_delta = QDoubleSpinBox()
        self._oi_threshold_delta.setRange(0.5, 6.0)
        self._oi_threshold_delta.setDecimals(2)
        self._oi_threshold_delta.setSingleStep(0.1)

        self._oi_absorption_min_z = QDoubleSpinBox()
        self._oi_absorption_min_z.setRange(0.5, 5.0)
        self._oi_absorption_min_z.setDecimals(2)
        self._oi_absorption_min_z.setSingleStep(0.1)

        self._oi_absorption_ratio = QDoubleSpinBox()
        self._oi_absorption_ratio.setRange(0.10, 0.49)
        self._oi_absorption_ratio.setDecimals(2)
        self._oi_absorption_ratio.setSingleStep(0.05)

        thresholds_form.addRow("Порог ΔOI (OI_THRESHOLD_OI):", self._oi_threshold_oi)
        thresholds_form.addRow("Порог дельты (OI_THRESHOLD_DELTA):", self._oi_threshold_delta)
        thresholds_form.addRow(
            "Мин. Z дельты для поглощения (OI_ABSORPTION_MIN_Z_DELTA):",
            self._oi_absorption_min_z,
        )
        thresholds_form.addRow(
            "Доля закрытия (OI_ABSORPTION_CLOSE_RATIO):",
            self._oi_absorption_ratio,
        )

        spot_form = QFormLayout()
        spot_form.setSpacing(9)
        spot_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._oi_spot_sync = QDoubleSpinBox()
        self._oi_spot_sync.setRange(0.3, 4.0)
        self._oi_spot_sync.setDecimals(2)
        self._oi_spot_sync.setSingleStep(0.1)

        self._oi_spot_div = QDoubleSpinBox()
        self._oi_spot_div.setRange(0.2, 4.0)
        self._oi_spot_div.setDecimals(2)
        self._oi_spot_div.setSingleStep(0.1)

        self._oi_spot_dead = QDoubleSpinBox()
        self._oi_spot_dead.setRange(0.05, 1.5)
        self._oi_spot_dead.setDecimals(2)
        self._oi_spot_dead.setSingleStep(0.05)

        self._oi_spot_dead_extreme = QDoubleSpinBox()
        self._oi_spot_dead_extreme.setRange(1.0, 6.0)
        self._oi_spot_dead_extreme.setDecimals(2)
        self._oi_spot_dead_extreme.setSingleStep(0.1)

        spot_form.addRow("Z подтверждения спота (OI_SPOT_SYNC_MIN_Z):", self._oi_spot_sync)
        spot_form.addRow("Z дивергенции спота (OI_SPOT_DIV_MIN_Z):", self._oi_spot_div)
        spot_form.addRow("Мёртвая зона спота (OI_SPOT_DEAD_MAX_Z):", self._oi_spot_dead)
        spot_form.addRow(
            "Экстремум OI без спота (OI_SPOT_DEAD_EXTREME_OI_Z):",
            self._oi_spot_dead_extreme,
        )

        grid.addLayout(instrument_form, 0, 0)
        grid.addLayout(thresholds_form, 0, 1)
        grid.addLayout(spot_form, 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        return card

    # ==================== ЗАГРУЗКА / СБОР ДАННЫХ ====================

    def load_from_disk(self) -> None:
        try:
            cfg = self._config.load_raw()
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка чтения config.json", str(exc))
            return
        self._populate_fields(cfg)

    def _populate_fields(self, cfg: dict[str, Any]) -> None:
        self._symbol_edit.setText(str(cfg.get("SYMBOL", "BTCUSDT")))
        self._spot_clusters_spin.setValue(int(cfg.get("SPOT_CLUSTERS", 15)))
        self._fut_clusters_spin.setValue(int(cfg.get("FUTURES_CLUSTERS", 30)))

        mode = str(cfg.get("CLUSTER_MODE", "round")).strip().lower()
        idx = self._cluster_mode_combo.findText(mode)
        self._cluster_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self._days_bin_4h.setValue(int(cfg.get("DAYS_BINANCE_4H", 120)))
        self._days_bin_1h.setValue(int(cfg.get("DAYS_BINANCE_1H", 60)))
        self._days_bin_15m.setValue(int(cfg.get("DAYS_BINANCE_15M", 30)))
        self._days_bin_5m.setValue(int(cfg.get("DAYS_BINANCE_5M", 7)))
        self._days_byb_1h.setValue(int(cfg.get("DAYS_BYBIT_1H", 60)))
        self._days_byb_15m.setValue(int(cfg.get("DAYS_BYBIT_15M", 30)))
        self._days_byb_5m.setValue(int(cfg.get("DAYS_BYBIT_5M", 7)))

        self._atr_winsor.setValue(float(cfg.get("ATR_WINSOR_PCT", 95.0)))
        self._outlier_ratio.setValue(float(cfg.get("OUTLIER_DAY_MAX_RATIO", 2.5)))
        self._outlier_min_weight.setValue(float(cfg.get("OUTLIER_DAY_MIN_WEIGHT", 0.15)))

        self._workers_spin.setValue(int(cfg.get("DOWNLOAD_WORKERS", 4)))
        self._tz_offset_spin.setValue(int(cfg.get("TZ_OFFSET_HOURS", 0)))
        self._console_top_spin.setValue(int(cfg.get("CONSOLE_TOP_ROWS", 0)))
        self._save_csv_cb.setChecked(bool(cfg.get("SAVE_SORTED_CSV", False)))

        self._oi_tf_combo.setCurrentText(str(cfg.get("OI_DEFAULT_TIMEFRAME", "1h")))
        self._oi_z_window.setValue(int(cfg.get("OI_Z_WINDOW", 60)))
        self._oi_visible.setValue(int(cfg.get("OI_VISIBLE_BARS", 200)))
        self._oi_refresh.setValue(int(cfg.get("OI_REFRESH_SEC", 20)))
        self._oi_use_bybit.setChecked(bool(cfg.get("OI_USE_BYBIT", True)))
        self._oi_threshold_oi.setValue(float(cfg.get("OI_THRESHOLD_OI", 2.0)))
        self._oi_threshold_delta.setValue(float(cfg.get("OI_THRESHOLD_DELTA", 1.5)))
        self._oi_absorption_min_z.setValue(
            float(cfg.get("OI_ABSORPTION_MIN_Z_DELTA", 1.5))
        )
        self._oi_absorption_ratio.setValue(
            float(cfg.get("OI_ABSORPTION_CLOSE_RATIO", 0.35))
        )
        self._oi_spot_sync.setValue(float(cfg.get("OI_SPOT_SYNC_MIN_Z", 1.0)))
        self._oi_spot_div.setValue(float(cfg.get("OI_SPOT_DIV_MIN_Z", 0.8)))
        self._oi_spot_dead.setValue(float(cfg.get("OI_SPOT_DEAD_MAX_Z", 0.3)))
        self._oi_spot_dead_extreme.setValue(
            float(cfg.get("OI_SPOT_DEAD_EXTREME_OI_Z", 2.5))
        )

        # Таблица ТФ -> ATR / Half-Life
        atr_map: dict[str, Any] = cfg.get("ATR_LOOKBACK_DAYS", {})
        hl_map: dict[str, Any] = cfg.get("HALF_LIFE_DAYS", {})
        all_keys = sorted(
            {str(k) for k in atr_map.keys()} | {str(k) for k in hl_map.keys()},
            key=lambda x: int(x),
        )
        self._tf_table.setRowCount(len(all_keys))
        for r, key in enumerate(all_keys):
            atr_val = atr_map.get(key, 14)
            hl_val = hl_map.get(key, 7.0)
            self._tf_table.setItem(r, 0, QTableWidgetItem(str(key)))
            self._tf_table.setItem(r, 1, QTableWidgetItem(str(atr_val)))
            self._tf_table.setItem(r, 2, QTableWidgetItem(str(hl_val)))

        # Таблица правил округления масштаба
        rules: list[list[Any]] = cfg.get("SCALE_ROUNDING_RULES", [])
        self._rules_table.setRowCount(len(rules))
        for r, pair in enumerate(rules):
            limit, step = pair[0], pair[1]
            limit_str = "" if limit is None else str(limit)
            self._rules_table.setItem(r, 0, QTableWidgetItem(limit_str))
            self._rules_table.setItem(r, 1, QTableWidgetItem(str(step)))

    def _collect_config(self) -> dict[str, Any]:
        atr_map: dict[str, int] = {}
        hl_map: dict[str, float] = {}

        for r in range(self._tf_table.rowCount()):
            k_item = self._tf_table.item(r, 0)
            a_item = self._tf_table.item(r, 1)
            h_item = self._tf_table.item(r, 2)

            k_txt = k_item.text().strip() if k_item else ""
            a_txt = a_item.text().strip() if a_item else ""
            h_txt = h_item.text().strip() if h_item else ""
            if not k_txt and not a_txt and not h_txt:
                continue

            tf_min = int(k_txt)
            if tf_min <= 0:
                raise ValueError(f"Строка {r + 1}: таймфрейм должен быть положительным числом")
            atr_days = int(a_txt)
            hl_days = float(h_txt.replace(",", "."))
            if atr_days <= 0 or hl_days <= 0:
                raise ValueError(f"Строка {r + 1}: ATR Lookback и Half-Life должны быть > 0")

            atr_map[str(tf_min)] = atr_days
            hl_map[str(tf_min)] = hl_days

        rules: list[list[Any]] = []
        for r in range(self._rules_table.rowCount()):
            lim_item = self._rules_table.item(r, 0)
            step_item = self._rules_table.item(r, 1)

            lim_txt = lim_item.text().strip() if lim_item else ""
            step_txt = step_item.text().strip() if step_item else ""
            if not lim_txt and not step_txt:
                continue

            if lim_txt.lower() in ("", "none", "null", "inf", "∞"):
                lim_val: int | float | None = None
            else:
                num = float(lim_txt.replace(",", "."))
                lim_val = int(num) if num.is_integer() else num

            step_val = int(step_txt)
            if step_val <= 0:
                raise ValueError(f"Правило {r + 1}: шаг округления должен быть > 0")
            rules.append([lim_val, step_val])

        # Берём актуальный сырой конфиг, чтобы не затереть ключи, которых нет на форме
        # (например, UI-состояние тулбара OI & Delta Regime).
        cfg = self._config.load_raw()
        cfg.update({
            "SYMBOL": self._symbol_edit.text().strip().upper(),
            "SPOT_CLUSTERS": self._spot_clusters_spin.value(),
            "FUTURES_CLUSTERS": self._fut_clusters_spin.value(),
            "CLUSTER_MODE": self._cluster_mode_combo.currentText(),
            "DAYS_BINANCE_4H": self._days_bin_4h.value(),
            "DAYS_BINANCE_1H": self._days_bin_1h.value(),
            "DAYS_BINANCE_15M": self._days_bin_15m.value(),
            "DAYS_BINANCE_5M": self._days_bin_5m.value(),
            "DAYS_BYBIT_1H": self._days_byb_1h.value(),
            "DAYS_BYBIT_15M": self._days_byb_15m.value(),
            "DAYS_BYBIT_5M": self._days_byb_5m.value(),
            "ATR_LOOKBACK_DAYS": atr_map,
            "ATR_WINSOR_PCT": round(self._atr_winsor.value(), 2),
            "SCALE_ROUNDING_RULES": rules,
            "HALF_LIFE_DAYS": hl_map,
            "OUTLIER_DAY_MAX_RATIO": round(self._outlier_ratio.value(), 3),
            "OUTLIER_DAY_MIN_WEIGHT": round(self._outlier_min_weight.value(), 3),
            "DOWNLOAD_WORKERS": self._workers_spin.value(),
            "TZ_OFFSET_HOURS": self._tz_offset_spin.value(),
            "CONSOLE_TOP_ROWS": self._console_top_spin.value(),
            "SAVE_SORTED_CSV": self._save_csv_cb.isChecked(),
            "OI_DEFAULT_TIMEFRAME": self._oi_tf_combo.currentText(),
            "OI_Z_WINDOW": self._oi_z_window.value(),
            "OI_THRESHOLD_OI": round(self._oi_threshold_oi.value(), 2),
            "OI_THRESHOLD_DELTA": round(self._oi_threshold_delta.value(), 2),
            "OI_ABSORPTION_CLOSE_RATIO": round(self._oi_absorption_ratio.value(), 2),
            "OI_ABSORPTION_MIN_Z_DELTA": round(self._oi_absorption_min_z.value(), 2),
            "OI_SPOT_SYNC_MIN_Z": round(self._oi_spot_sync.value(), 2),
            "OI_SPOT_DIV_MIN_Z": round(self._oi_spot_div.value(), 2),
            "OI_SPOT_DEAD_MAX_Z": round(self._oi_spot_dead.value(), 2),
            "OI_SPOT_DEAD_EXTREME_OI_Z": round(self._oi_spot_dead_extreme.value(), 2),
            "OI_USE_BYBIT": self._oi_use_bybit.isChecked(),
            "OI_VISIBLE_BARS": self._oi_visible.value(),
            "OI_REFRESH_SEC": self._oi_refresh.value(),
        })
        return cfg

    # ==================== ОБРАБОТЧИКИ КНОПОК ====================

    def _add_tf_row(self) -> None:
        r = self._tf_table.rowCount()
        self._tf_table.insertRow(r)
        self._tf_table.setItem(r, 0, QTableWidgetItem("30"))
        self._tf_table.setItem(r, 1, QTableWidgetItem("14"))
        self._tf_table.setItem(r, 2, QTableWidgetItem("7.0"))

    def _add_rule_row(self) -> None:
        r = self._rules_table.rowCount()
        self._rules_table.insertRow(r)
        self._rules_table.setItem(r, 0, QTableWidgetItem(""))
        self._rules_table.setItem(r, 1, QTableWidgetItem("100"))

    def _remove_selected_row(self, table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)

    def _save(self, recalculate: bool) -> None:
        try:
            raw = self._collect_config()
            clean = self._config.save_raw(raw)
        except Exception as exc:
            QMessageBox.warning(self, "Ошибка в настройках", str(exc))
            return

        self._populate_fields(clean)
        self.saved.emit(clean, recalculate)

    def _reset_defaults(self) -> None:
        ans = QMessageBox.question(
            self,
            "Сбросить настройки?",
            "Вернуть все параметры config.json к значениям по умолчанию?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        clean = self._config.reset_defaults()
        self._populate_fields(clean)
        self.saved.emit(clean, False)
        self.toast_requested.emit("✓ Настройки сброшены к значениям по умолчанию")

    def _open_config_file(self) -> None:
        path = self._config.config_path
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.toast_requested.emit(f"Открыт {path.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось открыть файл", str(exc))