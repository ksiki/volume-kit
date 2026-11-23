import html
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets import ExchangeDot, MetricBox, TfBadge, ThresholdButton


class ScaleTile(QFrame):
    """Плитка параметров сжатия кластеров для одной биржи/ТФ."""

    copied = Signal(str, str)

    def __init__(self, scale_data: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScaleTile")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # Заголовок плитки: биржа + ТФ
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        dot = ExchangeDot(scale_data["exchange_key"], size=8)
        ex_lbl = QLabel(scale_data["exchange"])
        ex_lbl.setObjectName("ExchangeName")
        tf_badge = TfBadge(scale_data["timeframe"])

        head.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(ex_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        head.addWidget(tf_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        # Метрики: Масштаб (кликабельный), Шаг цены (только информация)
        metrics_row = QHBoxLayout()
        metrics_row.setContentsMargins(0, 0, 0, 0)
        metrics_row.setSpacing(8)

        scale_box = MetricBox(
            title="Масштаб",
            value=str(scale_data["price_scale"]),
            copy_label=f"Масштаб {scale_data['exchange']} {scale_data['timeframe']}",
            primary=True,
            copyable=True,
        )
        scale_box.copied.connect(self.copied.emit)

        step_box = MetricBox(
            title="Шаг цены",
            value=str(scale_data["price_step_str"]),
            primary=False,
            copyable=False,
        )

        metrics_row.addWidget(scale_box, 5)
        metrics_row.addWidget(step_box, 4)
        layout.addLayout(metrics_row)

        # Метаданные: ATR и окно / half-life
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(2, 0, 2, 0)
        meta_row.setSpacing(8)

        atr_lbl = QLabel(f"ATR <b>{scale_data['atr']:.2f}</b>")
        atr_lbl.setObjectName("ScaleMeta")

        win_lbl = QLabel(
            f"Окно <b>{scale_data['days']}д</b> / HL <b>{scale_data['half_life_days']}д</b>"
        )
        win_lbl.setObjectName("ScaleMeta")

        meta_row.addWidget(atr_lbl)
        meta_row.addStretch(1)
        meta_row.addWidget(win_lbl)
        layout.addLayout(meta_row)


class CategoryCard(QFrame):
    """Карточка одной торговой категории (Трейдинг 15m или Трейдинг 1h)."""

    copied = Signal(str, str)

    def __init__(self, category: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Шапка карточки
        header = QFrame()
        header.setObjectName("CardHeader")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 14, 20, 14)
        h_layout.setSpacing(10)

        title = QLabel(category["title"])
        title.setObjectName("CardTitle")

        spot_tf_badge = TfBadge(
            f"Spot {category['spot_tf']}", tf_key=category["spot_tf"], compact=True
        )
        fut_tf_badge = TfBadge(
            f"Fut {category['fut_tf']}", tf_key=category["fut_tf"], compact=True
        )

        h_layout.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        h_layout.addStretch(1)
        h_layout.addWidget(spot_tf_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        h_layout.addWidget(fut_tf_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(header)

        # Тело карточки
        body = QVBoxLayout()
        body.setContentsMargins(20, 16, 20, 18)
        body.setSpacing(16)

        # Раздел 1: Масштаб и шаг цены
        sec1_lbl = QLabel("1. МАСШТАБ И ШАГ ЦЕНЫ (СЖАТИЕ КЛАСТЕРОВ)")
        sec1_lbl.setObjectName("SectionLabel")
        body.addWidget(sec1_lbl)

        scales_grid = QGridLayout()
        scales_grid.setContentsMargins(0, 0, 0, 0)
        scales_grid.setHorizontalSpacing(12)
        scales_grid.setVerticalSpacing(12)

        for idx, scale_data in enumerate(category["scales"]):
            tile = ScaleTile(scale_data)
            tile.copied.connect(self.copied.emit)
            scales_grid.addWidget(tile, 0, idx)
            scales_grid.setColumnStretch(idx, 1)

        body.addLayout(scales_grid)

        # Разделитель секций
        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        body.addWidget(divider)

        # Раздел 2: Фильтры ClusterSearch
        sec2_lbl = QLabel("2. ФИЛЬТРЫ CLUSTERSEARCH")
        sec2_lbl.setObjectName("SectionLabel")
        body.addWidget(sec2_lbl)

        # Шапка таблицы фильтров
        tbl_head = QGridLayout()
        tbl_head.setContentsMargins(12, 0, 12, 0)
        tbl_head.setHorizontalSpacing(10)

        headers = [
            ("ИНСТРУМЕНТ / ТФ", Qt.AlignmentFlag.AlignLeft, 28),
            ("ФИЛЬТР", Qt.AlignmentFlag.AlignLeft, 22),
            ("ПОРОГ", Qt.AlignmentFlag.AlignRight, 22),
            ("~USD", Qt.AlignmentFlag.AlignRight, 14),
            ("ПОЗИЦИЯ", Qt.AlignmentFlag.AlignRight, 14),
        ]
        for col, (text, align, stretch) in enumerate(headers):
            lbl = QLabel(text)
            lbl.setObjectName("TableHead")
            lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            tbl_head.addWidget(lbl, 0, col)
            tbl_head.setColumnStretch(col, stretch)

        body.addLayout(tbl_head)

        # Строки фильтров
        rows_box = QVBoxLayout()
        rows_box.setContentsMargins(0, 0, 0, 0)
        rows_box.setSpacing(7)

        for f in category["filters"]:
            row_frame = QFrame()
            row_frame.setObjectName("FilterRow")
            row_grid = QGridLayout(row_frame)
            row_grid.setContentsMargins(12, 8, 12, 8)
            row_grid.setHorizontalSpacing(10)

            # Колонка 0: Биржа + ТФ
            inst_wrap = QWidget()
            inst_lay = QHBoxLayout(inst_wrap)
            inst_lay.setContentsMargins(0, 0, 0, 0)
            inst_lay.setSpacing(7)

            is_spot = f["exchange_key"] == "binance_spot"
            short_name = "Binance Spot" if is_spot else "ByBit Fut"

            inst_lay.addWidget(
                ExchangeDot(f["exchange_key"], size=7), 0, Qt.AlignmentFlag.AlignVCenter
            )
            name_lbl = QLabel(short_name)
            name_lbl.setStyleSheet("font-weight: 600; font-size: 13px;")
            inst_lay.addWidget(name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
            inst_lay.addWidget(
                TfBadge(f["timeframe"], compact=True), 0, Qt.AlignmentFlag.AlignVCenter
            )
            inst_lay.addStretch(1)

            # Колонка 1: Фильтр
            metric_lbl = QLabel(f["metric"])
            metric_lbl.setObjectName("FilterMetricName")

            # Колонка 2: Порог (кнопка копирования)
            thresh_wrap = QWidget()
            thresh_lay = QHBoxLayout(thresh_wrap)
            thresh_lay.setContentsMargins(0, 0, 0, 0)
            thresh_lay.addStretch(1)
            btn_thresh = ThresholdButton(
                value=f["threshold_str"],
                metric_type=f["metric_type"],
                copy_label=f"{f['metric']} {f['timeframe']}",
            )
            btn_thresh.copied.connect(self.copied.emit)
            thresh_lay.addWidget(btn_thresh)

            # Колонка 3: ~USD
            usd_lbl = QLabel(f["usd_str"])
            usd_lbl.setObjectName("FilterUsd")
            usd_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            # Колонка 4: Позиция
            pos_lbl = QLabel(f["pos_str"])
            pos_lbl.setObjectName("FilterPos")
            pos_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            row_grid.addWidget(inst_wrap, 0, 0)
            row_grid.addWidget(metric_lbl, 0, 1)
            row_grid.addWidget(thresh_wrap, 0, 2)
            row_grid.addWidget(usd_lbl, 0, 3)
            row_grid.addWidget(pos_lbl, 0, 4)

            for col, (_, _, stretch) in enumerate(headers):
                row_grid.setColumnStretch(col, stretch)

            rows_box.addWidget(row_frame)

        body.addLayout(rows_box)
        root.addLayout(body)


class CalibratorPage(QWidget):
    """Инструмент Calibrator внутри Volume Kit."""

    copied = Signal(str, str)
    open_logs_requested = Signal()
    recalc_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_report: bool = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Тонкий баннер прогресса при пересчёте поверх существующих данных
        self._top_banner = QFrame()
        self._top_banner.setObjectName("ProgressBanner")
        self._top_banner.hide()

        banner_lay = QVBoxLayout(self._top_banner)
        banner_lay.setContentsMargins(24, 10, 24, 10)
        banner_lay.setSpacing(6)

        banner_top = QHBoxLayout()
        banner_top.setSpacing(12)

        self._banner_stage = QLabel("Обновление данных...")
        self._banner_stage.setStyleSheet("font-weight: 600; font-size: 12.5px; color: #1D4ED8;")

        self._banner_log = QLabel("")
        self._banner_log.setObjectName("ScaleMeta")
        self._banner_log.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        btn_banner_logs = QPushButton("Логи")
        btn_banner_logs.setObjectName("FilterChip")
        btn_banner_logs.clicked.connect(self.open_logs_requested.emit)

        banner_top.addWidget(self._banner_stage)
        banner_top.addStretch(1)
        banner_top.addWidget(self._banner_log)
        banner_top.addWidget(btn_banner_logs)

        self._banner_progress = QProgressBar()
        self._banner_progress.setObjectName("ThinProgress")
        self._banner_progress.setRange(0, 100)
        self._banner_progress.setTextVisible(False)

        banner_lay.addLayout(banner_top)
        banner_lay.addWidget(self._banner_progress)
        root.addWidget(self._top_banner)

        # Стек состояний: 0 = загрузка, 1 = ошибка (без данных), 2 = дашборд
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._build_loading_view()
        self._build_error_view()
        self._build_dashboard_view()

    def _build_loading_view(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 48, 28, 28)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("Card")
        card.setMaximumWidth(660)
        card.setMinimumWidth(520)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(12)

        top = QHBoxLayout()
        self._load_title = QLabel("Калибровка...")
        self._load_title.setObjectName("CardTitle")

        self._load_pct = QLabel("0%")
        self._load_pct.setStyleSheet(
            "font-family: 'Cascadia Code', Consolas, monospace; "
            "font-size: 14px; font-weight: 700; color: #2563EB;"
        )

        top.addWidget(self._load_title)
        top.addStretch(1)
        top.addWidget(self._load_pct)
        lay.addLayout(top)

        self._load_progress = QProgressBar()
        self._load_progress.setObjectName("ThinProgress")
        self._load_progress.setRange(0, 100)
        self._load_progress.setTextVisible(False)
        lay.addWidget(self._load_progress)

        self._load_stage = QLabel("Инициализация...")
        self._load_stage.setObjectName("SettingsHint")
        lay.addWidget(self._load_stage)

        self._mini_log = QPlainTextEdit()
        self._mini_log.setObjectName("LogView")
        self._mini_log.setReadOnly(True)
        self._mini_log.setFixedHeight(150)
        lay.addWidget(self._mini_log)

        actions = QHBoxLayout()
        actions.addStretch(1)
        btn_logs = QPushButton("Все логи")
        btn_logs.setObjectName("SecondaryBtn")
        btn_logs.clicked.connect(self.open_logs_requested.emit)
        actions.addWidget(btn_logs)
        lay.addLayout(actions)

        center_row = QHBoxLayout()
        center_row.addStretch(1)
        center_row.addWidget(card)
        center_row.addStretch(1)

        outer.addLayout(center_row)
        outer.addStretch(2)
        self._stack.addWidget(page)

    def _build_error_view(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 48, 28, 28)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("Card")
        card.setMaximumWidth(660)
        card.setMinimumWidth(520)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(14)

        title = QLabel("✕  Ошибка калибровки")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #DC2626;")
        lay.addWidget(title)

        err_box = QFrame()
        err_box.setObjectName("ErrorBanner")
        err_lay = QVBoxLayout(err_box)
        err_lay.setContentsMargins(14, 12, 14, 12)

        self._error_full_lbl = QLabel("")
        self._error_full_lbl.setObjectName("ErrorText")
        self._error_full_lbl.setWordWrap(True)
        self._error_full_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        err_lay.addWidget(self._error_full_lbl)
        lay.addWidget(err_box)

        actions = QHBoxLayout()
        actions.addStretch(1)

        btn_logs = QPushButton("Открыть логи")
        btn_logs.setObjectName("SecondaryBtn")
        btn_logs.clicked.connect(self.open_logs_requested.emit)

        btn_retry = QPushButton("Повторить")
        btn_retry.setObjectName("PrimaryBtn")
        btn_retry.clicked.connect(self.recalc_requested.emit)

        actions.addWidget(btn_logs)
        actions.addWidget(btn_retry)
        lay.addLayout(actions)

        center_row = QHBoxLayout()
        center_row.addStretch(1)
        center_row.addWidget(card)
        center_row.addStretch(1)

        outer.addLayout(center_row)
        outer.addStretch(2)
        self._stack.addWidget(page)

    def _build_dashboard_view(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        self._dash_root = QVBoxLayout(container)
        self._dash_root.setContentsMargins(24, 22, 24, 28)
        self._dash_root.setSpacing(18)

        # Баннер ошибки пересчёта поверх старых данных
        self._dash_error_banner = QFrame()
        self._dash_error_banner.setObjectName("ErrorBanner")
        self._dash_error_banner.hide()
        eb_lay = QHBoxLayout(self._dash_error_banner)
        eb_lay.setContentsMargins(14, 10, 14, 10)
        self._dash_error_lbl = QLabel("")
        self._dash_error_lbl.setObjectName("ErrorText")
        self._dash_error_lbl.setWordWrap(True)
        eb_lay.addWidget(self._dash_error_lbl, 1)
        self._dash_root.addWidget(self._dash_error_banner)

        # Сетка карточек стратегий (2 колонки)
        self._cards_wrap = QWidget()
        self._cards_opacity = QGraphicsOpacityEffect(self._cards_wrap)
        self._cards_opacity.setOpacity(1.0)
        self._cards_wrap.setGraphicsEffect(self._cards_opacity)

        self._cards_grid = QGridLayout(self._cards_wrap)
        self._cards_grid.setContentsMargins(0, 0, 0, 0)
        self._cards_grid.setHorizontalSpacing(20)
        self._cards_grid.setVerticalSpacing(20)
        self._dash_root.addWidget(self._cards_wrap)

        self._dash_root.addStretch(1)

        scroll.setWidget(container)
        self._stack.addWidget(scroll)

    # ==================== ПУБЛИЧНЫЕ МЕТОДЫ СОСТОЯНИЯ ====================

    def set_running(self, symbol: str, progress: int, stage: str) -> None:
        self._dash_error_banner.hide()
        if self._has_report:
            self._top_banner.show()
            self._banner_stage.setText(f"{stage} ({progress}%)")
            self._banner_progress.setValue(progress)
            self._cards_opacity.setOpacity(0.6)
            self._stack.setCurrentIndex(2)
        else:
            self._top_banner.hide()
            self._load_title.setText(f"Калибровка {symbol}")
            self._load_pct.setText(f"{progress}%")
            self._load_progress.setValue(progress)
            self._load_stage.setText(stage)
            self._stack.setCurrentIndex(0)

    def update_progress(self, progress: int, stage: str) -> None:
        if self._has_report:
            self._banner_stage.setText(f"{stage} ({progress}%)")
            self._banner_progress.setValue(progress)
        else:
            self._load_pct.setText(f"{progress}%")
            self._load_progress.setValue(progress)
            self._load_stage.setText(stage)

    def set_symbol(self, symbol: str) -> None:
        self._load_title.setText(f"Калибровка {symbol}")

    def clear_mini_logs(self) -> None:
        self._mini_log.clear()
        self._banner_log.setText("")

    def append_log_entry(self, entry: dict[str, Any]) -> None:
        t = html.escape(str(entry.get("time", "")))
        logger_name = html.escape(str(entry.get("logger", "")))
        msg = html.escape(str(entry.get("message", "")))

        self._banner_log.setText(f"[{entry.get('logger', '')}] {entry.get('message', '')}")
        self._mini_log.appendHtml(
            f'<span style="color:#64748B;">{t}</span> '
            f'<span style="color:#6366F1;">[{logger_name}]</span> '
            f'<span style="color:#0F172A;">{msg}</span>'
        )
        sb = self._mini_log.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def set_report(self, report: dict[str, Any]) -> None:
        self._has_report = True
        self._top_banner.hide()
        self._dash_error_banner.hide()
        self._cards_opacity.setOpacity(1.0)

        # Очищаем прошлые карточки
        while self._cards_grid.count():
            item = self._cards_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for idx, cat in enumerate(report.get("categories", [])):
            card = CategoryCard(cat)
            card.copied.connect(self.copied.emit)
            self._cards_grid.addWidget(card, 0, idx, Qt.AlignmentFlag.AlignTop)
            self._cards_grid.setColumnStretch(idx, 1)

        self._stack.setCurrentIndex(2)

    def set_error(self, error_msg: str) -> None:
        self._top_banner.hide()
        self._cards_opacity.setOpacity(1.0)

        if self._has_report:
            self._dash_error_lbl.setText(f"Ошибка пересчёта: {error_msg}")
            self._dash_error_banner.show()
            self._stack.setCurrentIndex(2)
        else:
            self._error_full_lbl.setText(error_msg)
            self._stack.setCurrentIndex(1)