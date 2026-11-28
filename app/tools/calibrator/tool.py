"""Инструмент Calibrator: расчёт объёмных фильтров в фоновом потоке."""

from typing import Any, cast

from PySide6.QtWidgets import QWidget

from app.core.logging_setup import log_event
from app.core.tools import BaseTool, ToolContext, register_tool
from app.tools.calibrator.page import CalibratorPage
from app.ui.workers.calibration_worker import CalibrationWorker


@register_tool
class CalibratorTool(BaseTool):
    """Инструмент Calibrator внутри Volume Kit."""

    tool_id = "calibrator"
    title = "Calibrator"
    nav_label = "⊞  Calibrator"
    nav_group = "ИНСТРУМЕНТЫ"
    order = 10

    def __init__(self, context: ToolContext) -> None:
        super().__init__(context)
        self._worker: CalibrationWorker | None = None

        context.commands.register("calibration.start", self.start_calibration)
        context.commands.register("calibration.mark_stale", self.mark_stale)

    @property
    def page(self) -> CalibratorPage:
        return cast(CalibratorPage, self.widget)

    def create_widget(self) -> QWidget:
        page = CalibratorPage(config=self.context.config)
        page.copied.connect(self._on_copied)
        page.recalc_requested.connect(lambda: self.start_calibration(reload_cfg=True))
        page.set_idle()
        return page

    def mark_stale(self) -> None:
        """Помечает результаты устаревшими после смены тикера/настроек."""
        if self.is_widget_created:
            self.page.mark_stale()

    # ==================== КАЛИБРАЦИЯ ====================

    def start_calibration(self, reload_cfg: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.context.host.show_toast("Расчёт уже выполняется")
            return

        page = self.page
        symbol = self.context.host.current_symbol
        log_event("run_start", f"Запуск расчёта · {symbol}", tool="Calibrator")

        page.set_running(
            symbol=symbol,
            progress=2,
            stage="Чтение config.json..." if reload_cfg else "Запуск калибратора...",
        )

        worker = CalibrationWorker(reload_cfg=reload_cfg, parent=page, config=self.context.config)
        worker.progress_changed.connect(self._on_calc_progress)
        worker.symbol_changed.connect(self._on_symbol_changed)
        worker.finished_ok.connect(self._on_calc_finished)
        worker.failed.connect(self._on_calc_failed)
        worker.finished.connect(self._on_worker_cleanup)

        self._worker = worker
        worker.start()

    def _on_calc_progress(self, pct: int, stage: str) -> None:
        self.page.update_progress(pct, stage)

    def _on_symbol_changed(self, symbol: str) -> None:
        self.context.host.set_symbol(symbol)

    def _on_calc_finished(self, report: dict[str, Any]) -> None:
        self.context.host.set_symbol(str(report.get("symbol", self.context.config.current.symbol)))

        # Если live-цена ещё не успела прийти, показываем референсную спот-цену из отчёта
        self.context.host.show_reference_price(report.get("spot_ref_price"))

        self.page.set_report(report)

    def _on_calc_failed(self, err_msg: str) -> None:
        self.page.set_error(err_msg)

    def _on_worker_cleanup(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_copied(self, label: str, value: str) -> None:
        self.context.host.show_toast(f"✓ Скопировано ({label}): {value}")

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            # Сетевые загрузки могут блокировать поток дольше wait: жёсткий откат при закрытии
            if not self._worker.wait(800):
                self._worker.terminate()
                self._worker.wait(500)
