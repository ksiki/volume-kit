"""Инструмент OI & Delta Regime: кэш, Live-таймер и связка с фоновым worker."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from app.core.config import AppSettings
from app.core.logging_setup import log_event
from app.core.tools import BaseTool, ToolContext, register_tool
from app.services.oi_regime.engine import build_bundle
from app.services.oi_regime.fetcher import RawRegimeData
from app.services.oi_regime.models import RegimeBundle, RegimeParams
from app.tools.oi_regime.page import OIRegimePage
from app.ui.workers.oi_regime_worker import OIRegimeWorker

log = logging.getLogger(__name__)

_CACHE_LIMIT = 4


def _params_from_settings(settings: AppSettings, timeframe: str | None = None) -> RegimeParams:
    return RegimeParams(
        timeframe=timeframe or settings.oi_default_timeframe,
        z_window=settings.oi_z_window,
        threshold_oi=settings.oi_threshold_oi,
        threshold_delta=settings.oi_threshold_delta,
        absorption_close_ratio=settings.oi_absorption_close_ratio,
        absorption_min_z_delta=settings.oi_absorption_min_z_delta,
        spot_sync_min_z=settings.oi_spot_sync_min_z,
        spot_div_min_z=settings.oi_spot_div_min_z,
        spot_dead_max_z=settings.oi_spot_dead_max_z,
        spot_dead_extreme_oi_z=settings.oi_spot_dead_extreme_oi_z,
        use_bybit=settings.oi_use_bybit,
        visible_bars=settings.oi_visible_bars,
        tz_offset_hours=settings.tz_offset_hours,
    )


@register_tool
class OIRegimeTool(BaseTool):
    """OI & Delta Regime: межбиржевой OI, дельты и robust Z-матрица."""

    tool_id = "oi_regime"
    title = "OI & Delta Regime"
    nav_label = "◈  OI & Delta Regime"
    nav_group = "ИНСТРУМЕНТЫ"
    order = 15

    def __init__(self, context: ToolContext) -> None:
        super().__init__(context)
        self._worker: OIRegimeWorker | None = None
        self._live_timer: QTimer | None = None
        self._cache: dict[tuple[str, str], RawRegimeData] = {}
        self._bundle: RegimeBundle | None = None
        self._started = False
        self._page_visible = False
        self._refresh_pending = False
        self._settings_snapshot: AppSettings = context.config.current
        self._seen_events: set[str] = set()
        context.commands.register("oi_regime.reload_config", self.reload_config)

    @property
    def page(self) -> OIRegimePage:
        return cast(OIRegimePage, self.widget)

    def create_widget(self) -> QWidget:
        settings = self.context.config.current
        self._settings_snapshot = settings
        params = _params_from_settings(settings)
        page = OIRegimePage(params, settings.oi_refresh_sec, config=self.context.config)
        page.refresh_requested.connect(lambda: self.start_fetch("full", manual=True))
        page.timeframe_changed.connect(self._on_timeframe_changed)
        page.params_changed.connect(self._on_params_changed)
        page.live_toggled.connect(self._on_live_toggled)
        page.visibility_changed.connect(self._on_visibility_changed)
        page.defaults_reset.connect(self._on_defaults_reset)
        page.toast_requested.connect(self.context.host.show_toast)

        self._live_timer = QTimer(page)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._on_live_timeout)
        return page

    def on_first_shown(self) -> None:
        if self._started:
            return
        self._started = True
        if self._worker is not None:
            return
        QTimer.singleShot(80, lambda: self.start_fetch("full", manual=True))

    def on_shown(self) -> None:
        self._page_visible = True
        self._ensure_fresh_or_schedule()

    # ==================== ЗАГРУЗКА ====================

    def start_fetch(self, mode: str = "full", manual: bool = False) -> None:
        if self._worker is not None:
            if mode != "live":
                self._refresh_pending = True
            if manual:
                self.context.host.show_toast("Обновление OI Regime уже выполняется")
            return

        self._refresh_pending = False
        page = self.page
        symbol = self.context.config.current.symbol
        params = replace(page.current_params, timeframe=page.current_timeframe)
        cache_key = (symbol, params.timeframe)
        cached = self._cache.get(cache_key)
        if mode == "live" and cached is None:
            mode = "full"
        if mode != "live" and manual:
            log_event(
                "run_start",
                f"OI Regime · {symbol} {params.timeframe} · загрузка данных",
                tool="OI Regime",
                logger_name="app.services.oi_regime.fetcher",
            )

        page.set_loading("Загрузка...")
        worker = OIRegimeWorker(
            symbol=symbol,
            params=params,
            mode=mode,
            cached_raw=cached if mode == "live" else None,
            parent=page,
        )
        worker.stage_changed.connect(self._on_stage)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self._worker = worker
        worker.start()

    def _on_stage(self, text: str) -> None:
        if self.is_widget_created:
            self.page.set_status_text(text, "loading")

    def _on_finished(self, mode: str, bundle: object, raw: object) -> None:
        if not isinstance(bundle, RegimeBundle) or not isinstance(raw, RawRegimeData):
            return
        current = self.context.config.current.symbol
        if bundle.symbol != current or bundle.timeframe != self.page.current_timeframe:
            if self._page_visible:
                QTimer.singleShot(50, lambda: self.start_fetch("full", manual=False))
            return

        # Если параметры в UI изменились, пока шёл запрос, пересчитываем из свежих raw.
        current_params = replace(self.page.current_params, timeframe=bundle.timeframe)
        if current_params != bundle.params:
            try:
                bundle = build_bundle(raw, current_params, bundle.symbol)
            except Exception:
                log.exception(
                    "Не удалось пересчитать OI Regime с актуальными параметрами",
                    extra={"tool": "OI Regime"},
                )
                self._schedule_live()
                return
        key = (bundle.symbol, bundle.timeframe)
        self._cache[key] = raw
        self._trim_cache()
        previous = self._bundle
        self._bundle = bundle
        self.page.apply_bundle(bundle)

        if mode == "full":
            log.info(
                "OI Regime %s %s: баров %d, аномалий %d, warmup %d, источники %s",
                bundle.symbol,
                bundle.timeframe,
                len(bundle.bars),
                bundle.anomaly_count,
                bundle.warmup_rows,
                bundle.sources_label,
                extra={"tool": "OI Regime"},
            )
            self._seen_events = {
                self._event_key(bundle, event.open_time_ms, event.regime_key)
                for event in bundle.events
            }
        else:
            self._log_new_live_anomalies(bundle, previous)
            self._seen_events.update(
                self._event_key(bundle, event.open_time_ms, event.regime_key)
                for event in bundle.events
            )
            if len(self._seen_events) > 800:
                self._seen_events = {
                    self._event_key(bundle, event.open_time_ms, event.regime_key)
                    for event in bundle.events
                }
        self._schedule_live()

    def _on_failed(self, mode: str, message: str) -> None:
        if not self.is_widget_created:
            return
        self.page.set_error(message)
        if self.page.is_live_checked():
            self._schedule_live()

    def _cleanup_worker(self, worker: OIRegimeWorker) -> None:
        is_current = self._worker is worker
        if is_current:
            self._worker = None
        worker.deleteLater()
        if is_current and not self._refresh_pending and self.is_widget_created:
            # Страховка: статус не должен «залипнуть» в loading после завершения запроса.
            if self.page.status_state == "loading":
                self.page.set_status_text("Обновление не завершилось", "warn")
        if self._refresh_pending and self._page_visible:
            self._refresh_pending = False
            QTimer.singleShot(50, lambda: self.start_fetch("full", manual=False))

    def _trim_cache(self) -> None:
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.pop(next(iter(self._cache)))

    @staticmethod
    def _event_key(bundle: RegimeBundle, ts_ms: int, regime_key: str) -> str:
        return f"{bundle.symbol}:{bundle.timeframe}:{ts_ms}:{regime_key}"

    def _log_new_live_anomalies(
        self, bundle: RegimeBundle, previous: RegimeBundle | None
    ) -> None:
        logged = 0
        for event in bundle.events:
            key = self._event_key(bundle, event.open_time_ms, event.regime_key)
            if key in self._seen_events:
                continue
            log_event(
                "anomaly",
                f"{bundle.symbol} {bundle.timeframe} · {event.marker} {event.regime_label} · "
                f"ΔOI {event.delta_oi_total if event.delta_oi_total is not None else '—'} · "
                f"Z_OI {event.z_doi:+.2f} / Z_Δ {event.z_fut_delta:+.2f}"
                + (" · LIVE" if event.is_live else ""),
                tool="OI Regime",
                logger_name="app.services.oi_regime.engine",
            )
            self._seen_events.add(key)
            logged += 1
            if logged >= 3:
                break
        if logged == 0 and previous is not None and bundle.anomaly_count > previous.anomaly_count:
            log.info(
                "OI Regime %s %s: аномалий %d",
                bundle.symbol,
                bundle.timeframe,
                bundle.anomaly_count,
                extra={"tool": "OI Regime"},
            )


    # ==================== LIVE И СОБЫТИЯ UI ====================

    def _schedule_live(self) -> None:
        if self._live_timer is None or not self.is_widget_created:
            return
        settings = self.context.config.current
        interval = int(settings.oi_refresh_sec)
        live_on = self.page.is_live_checked() and interval > 0
        if not live_on or not self._page_visible:
            self._live_timer.stop()
            return
        self._live_timer.start(interval * 1000)

    def _on_live_timeout(self) -> None:
        if not self.is_widget_created:
            return
        if not self.page.is_live_checked() or not self._page_visible:
            return
        if self._worker is not None:
            self._schedule_live()
            return
        self.start_fetch("live", manual=False)

    def _on_live_toggled(self, enabled: bool) -> None:
        if enabled:
            if self._bundle is None:
                self.start_fetch("full", manual=True)
            else:
                self._schedule_live()
        elif self._live_timer is not None:
            self._live_timer.stop()

    def _on_visibility_changed(self, visible: bool) -> None:
        self._page_visible = visible
        if not self.is_widget_created:
            return
        if visible:
            self._ensure_fresh_or_schedule()
        elif self._live_timer is not None:
            self._live_timer.stop()

    def _ensure_fresh_or_schedule(self) -> None:
        if not self.is_widget_created:
            return
        interval = max(1, int(self.context.config.current.oi_refresh_sec))
        stale_ms = max(60_000, interval * 2 * 1000)
        now_ms = int(time.time() * 1000)
        if self._bundle is None:
            if self._worker is not None:
                return
            self.start_fetch("full", manual=True)
            return
        if now_ms - self._bundle.updated_at_ms > stale_ms:
            self.start_fetch("full", manual=False)
            return
        self._schedule_live()

    def _on_timeframe_changed(self, timeframe: str) -> None:
        if not self.is_widget_created:
            return
        params = replace(self.page.current_params, timeframe=timeframe)
        log_event(
            "run_start",
            f"OI Regime · {self.context.config.current.symbol} {timeframe} · смена таймфрейма",
            tool="OI Regime",
            logger_name="app.services.oi_regime.fetcher",
        )
        cached = self._cache.get((self.context.config.current.symbol, timeframe))
        if cached is not None:
            try:
                bundle = build_bundle(cached, params, cached.symbol)
                self._bundle = bundle
                self.page.apply_bundle(bundle)
            except Exception:
                log.exception(
                    "Не удалось пересчитать OI Regime из кэша",
                    extra={"tool": "OI Regime"},
                )
        self.start_fetch("full", manual=False)

    def _on_params_changed(self, payload: dict[str, object]) -> None:
        if not self.is_widget_created:
            return
        params = replace(
            self.page.current_params,
            threshold_oi=float(
                payload.get("threshold_oi", self.page.current_params.threshold_oi)
            ),
            threshold_delta=float(
                payload.get("threshold_delta", self.page.current_params.threshold_delta)
            ),
            z_window=int(payload.get("z_window", self.page.current_params.z_window)),
        )
        symbol = self.context.config.current.symbol
        cached = self._cache.get((symbol, params.timeframe))
        if cached is None:
            return
        try:
            bundle = build_bundle(cached, params, symbol)
        except Exception:
            log.exception(
                "Ошибка пересчёта OI Regime из кэша", extra={"tool": "OI Regime"}
            )
            return
        self._bundle = bundle
        self.page.apply_bundle(bundle)

    def _on_defaults_reset(self, timeframe_changed: bool) -> None:
        """Реакция на «↺ Сброс»: полный refresh при смене ТФ, иначе пересчёт из кэша."""
        if not self.is_widget_created:
            return
        if timeframe_changed:
            self.start_fetch("full", manual=False)
            self._schedule_live()
            return
        symbol = self.context.config.current.symbol
        cached = self._cache.get((symbol, self.page.current_timeframe))
        if cached is None:
            self.start_fetch("full", manual=False)
        self._schedule_live()


    # ==================== НАСТРОЙКИ ====================

    def reload_config(self) -> None:
        settings = self.context.config.current
        previous = self._settings_snapshot
        self._settings_snapshot = settings
        if not self.is_widget_created:
            return

        current_tf = self.page.current_timeframe
        tf_changed = settings.oi_default_timeframe != previous.oi_default_timeframe
        target_tf = settings.oi_default_timeframe if tf_changed else current_tf
        params = _params_from_settings(settings, target_tf)

        self.page.set_defaults(params, settings.oi_refresh_sec)
        # Live/фильтры восстанавливаются из config.json внутри set_defaults.

        symbol_changed = settings.symbol != previous.symbol
        bybit_changed = settings.oi_use_bybit != previous.oi_use_bybit
        cache_key = (settings.symbol, target_tf)
        cached = self._cache.get(cache_key)
        cache_too_small = cached is None or len(cached.fut_klines) < (
            settings.oi_visible_bars + settings.oi_z_window + 10
        )

        if symbol_changed:
            self._cache.clear()
            self._bundle = None
            self._seen_events.clear()

        if symbol_changed or tf_changed or bybit_changed or cache_too_small:
            if self._page_visible:
                self.start_fetch("full", manual=tf_changed)
            else:
                self._bundle = None
                self._schedule_live()
            return

        try:
            bundle = build_bundle(cached, params, settings.symbol)
        except Exception:
            log.exception(
                "Ошибка пересчёта OI Regime после сохранения настроек",
                extra={"tool": "OI Regime"},
            )
            return
        self._bundle = bundle
        self.page.apply_bundle(bundle)
        self._schedule_live()

    # ==================== ЗАВЕРШЕНИЕ ====================

    def shutdown(self) -> None:
        if self._live_timer is not None:
            self._live_timer.stop()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(800):
                worker.terminate()
                worker.wait(500)
        self._worker = None

