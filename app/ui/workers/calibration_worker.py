"""Фоновый поток выполнения калибровки без блокировки UI."""

import logging

from PySide6.QtCore import QObject, QThread, Signal

from app.core.config import ConfigService, get_config_service
from app.services.calibration import run_calibration

log = logging.getLogger("Backend")


class CalibrationWorker(QThread):
    """Фоновый поток выполнения калибровки без блокировки UI."""

    progress_changed = Signal(int, str)
    symbol_changed = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        reload_cfg: bool = False,
        parent: QObject | None = None,
        config: ConfigService | None = None,
    ) -> None:
        super().__init__(parent)
        self._reload_cfg = reload_cfg
        self._config = config if config is not None else get_config_service()

    def run(self) -> None:
        try:
            self.progress_changed.emit(
                2,
                "Чтение config.json..." if self._reload_cfg else "Запуск калибратора...",
            )

            settings = self._config.reload() if self._reload_cfg else self._config.current
            self.symbol_changed.emit(settings.symbol)

            def _on_progress(pct: int, stage: str) -> None:
                self.progress_changed.emit(pct, stage)

            bundle = run_calibration(progress_cb=_on_progress, settings=settings)
            self.finished_ok.emit(bundle.report)

        except Exception as exc:
            log.exception(f"Ошибка калибровки: {exc}")
            self.failed.emit(str(exc))
