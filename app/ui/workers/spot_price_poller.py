"""Фоновый опрос цены Binance Spot."""

import threading

from PySide6.QtCore import QObject, QThread, Signal

from app.services.spot_price import fetch_binance_spot_price


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
                price = fetch_binance_spot_price(symbol)
                if price is not None and not self._stop_event.is_set():
                    with self._lock:
                        still_same = self._symbol == symbol
                    if still_same:
                        self.price_updated.emit(symbol, price)

            self._wake_event.wait(self._interval_sec)
