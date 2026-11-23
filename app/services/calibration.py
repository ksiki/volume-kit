"""Оркестрация полной калибровки Volume Kit."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from app.core.config import AppSettings, get_config_service
from app.core.paths import OUTPUT_DIR
from app.services.analytics import (
    PercentileResult,
    TimeframeAnalysis,
    analyze_timeframe,
    get_value_at_percent,
)
from app.services.market_data import (
    compute_scale_info,
    get_binance_spot_tick_size,
    get_bybit_futures_tick_size,
)
from app.services.storage import ensure_days_cached, get_utc_dates

log = logging.getLogger("Main")

ProgressCallback = Callable[[int, str], None]


@dataclass
class CalibrationBundle:
    report: dict[str, Any]
    res_spot_15m: TimeframeAnalysis
    res_fut_5m: TimeframeAnalysis
    res_spot_1h: TimeframeAnalysis
    res_fut_15m: TimeframeAnalysis
    spot_15m_atr: float
    fut_5m_atr: float
    spot_1h_atr: float
    fut_15m_atr: float


def _format_usd(amount: float) -> str:
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount:.0f}"


def _build_category_data(
    category_id: str,
    category_title: str,
    spot_res: TimeframeAnalysis,
    fut_res: TimeframeAnalysis,
    spot_atr: float,
    fut_atr: float,
    spot_raw_scale: float,
    fut_raw_scale: float,
) -> dict[str, Any]:
    spot_vol_1 = get_value_at_percent(spot_res.max_volume_sorted, "MaxVolume", 1.0)
    spot_vol_3 = get_value_at_percent(spot_res.max_volume_sorted, "MaxVolume", 3.0)
    spot_delta_1 = get_value_at_percent(spot_res.max_delta_sorted, "MaxDelta", 1.0)
    fut_vol_3 = get_value_at_percent(fut_res.max_volume_sorted, "MaxVolume", 3.0)

    def _make_filter(
        exchange: str,
        exchange_key: str,
        timeframe: str,
        metric: str,
        metric_type: str,
        res: PercentileResult,
        ref_price: float,
    ) -> dict[str, Any]:
        return {
            "exchange": exchange,
            "exchange_key": exchange_key,
            "timeframe": timeframe,
            "metric": metric,
            "metric_type": metric_type,
            "threshold": round(res.value, 2),
            "threshold_str": f"{res.value:.2f}",
            "usd_str": _format_usd(res.value * ref_price),
            "row_num": res.row_num,
            "total_rows": res.total_rows,
            "pos_str": f"#{res.row_num} / {res.total_rows}",
        }

    return {
        "id": category_id,
        "title": category_title,
        "spot_tf": spot_res.timeframe,
        "fut_tf": fut_res.timeframe,
        "spot_ref_price": round(spot_res.reference_price, 2),
        "fut_ref_price": round(fut_res.reference_price, 2),
        "scales": [
            {
                "exchange": "Binance Spot",
                "exchange_key": "binance_spot",
                "timeframe": spot_res.timeframe,
                "price_scale": spot_res.price_scale,
                "price_step": spot_res.price_step,
                "price_step_str": f"{spot_res.price_step:g}",
                "atr": round(spot_atr, 2),
                "raw_scale": round(spot_raw_scale, 2),
                "days": spot_res.days,
                "half_life_days": int(round(spot_res.half_life_days)),
                "total_bars": spot_res.total_bars,
                "excluded_weekend_bars": spot_res.excluded_weekend_bars,
                "total_trades": spot_res.total_trades,
            },
            {
                "exchange": "ByBit Futures",
                "exchange_key": "bybit_futures",
                "timeframe": fut_res.timeframe,
                "price_scale": fut_res.price_scale,
                "price_step": fut_res.price_step,
                "price_step_str": f"{fut_res.price_step:g}",
                "atr": round(fut_atr, 2),
                "raw_scale": round(fut_raw_scale, 2),
                "days": fut_res.days,
                "half_life_days": int(round(fut_res.half_life_days)),
                "total_bars": fut_res.total_bars,
                "excluded_weekend_bars": fut_res.excluded_weekend_bars,
                "total_trades": fut_res.total_trades,
            },
        ],
        "filters": [
            _make_filter(
                "Binance Spot", "binance_spot", spot_res.timeframe,
                "MaxVolume (1%)", "vol_1", spot_vol_1, spot_res.reference_price,
            ),
            _make_filter(
                "Binance Spot", "binance_spot", spot_res.timeframe,
                "MaxVolume (3%)", "vol_3", spot_vol_3, spot_res.reference_price,
            ),
            _make_filter(
                "Binance Spot", "binance_spot", spot_res.timeframe,
                "MaxDelta (1%)", "delta_1", spot_delta_1, spot_res.reference_price,
            ),
            _make_filter(
                "ByBit Futures", "bybit_futures", fut_res.timeframe,
                "MaxVolume (3%)", "vol_3", fut_vol_3, fut_res.reference_price,
            ),
        ],
    }


def run_calibration(
    progress_cb: ProgressCallback | None = None,
    *,
    settings: AppSettings | None = None,
) -> CalibrationBundle:
    """Выполняет полный цикл калибровки и возвращает структурированный отчёт."""
    if settings is None:
        settings = get_config_service().current

    def _report(pct: int, stage: str) -> None:
        if progress_cb is not None:
            progress_cb(pct, stage)

    t_total = time.perf_counter()
    log.info(f"Запуск Volume Kit")

    # 1. Тик-сайзы, Robust ATR и Масштаб Цены
    _report(5, f"Запрос параметров бирж ({settings.symbol})...")
    binance_tick = get_binance_spot_tick_size(settings.symbol)
    bybit_tick = get_bybit_futures_tick_size(settings.symbol)

    _report(12, "Расчёт Robust ATR и масштаба (Binance Spot 1h / 15m)...")
    scale_spot_1h = compute_scale_info("binance_spot", settings.symbol, "1h", binance_tick, settings.spot_clusters, settings=settings)
    scale_spot_15m = compute_scale_info("binance_spot", settings.symbol, "15m", binance_tick, settings.spot_clusters, settings=settings)

    _report(20, "Расчёт Robust ATR и масштаба (ByBit Futures 15m / 5m)...")
    scale_fut_15m = compute_scale_info("bybit_futures", settings.symbol, "15m", bybit_tick, settings.futures_clusters, settings=settings)
    scale_fut_5m = compute_scale_info("bybit_futures", settings.symbol, "5m", bybit_tick, settings.futures_clusters, settings=settings)

    # 2. Загрузка и кэширование недостающих дней
    max_binance_days = max(settings.days_binance_1h, settings.days_binance_15m)
    max_bybit_days = max(settings.days_bybit_15m, settings.days_bybit_5m)

    _report(28, f"Проверка кэша и загрузка Binance Spot ({max_binance_days}д)...")
    binance_paths_all = ensure_days_cached("binance_spot", settings.symbol, get_utc_dates(max_binance_days), settings=settings)

    _report(48, f"Проверка кэша и загрузка ByBit Futures ({max_bybit_days}д)...")
    bybit_paths_all = ensure_days_cached("bybit_futures", settings.symbol, get_utc_dates(max_bybit_days), settings=settings)

    binance_paths_60d = binance_paths_all[-settings.days_binance_1h:]
    binance_paths_30d = binance_paths_all[-settings.days_binance_15m:]
    bybit_paths_30d = bybit_paths_all[-settings.days_bybit_15m:]
    bybit_paths_14d = bybit_paths_all[-settings.days_bybit_5m:]

    # 3. Кластерный анализ
    _report(64, "Кластерный анализ Binance Spot 15m...")
    res_spot_15m = analyze_timeframe(
        "binance_spot", settings.symbol, "15m", 15, settings.days_binance_15m,
        binance_paths_30d, scale_spot_15m.price_scale, scale_spot_15m.price_step, reference_price=scale_spot_15m.reference_price,
        settings=settings,
    )

    _report(73, "Кластерный анализ ByBit Futures 5m...")
    res_fut_5m = analyze_timeframe(
        "bybit_futures", settings.symbol, "5m", 5, settings.days_bybit_5m,
        bybit_paths_14d, scale_fut_5m.price_scale, scale_fut_5m.price_step, reference_price=scale_fut_5m.reference_price,
        settings=settings,
    )

    _report(82, "Кластерный анализ Binance Spot 1h...")
    res_spot_1h = analyze_timeframe(
        "binance_spot", settings.symbol, "1h", 60, settings.days_binance_1h,
        binance_paths_60d, scale_spot_1h.price_scale, scale_spot_1h.price_step, reference_price=scale_spot_1h.reference_price,
        settings=settings,
    )

    _report(92, "Кластерный анализ ByBit Futures 15m...")
    res_fut_15m = analyze_timeframe(
        "bybit_futures", settings.symbol, "15m", 15, settings.days_bybit_15m,
        bybit_paths_30d, scale_fut_15m.price_scale, scale_fut_15m.price_step, reference_price=scale_fut_15m.reference_price,
        settings=settings,
    )

    if settings.save_sorted_csv:
        log.info(f"Полные таблицы сохранены в: {OUTPUT_DIR}")

    elapsed = round(time.perf_counter() - t_total, 2)
    log.info(f"Калибровка {settings.symbol} завершена за {elapsed:.2f}с")

    cat_15m = _build_category_data(
        "trading_15m", "Трейдинг 15m",
        res_spot_15m, res_fut_5m,
        scale_spot_15m.atr, scale_fut_5m.atr,
        scale_spot_15m.raw_scale, scale_fut_5m.raw_scale,
    )
    cat_1h = _build_category_data(
        "trading_1h", "Трейдинг 1h",
        res_spot_1h, res_fut_15m,
        scale_spot_1h.atr, scale_fut_15m.atr,
        scale_spot_1h.raw_scale, scale_fut_15m.raw_scale,
    )

    report = {
        "symbol": settings.symbol,
        "spot_ref_price": round(res_spot_15m.reference_price, 2),
        "fut_ref_price": round(res_fut_5m.reference_price, 2),
        "elapsed_sec": elapsed,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
        "categories": [cat_15m, cat_1h],
    }

    _report(100, f"Готово за {elapsed:.2f}с")

    return CalibrationBundle(
        report=report,
        res_spot_15m=res_spot_15m,
        res_fut_5m=res_fut_5m,
        res_spot_1h=res_spot_1h,
        res_fut_15m=res_fut_15m,
        spot_15m_atr=scale_spot_15m.atr,
        fut_5m_atr=scale_fut_5m.atr,
        spot_1h_atr=scale_spot_1h.atr,
        fut_15m_atr=scale_fut_15m.atr,
    )
