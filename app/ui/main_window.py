import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config as app_config
from .backend import CalibrationWorker, QtLogBridge, SpotPricePoller, install_qt_logging
from .calibrator_page import CalibratorPage
from .logs_panel import LogsPanel
from .settings_page import SettingsPage
from .web_tool_page import WebToolPage
from .widgets import ExchangeDot, ToastNotification, repolish

COINGLASS_LIQ_URL = "https://www.coinglass.com/ru/LiquidationHeatMap/BTC?pair=Binance_BTCUSDT"
COINGLASS_LIQ_CLASS = "liq-heatmap_chartPanel__5Sby3"

COINGLASS_OB_URL = "https://www.coinglass.com/LiquidityHeatmap/BTC"
COINGLASS_OB_CLASS = "ChartCard_card__wWOwA"

SPOT_PRICE_REFRESH_SEC = 2.0


def _format_price(val: Any) -> str:
    if val is None:
        return "—"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "—"

    if num >= 100:
        return f"{num:,.2f}"
    if num >= 1:
        return f"{num:,.4f}"
    return f"{num:,.6f}"


class MainWindow(QMainWindow):
    """Главное окно приложения Volume Kit."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Volume Kit")
        self.setMinimumSize(1120, 700)

        self._worker: CalibrationWorker | None = None
        self._nav_buttons: dict[str, QPushButton] = {}
        self._page_indices: dict[str, int] = {}
        self._page_titles: dict[str, str] = {}
        self._web_pages: dict[str, WebToolPage] = {}
        self._current_page_key: str = "calibrator"
        self._current_symbol: str = str(app_config.SYMBOL).strip().upper()
        self._last_spot_price: float | None = None

        self._price_flash_timer = QTimer(self)
        self._price_flash_timer.setSingleShot(True)
        self._price_flash_timer.timeout.connect(self._reset_spot_price_flash)

        self._log_bridge = QtLogBridge(self)
        self._log_handler = install_qt_logging(self._log_bridge)

        self._price_poller = SpotPricePoller(
            symbol=self._current_symbol,
            interval_sec=SPOT_PRICE_REFRESH_SEC,
            parent=self,
        )

        self._build_ui()
        self._connect_signals()

        self._toast = ToastNotification(self)
        self._price_poller.start()

        # Запускаем калибратор сразу после отрисовки окна
        QTimer.singleShot(80, lambda: self.start_calibration(reload_cfg=False))

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 1. Левая боковая панель навигации
        outer.addWidget(self._build_sidebar())

        # 2. Правая основная рабочая область
        right_wrap = QWidget()
        right_lay = QVBoxLayout(right_wrap)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        right_lay.addWidget(self._build_topbar())

        # Тело: стек страниц инструментов + выдвижная панель логов справа
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._stack = QStackedWidget()
        self._register_pages()
        body.addWidget(self._stack, 1)

        self._logs_panel = LogsPanel()
        self._logs_panel.hide()
        body.addWidget(self._logs_panel)

        right_lay.addLayout(body, 1)
        outer.addWidget(right_wrap, 1)

        self._switch_page("calibrator")

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(236)

        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(14, 16, 14, 16)
        lay.setSpacing(6)

        # Бренд
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 2, 4, 8)
        brand_row.setSpacing(10)

        logo = QLabel("VK")
        logo.setObjectName("BrandLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(36, 36)

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(1)

        title = QLabel("Volume Kit")
        title.setObjectName("BrandTitle")

        subtitle = QLabel("Инструменты трейдера")
        subtitle.setObjectName("BrandSubtitle")

        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)

        brand_row.addWidget(logo)
        brand_row.addLayout(brand_text)
        brand_row.addStretch(1)
        lay.addLayout(brand_row)

        div1 = QFrame()
        div1.setObjectName("SidebarDivider")
        lay.addWidget(div1)

        # Секция инструментов
        sec_tools = QLabel("ИНСТРУМЕНТЫ")
        sec_tools.setObjectName("NavSectionLabel")
        lay.addWidget(sec_tools)

        self._add_nav_button(lay, "calibrator", "⊞  Calibrator")
        self._add_nav_button(lay, "liq_heatmap", "◔  Liquidation Heatmap")
        self._add_nav_button(lay, "ob_heatmap", "▤  Liquidity Heatmap")

        lay.addStretch(1)

        div2 = QFrame()
        div2.setObjectName("SidebarDivider")
        lay.addWidget(div2)

        # Секция системы
        sec_sys = QLabel("СИСТЕМА")
        sec_sys.setObjectName("NavSectionLabel")
        lay.addWidget(sec_sys)

        self._add_nav_button(lay, "settings", "⚙  Настройки")

        return sidebar

    def _add_nav_button(self, layout: QVBoxLayout, key: str, text: str) -> None:
        btn = QPushButton(text)
        btn.setObjectName("NavButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("active", False)
        btn.clicked.connect(lambda _checked=False, k=key: self._switch_page(k))
        repolish(btn)
        self._nav_buttons[key] = btn
        layout.addWidget(btn)

    def _build_topbar(self) -> QFrame:
        topbar = QFrame()
        topbar.setObjectName("TopBar")
        topbar.setFixedHeight(58)

        lay = QHBoxLayout(topbar)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(10)

        self._page_title_lbl = QLabel("Calibrator")
        self._page_title_lbl.setObjectName("PageTitle")

        self._symbol_pill = QLabel(self._current_symbol)
        self._symbol_pill.setObjectName("SymbolPill")
        self._symbol_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._symbol_pill.setFixedHeight(32)

        # Live-чип цены Binance Spot
        self._spot_price_pill = QFrame()
        self._spot_price_pill.setObjectName("SpotPricePill")
        self._spot_price_pill.setFixedHeight(32)
        self._spot_price_pill.setToolTip("Текущая цена Binance Spot (обновляется каждые 2 сек)")

        spot_lay = QHBoxLayout(self._spot_price_pill)
        spot_lay.setContentsMargins(10, 0, 11, 0)
        spot_lay.setSpacing(7)

        spot_dot = ExchangeDot("binance_spot", size=7)
        spot_tag = QLabel("Binance Spot")
        spot_tag.setObjectName("SpotPriceTag")

        self._spot_price_val_lbl = QLabel("—")
        self._spot_price_val_lbl.setObjectName("SpotPriceValue")
        self._spot_price_val_lbl.setProperty("direction", "neutral")
        repolish(self._spot_price_val_lbl)

        spot_lay.addWidget(spot_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        spot_lay.addWidget(spot_tag, 0, Qt.AlignmentFlag.AlignVCenter)
        spot_lay.addWidget(self._spot_price_val_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._status_pill = QLabel("Запуск...")
        self._status_pill.setObjectName("StatusPill")
        self._status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_pill.setFixedHeight(32)
        self._status_pill.setProperty("status", "running")
        repolish(self._status_pill)

        self._recalc_btn = QPushButton("↻ Пересчитать")
        self._recalc_btn.setObjectName("PrimaryBtn")
        self._recalc_btn.setFixedHeight(32)
        self._recalc_btn.setToolTip("Перечитать config.json и обновить расчёт Calibrator")

        self._logs_btn = QPushButton("Логи (0)")
        self._logs_btn.setObjectName("SecondaryBtn")
        self._logs_btn.setFixedHeight(32)
        self._logs_btn.setToolTip("Показать или скрыть панель логов (горячая клавиша L)")

        lay.addWidget(self._page_title_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addSpacing(4)
        lay.addWidget(self._symbol_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._spot_price_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        lay.addWidget(self._status_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._recalc_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._logs_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        return topbar

    def _register_pages(self) -> None:
        # 1. Calibrator
        self._calibrator_page = CalibratorPage()
        self._add_page("calibrator", "Calibrator", self._calibrator_page)

        # 2. Coinglass Liquidation Heatmap
        liq_page = WebToolPage(
            title="Liquidation Heatmap (Binance BTCUSDT)",
            url=COINGLASS_LIQ_URL,
            target_class=COINGLASS_LIQ_CLASS,
        )
        self._web_pages["liq_heatmap"] = liq_page
        self._add_page("liq_heatmap", "Liquidation Heatmap", liq_page)

        # 3. Coinglass Liquidity Heatmap
        ob_page = WebToolPage(
            title="Liquidity Heatmap (BTC)",
            url=COINGLASS_OB_URL,
            target_class=COINGLASS_OB_CLASS,
        )
        self._web_pages["ob_heatmap"] = ob_page
        self._add_page("ob_heatmap", "Liquidity Heatmap", ob_page)

        # 4. Настройки config.json
        self._settings_page = SettingsPage()
        self._add_page("settings", "Настройки (config.json)", self._settings_page)

    def _add_page(self, key: str, title: str, widget: QWidget) -> None:
        idx = self._stack.addWidget(widget)
        self._page_indices[key] = idx
        self._page_titles[key] = title

    def _connect_signals(self) -> None:
        self._log_bridge.log_received.connect(self._on_log_received)
        self._price_poller.price_updated.connect(self._on_spot_price_updated)

        self._recalc_btn.clicked.connect(lambda: self.start_calibration(reload_cfg=True))
        self._logs_btn.clicked.connect(self._toggle_logs_panel)

        self._logs_panel.close_requested.connect(self._logs_panel.hide)
        self._logs_panel.copied.connect(self.show_toast)
        self._logs_panel.counts_changed.connect(self._on_log_counts_changed)

        self._calibrator_page.copied.connect(self._on_value_copied)
        self._calibrator_page.open_logs_requested.connect(self._open_logs_panel)
        self._calibrator_page.recalc_requested.connect(
            lambda: self.start_calibration(reload_cfg=True)
        )

        self._settings_page.saved.connect(self._on_settings_saved)
        self._settings_page.toast_requested.connect(self.show_toast)

    # ==================== НАВИГАЦИЯ ====================

    def _switch_page(self, key: str) -> None:
        if key not in self._page_indices:
            return
        self._current_page_key = key
        self._stack.setCurrentIndex(self._page_indices[key])
        self._page_title_lbl.setText(self._page_titles[key])

        for btn_key, btn in self._nav_buttons.items():
            btn.setProperty("active", btn_key == key)
            repolish(btn)

        # Ленивая загрузка веб-инструментов при первом открытии
        if key in self._web_pages:
            self._web_pages[key].ensure_loaded()

    def _toggle_logs_panel(self) -> None:
        if self._logs_panel.isVisible():
            self._logs_panel.hide()
        else:
            self._logs_panel.show()

    def _open_logs_panel(self) -> None:
        self._logs_panel.show()

    # ==================== LIVE ЦЕНА BINANCE SPOT ====================

    def _update_active_symbol(self, symbol: str) -> None:
        clean = str(symbol).strip().upper()
        if not clean:
            return
        changed = clean != self._current_symbol
        self._current_symbol = clean
        self._symbol_pill.setText(clean)
        if changed:
            self._last_spot_price = None
            self._spot_price_val_lbl.setText("—")
            self._reset_spot_price_flash()
            self._price_poller.set_symbol(clean)

    def _on_spot_price_updated(self, symbol: str, price: float) -> None:
        if symbol != self._current_symbol:
            return

        direction: str | None = None
        if self._last_spot_price is not None:
            if price > self._last_spot_price:
                direction = "up"
            elif price < self._last_spot_price:
                direction = "down"

        self._last_spot_price = price
        self._spot_price_val_lbl.setText(_format_price(price))

        if direction is not None:
            self._spot_price_val_lbl.setProperty("direction", direction)
            repolish(self._spot_price_val_lbl)
            self._price_flash_timer.start(900)

    def _reset_spot_price_flash(self) -> None:
        self._spot_price_val_lbl.setProperty("direction", "neutral")
        repolish(self._spot_price_val_lbl)

    # ==================== КАЛИБРАЦИЯ ====================

    def start_calibration(self, reload_cfg: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.show_toast("Расчёт уже выполняется")
            return

        if reload_cfg:
            self._logs_panel.clear_logs()
            self._calibrator_page.clear_mini_logs()

        self._recalc_btn.setEnabled(False)
        self._set_status("running", "Расчёт 0%")
        self._calibrator_page.set_running(
            symbol=self._current_symbol,
            progress=2,
            stage="Чтение config.json..." if reload_cfg else "Запуск калибратора...",
        )

        worker = CalibrationWorker(reload_cfg=reload_cfg, parent=self)
        worker.progress_changed.connect(self._on_calc_progress)
        worker.symbol_changed.connect(self._on_symbol_changed)
        worker.finished_ok.connect(self._on_calc_finished)
        worker.failed.connect(self._on_calc_failed)
        worker.finished.connect(self._on_worker_cleanup)

        self._worker = worker
        worker.start()

    def _on_calc_progress(self, pct: int, stage: str) -> None:
        self._set_status("running", f"Расчёт {pct}%")
        self._calibrator_page.update_progress(pct, stage)

    def _on_symbol_changed(self, symbol: str) -> None:
        self._update_active_symbol(symbol)
        self._calibrator_page.set_symbol(self._current_symbol)

    def _on_calc_finished(self, report: dict[str, Any]) -> None:
        self._update_active_symbol(str(report.get("symbol", app_config.SYMBOL)))

        # Если live-цена ещё не успела прийти, показываем референсную спот-цену из отчёта
        if self._last_spot_price is None and report.get("spot_ref_price") is not None:
            self._spot_price_val_lbl.setText(_format_price(report.get("spot_ref_price")))

        elapsed = report.get("elapsed_sec", 0)
        updated_at = report.get("updated_at", "")
        self._set_status("done", f"Готово за {elapsed}с · {updated_at}")

        self._calibrator_page.set_report(report)
        self._recalc_btn.setEnabled(True)

    def _on_calc_failed(self, err_msg: str) -> None:
        self._set_status("error", "Ошибка расчёта")
        self._calibrator_page.set_error(err_msg)
        self._recalc_btn.setEnabled(True)

    def _on_worker_cleanup(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _set_status(self, status_key: str, text: str) -> None:
        self._status_pill.setProperty("status", status_key)
        self._status_pill.setText(text)
        repolish(self._status_pill)

    # ==================== ОБРАБОТЧИКИ СОБЫТИЙ ====================

    def _on_log_received(self, entry: dict[str, Any]) -> None:
        self._logs_panel.add_entry(entry)
        self._calibrator_page.append_log_entry(entry)

    def _on_log_counts_changed(self, total: int, warns: int, errs: int) -> None:
        if errs > 0:
            self._logs_btn.setText(f"Логи ({errs} ERR)")
        elif warns > 0:
            self._logs_btn.setText(f"Логи ({warns} WARN)")
        else:
            self._logs_btn.setText(f"Логи ({total})")

    def _on_value_copied(self, label: str, value: str) -> None:
        self.show_toast(f"✓ Скопировано ({label}): {value}")

    def _on_settings_saved(self, cfg: dict[str, Any], recalculate: bool) -> None:
        self._update_active_symbol(str(cfg.get("SYMBOL", app_config.SYMBOL)))
        if recalculate:
            self.show_toast("✓ Настройки сохранены — запускаю пересчёт...")
            self._switch_page("calibrator")
            self.start_calibration(reload_cfg=True)
        else:
            self.show_toast("✓ Настройки сохранены в config.json")

    def show_toast(self, message: str) -> None:
        self._toast.show_message(message)

    # ==================== СИСТЕМНЫЕ СОБЫТИЯ ОКНА ====================

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_toast"):
            self._toast.reposition()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._logs_panel.isVisible():
            self._logs_panel.hide()
            event.accept()
            return

        focus_widget = self.focusWidget()
        in_text_input = isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit))
        if not in_text_input and event.text().lower() in ("l", "д"):
            self._toggle_logs_panel()
            event.accept()
            return

        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        logging.getLogger().removeHandler(self._log_handler)
        if self._price_poller.isRunning():
            self._price_poller.stop()
            self._price_poller.wait(800)
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(800)
        super().closeEvent(event)