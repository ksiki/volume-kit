"""Фоновая проверка прокси через Bybit и Binance."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

from app.core.http_client import ProxyAwareSession
from app.core.proxy import ProxyEntry, sanitize_error

log = logging.getLogger(__name__)

BYBIT_TIME_URL = "https://api.bybit.com/v5/market/time"
BINANCE_PING_URL = "https://api.binance.com/api/v3/ping"


class ProxyCheckWorker(QThread):
    """Проверяет один или несколько прокси, не блокируя UI."""

    # proxy_id, state ("ok"|"warn"|"error"), text, tooltip
    checked = Signal(str, str, str, str)
    finished_all = Signal()

    def __init__(
        self,
        entries: list[ProxyEntry],
        timeout: float = 6.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._entries = [entry.copy() for entry in entries]
        self._timeout = max(1.0, float(timeout))

    def _probe(self, entry: ProxyEntry, url: str) -> tuple[bool, float, str]:
        started = time.perf_counter()
        try:
            with ProxyAwareSession(proxy_override=entry) as session:
                response = session.get(url, timeout=(3.05, self._timeout))
                response.raise_for_status()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return True, elapsed_ms, ""
        except Exception as exc:  # noqa: BLE001 - любая ошибка = неуспешная проверка
            return False, 0.0, sanitize_error(exc)

    def _check_one(self, entry: ProxyEntry) -> tuple[str, str, str, str]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            bybit_future = pool.submit(self._probe, entry, BYBIT_TIME_URL)
            binance_future = pool.submit(self._probe, entry, BINANCE_PING_URL)
            bybit_ok, bybit_ms, bybit_err = bybit_future.result()
            binance_ok, binance_ms, binance_err = binance_future.result()

        if bybit_ok and binance_ok:
            ms = max(bybit_ms, binance_ms)
            tooltip = f"Bybit: {bybit_ms:.0f} ms\nBinance: {binance_ms:.0f} ms"
            return entry.id, "ok", f"✓ {ms:.0f} ms", tooltip

        if bybit_ok:
            tooltip = f"Bybit: {bybit_ms:.0f} ms\nBinance: {binance_err or 'ошибка'}"
            return entry.id, "warn", "⚠ Только Bybit", tooltip

        if binance_ok:
            tooltip = f"Bybit: {bybit_err or 'ошибка'}\nBinance: {binance_ms:.0f} ms"
            return entry.id, "warn", "⚠ Только Binance", tooltip

        tooltip = f"Bybit: {bybit_err or 'ошибка'}\nBinance: {binance_err or 'ошибка'}"
        return entry.id, "error", "✕ Ошибка", tooltip

    def run(self) -> None:
        entries = list(self._entries)
        if not entries:
            self.finished_all.emit()
            return

        workers = min(4, len(entries))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self._check_one, entry): entry for entry in entries}
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    break
                entry = futures[future]
                try:
                    proxy_id, state, text, tooltip = future.result()
                except Exception as exc:  # noqa: BLE001
                    proxy_id = entry.id
                    state = "error"
                    text = "✕ Ошибка"
                    tooltip = sanitize_error(exc)
                self.checked.emit(proxy_id, state, text, tooltip)

        self.finished_all.emit()


__all__ = ["BINANCE_PING_URL", "BYBIT_TIME_URL", "ProxyCheckWorker"]
