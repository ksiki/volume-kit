import importlib
import json
import logging
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

log = logging.getLogger("Backend")

_BINANCE_SPOT_PRICE_URLS = (
    "https://api.binance.com/api/v3/ticker/price",
    "https://data-api.binance.vision/api/v3/ticker/price",
)


class QtLogBridge(QObject):
    """Мост между стандартным logging и Qt-интерфейсом."""

    log_received = Signal(dict)


class _QtLogHandler(logging.Handler):
    def __init__(self, bridge: QtLogBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
            self._bridge.log_received.emit(entry)
        except Exception:
            pass


def install_qt_logging(bridge: QtLogBridge) -> logging.Handler:
    """Подключает перехват всех логов Python к QtLogBridge без дублирования."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for handler in list(root.handlers):
        if isinstance(handler, _QtLogHandler):
            root.removeHandler(handler)
        elif getattr(sys, "frozen", False):
            root.removeHandler(handler)

    qt_handler = _QtLogHandler(bridge)
    qt_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(qt_handler)
    return qt_handler


def reload_app_modules():
    """Перечитывает config.json и обновляет параметры всех расчётных модулей."""
    from .. import analytics, config, main as main_mod, market_data, storage

    importlib.reload(config)
    importlib.reload(market_data)
    importlib.reload(storage)
    importlib.reload(analytics)
    importlib.reload(main_mod)
    return config, main_mod


def _fetch_binance_spot_price(symbol: str, timeout: float = 3.0) -> float | None:
    """Запрашивает текущую цену на Binance Spot для указанного тикера."""
    query = urllib.parse.urlencode({"symbol": symbol})
    headers = {"User-Agent": "Volume Kit/1.0"}

    for base_url in _BINANCE_SPOT_PRICE_URLS:
        req = urllib.request.Request(f"{base_url}?{query}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            price = float(data["price"])
            if price > 0:
                return price
        except Exception:
            continue
    return None


class SpotPricePoller(QThread):
    """Фоновый опрос цены Binance Spot с заданным интервалом."""

    price_updated = Signal(str, float)

    def __init__(
        self,
        symbol: str,
        interval_sec: float = 2.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._symbol = symbol.strip().upper()
        self._interval_sec = max(1.0, float(interval_sec))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    def set_symbol(self, symbol: str) -> None:
        clean = symbol.strip().upper()
        if not clean:
            return
        with self._lock:
            changed = clean != self._symbol
            self._symbol = clean
        if changed:
            self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self.requestInterruption()

    def run(self) -> None:
        while not self._stop_event.is_set() and not self.isInterruptionRequested():
            self._wake_event.clear()
            with self._lock:
                symbol = self._symbol

            if symbol:
                price = _fetch_binance_spot_price(symbol)
                if price is not None and not self._stop_event.is_set():
                    with self._lock:
                        still_same = self._symbol == symbol
                    if still_same:
                        self.price_updated.emit(symbol, price)

            self._wake_event.wait(self._interval_sec)


class CalibrationWorker(QThread):
    """Фоновый поток выполнения калибровки без блокировки UI."""

    progress_changed = Signal(int, str)
    symbol_changed = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, reload_cfg: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reload_cfg = reload_cfg

    def run(self) -> None:
        try:
            self.progress_changed.emit(
                2,
                "Чтение config.json..." if self._reload_cfg else "Запуск калибратора...",
            )

            if self._reload_cfg:
                cfg_mod, main_mod = reload_app_modules()
                self.symbol_changed.emit(cfg_mod.SYMBOL)
            else:
                from .. import config as cfg_mod, main as main_mod
                self.symbol_changed.emit(cfg_mod.SYMBOL)

            def _on_progress(pct: int, stage: str) -> None:
                self.progress_changed.emit(pct, stage)

            bundle = main_mod.run_calibration(progress_cb=_on_progress)
            self.finished_ok.emit(bundle.report)

        except Exception as exc:
            log.exception(f"Ошибка калибровки: {exc}")
            self.failed.emit(str(exc))