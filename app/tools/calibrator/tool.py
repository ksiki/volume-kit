"""Инструмент Calibrator: расчёт объёмных фильтров в фоновом потоке."""

from typing import Any, Callable, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from app.core.tools import BaseTool, GlobalActionHandle, ToolContext, register_tool
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
        self._started: bool = False
        self._unsubscribe_logs: Callable[[], None] | None = None

        context.commands.register("calibration.start", self.start_calibration)
        self._recalc_action: GlobalActionHandle = context.host.add_global_action(
            "calibrator.recalc",
            "↻ Пересчитать",
            "Перечитать config.json и обновить расчёт Calibrator",
            lambda: self.start_calibration(reload_cfg=True),
        )

    @property
    def page(self) -> CalibratorPage:
        return cast(CalibratorPage, self.widget)

    def create_widget(self) -> QWidget:
        page = CalibratorPage()
        page.copied.connect(self._on_copied)
        page.open_logs_requested.connect(self.context.host.open_logs)
        page.recalc_requested.connect(lambda: self.start_calibration(reload_cfg=True))
        self._unsubscribe_logs = self.context.host.subscribe_logs(self._on_log_entry)
        return page

    def on_first_shown(self) -> None:
        # Запускаем калибратор сразу после первой отрисовки инструмента
        if not self._started:
            self._started = True
            QTimer.singleShot(80, lambda: self.start_calibration(reload_cfg=False))

    # ==================== КАЛИБРАЦИЯ ====================

    def start_calibration(self, reload_cfg: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.context.host.show_toast("Расчёт уже выполняется")
            return

        page = self.page

        if reload_cfg:
            self.context.host.clear_logs()
            page.clear_mini_logs()

        self._recalc_action.set_enabled(False)
        self.context.host.set_status("running", "Расчёт 0%")
        page.set_running(
            symbol=self.context.host.current_symbol,
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
        self.context.host.set_status("running", f"Расчёт {pct}%")
        self.page.update_progress(pct, stage)

    def _on_symbol_changed(self, symbol: str) -> None:
        self.context.host.set_symbol(symbol)
        self.page.set_symbol(self.context.host.current_symbol)

    def _on_calc_finished(self, report: dict[str, Any]) -> None:
        self.context.host.set_symbol(str(report.get("symbol", self.context.config.current.symbol)))

        # Если live-цена ещё не успела прийти, показываем референсную спот-цену из отчёта
        self.context.host.show_reference_price(report.get("spot_ref_price"))

        elapsed = report.get("elapsed_sec", 0)
        updated_at = report.get("updated_at", "")
        self.context.host.set_status("done", f"Готово за {elapsed}с · {updated_at}")

        self.page.set_report(report)
        self._recalc_action.set_enabled(True)

    def _on_calc_failed(self, err_msg: str) -> None:
        self.context.host.set_status("error", "Ошибка расчёта")
        self.page.set_error(err_msg)
        self._recalc_action.set_enabled(True)

    def _on_worker_cleanup(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_log_entry(self, entry: dict[str, Any]) -> None:
        if self.is_widget_created:
            self.page.append_log_entry(entry)

    def _on_copied(self, label: str, value: str) -> None:
        self.context.host.show_toast(f"✓ Скопировано ({label}): {value}")

    def shutdown(self) -> None:
        if self._unsubscribe_logs is not None:
            self._unsubscribe_logs()
            self._unsubscribe_logs = None
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            # Сетевые загрузки могут блокировать поток дольше wait: жёсткий откат при закрытии
            if not self._worker.wait(800):
                self._worker.terminate()
                self._worker.wait(500)
