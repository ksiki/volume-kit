"""Главное окно Volume Kit: оболочка, навигация и сервисы для инструментов."""

import logging
from typing import Any, Callable

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

from app.core.config import ConfigService, get_config_service
from app.core.paths import logo_path
from app.core.tools import BaseTool, CommandBus, ToolContext, ToolRegistry
from app.ui.log_bridge import QtLogBridge, install_qt_logging
from app.ui.logs_panel import LogsPanel
from app.ui.widgets import ExchangeDot, LogoMark, ToastNotification, repolish
from app.ui.workers.spot_price_poller import SpotPricePoller

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

    def __init__(self, registry: ToolRegistry, config: ConfigService | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Volume Kit")
        self.setMinimumSize(1120, 700)

        self._config = config if config is not None else get_config_service()
        self._tool_classes = registry.classes()
        if not self._tool_classes:
            raise RuntimeError("Не зарегистрировано ни одного инструмента")

        self._tools: dict[str, BaseTool] = {}
        self._commands = CommandBus()
        self._nav_buttons: dict[str, QPushButton] = {}
        self._page_indices: dict[str, int] = {}
        self._shown_tools: set[str] = set()
        self._current_tool_id: str = ""
        self._current_symbol: str = str(self._config.current.symbol).strip().upper()
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
        self._create_tools()
        self._connect_signals()

        self._toast = ToastNotification(self)
        self._price_poller.start()

        self._switch_tool(self._tool_classes[0].tool_id)

    # ==================== ПОСТРОЕНИЕ ИНТЕРФЕЙСА ====================

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
        body.addWidget(self._stack, 1)

        self._logs_panel = LogsPanel()
        self._logs_panel.hide()
        body.addWidget(self._logs_panel)

        right_lay.addLayout(body, 1)
        outer.addWidget(right_wrap, 1)

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

        logo = LogoMark(logo_path(), size=36)

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

        groups: dict[str, list[type[BaseTool]]] = {}
        for tool_cls in self._tool_classes:
            groups.setdefault(tool_cls.nav_group, []).append(tool_cls)

        for group_index, (group_name, tool_classes) in enumerate(groups.items()):
            if group_index > 0:
                lay.addStretch(1)
                divider = QFrame()
                divider.setObjectName("SidebarDivider")
                lay.addWidget(divider)

            section = QLabel(group_name)
            section.setObjectName("NavSectionLabel")
            lay.addWidget(section)

            for tool_cls in tool_classes:
                # Экранируем амперсанд, иначе Qt трактует его как мнемонику (OI & Delta -> OI _Delta)
                nav_text = tool_cls.nav_label.replace("&", "&&")
                self._add_nav_button(lay, tool_cls.tool_id, nav_text)

        return sidebar

    def _add_nav_button(self, layout: QVBoxLayout, key: str, text: str) -> None:
        btn = QPushButton(text)
        btn.setObjectName("NavButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("active", False)
        btn.clicked.connect(lambda _checked=False, k=key: self._switch_tool(k))
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

        self._page_title_lbl = QLabel(self._tool_classes[0].title)
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

        self._logs_btn = QPushButton("Логи (0)")
        self._logs_btn.setObjectName("SecondaryBtn")
        self._logs_btn.setFixedHeight(32)
        self._logs_btn.setToolTip("Показать или скрыть панель логов (горячая клавиша L)")

        lay.addWidget(self._page_title_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addSpacing(4)
        lay.addWidget(self._symbol_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._spot_price_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        lay.addWidget(self._logs_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        return topbar

    def _create_tools(self) -> None:
        """Создаёт экземпляры всех инструментов: команды регистрируются сразу, виджеты — лениво."""
        context = ToolContext(config=self._config, host=self, commands=self._commands)
        for tool_cls in self._tool_classes:
            self._tools[tool_cls.tool_id] = tool_cls(context)

    def _connect_signals(self) -> None:
        self._log_bridge.log_received.connect(self._logs_panel.add_entry)
        self._price_poller.price_updated.connect(self._on_spot_price_updated)

        self._logs_btn.clicked.connect(self._toggle_logs_panel)

        self._logs_panel.close_requested.connect(self._logs_panel.hide)
        self._logs_panel.copied.connect(self.show_toast)
        self._logs_panel.counts_changed.connect(self._on_log_counts_changed)

    # ==================== НАВИГАЦИЯ ====================

    def _switch_tool(self, tool_id: str) -> None:
        tool = self._tools.get(tool_id)
        if tool is None:
            return

        # Виджет создаётся по требованию при первом открытии вкладки
        if tool_id not in self._page_indices:
            self._page_indices[tool_id] = self._stack.addWidget(tool.widget)

        self._current_tool_id = tool_id
        self._stack.setCurrentIndex(self._page_indices[tool_id])
        self._page_title_lbl.setText(tool.title)

        for btn_key, btn in self._nav_buttons.items():
            btn.setProperty("active", btn_key == tool_id)
            repolish(btn)

        if tool_id not in self._shown_tools:
            self._shown_tools.add(tool_id)
            tool.on_first_shown()
        tool.on_shown()

    def _toggle_logs_panel(self) -> None:
        if self._logs_panel.isVisible():
            self._logs_panel.hide()
        else:
            self._logs_panel.show()

    # ==================== ToolHost: СЕРВИСЫ ДЛЯ ИНСТРУМЕНТОВ ====================

    @property
    def current_symbol(self) -> str:
        return self._current_symbol

    def show_toast(self, message: str) -> None:
        self._toast.show_message(message)

    def open_logs(self) -> None:
        self._logs_panel.show()

    def set_symbol(self, symbol: str) -> None:
        clean = str(symbol).strip().upper()
        if not clean:
            return
        changed = clean != self._current_symbol
        self._current_symbol = clean
        self._symbol_pill.setText(clean)
        self._price_poller.set_symbol(clean)
        if changed:
            self._last_spot_price = None
            self._spot_price_val_lbl.setText("—")
            self._reset_spot_price_flash()

    def show_reference_price(self, value: Any) -> None:
        """Показывает референсную цену, если live-цена ещё не приходила."""
        if self._last_spot_price is None and value is not None:
            self._spot_price_val_lbl.setText(_format_price(value))

    def switch_tool(self, tool_id: str) -> None:
        self._switch_tool(tool_id)

    def subscribe_logs(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._log_bridge.log_received.connect(callback)

        def _unsubscribe() -> None:
            try:
                self._log_bridge.log_received.disconnect(callback)
            except (RuntimeError, TypeError):
                pass

        return _unsubscribe

    # ==================== LIVE ЦЕНА BINANCE SPOT ====================

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

    # ==================== ОБРАБОТЧИКИ СОБЫТИЙ ====================

    def _on_log_counts_changed(self, total: int, warns: int, errs: int) -> None:
        if errs > 0:
            self._logs_btn.setText(f"Логи ({errs} ERR)")
        elif warns > 0:
            self._logs_btn.setText(f"Логи ({warns} WARN)")
        else:
            self._logs_btn.setText(f"Логи ({total})")

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
            # Ждём штатного завершения; сетевой вызов может зависнуть, поэтому есть жёсткий откат
            if not self._price_poller.wait(2000):
                self._price_poller.terminate()
                self._price_poller.wait(500)
        for tool in self._tools.values():
            tool.shutdown()
        super().closeEvent(event)
