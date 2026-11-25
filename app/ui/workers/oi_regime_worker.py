"""Фоновый поток загрузки и расчёта OI & Delta Regime."""

import logging

from PySide6.QtCore import QObject, QThread, Signal

from app.services.oi_regime.engine import build_bundle
from app.services.oi_regime.fetcher import (
    OIRegimeFetcher,
    RawRegimeData,
    RegimeFetchCancelled,
)
from app.services.oi_regime.models import RegimeParams

log = logging.getLogger(__name__)


class OIRegimeWorker(QThread):
    """Один цикл загрузки: полный снимок или лёгкое Live-обновление."""

    stage_changed = Signal(str)
    finished_ok = Signal(str, object, object)  # mode, bundle, raw
    failed = Signal(str, str)  # mode, error

    def __init__(
        self,
        symbol: str,
        params: RegimeParams,
        mode: str = "full",
        cached_raw: RawRegimeData | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._symbol = str(symbol).strip().upper()
        self._params = params
        self._mode = "live" if mode == "live" else "full"
        self._cached_raw = cached_raw

    @property
    def mode(self) -> str:
        return self._mode

    def _stage(self, text: str) -> None:
        if not self.isInterruptionRequested():
            self.stage_changed.emit(text)

    def run(self) -> None:
        fetcher = OIRegimeFetcher(cancel_check=self.isInterruptionRequested)
        try:
            self._stage("Загрузка данных Binance/Bybit...")
            if self._mode == "live" and self._cached_raw is not None:
                raw = fetcher.fetch_live(self._cached_raw, self._params, self._stage)
            else:
                raw = fetcher.fetch_full(self._symbol, self._params, self._stage)

            if self.isInterruptionRequested():
                return

            self._stage("Расчёт матрицы аномалий...")
            bundle = build_bundle(raw, self._params, self._symbol)
            if self.isInterruptionRequested():
                return
            self.finished_ok.emit(self._mode, bundle, raw)
        except RegimeFetchCancelled:
            # Штатная отмена: выходим молча, без сигнала failed.
            log.info("Загрузка OI Regime (%s) отменена", self._mode, extra={"tool": "OI Regime"})
        except Exception as exc:
            log.exception(
                "Ошибка OI Regime (%s): %s", self._mode, exc,
                extra={"tool": "OI Regime"},
            )
            if not self.isInterruptionRequested():
                self.failed.emit(self._mode, str(exc))
        finally:
            fetcher.close()
