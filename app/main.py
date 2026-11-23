import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .analytics import PercentileResult, TimeframeAnalysis, analyze_timeframe, get_value_at_percent
from .config import (
    CONSOLE_TOP_ROWS,
    DAYS_BINANCE_15M,
    DAYS_BINANCE_1H,
    DAYS_BYBIT_15M,
    DAYS_BYBIT_5M,
    FUTURES_CLUSTERS,
    OUTPUT_DIR,
    SAVE_SORTED_CSV,
    SPOT_CLUSTERS,
    SYMBOL,
)
from .market_data import (
    compute_scale_info,
    get_binance_spot_tick_size,
    get_bybit_futures_tick_size,
)
from .storage import ensure_days_cached, get_utc_dates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(name)-10s | %(message)s",
    datefmt="%H:%M:%S",
)
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


def run_calibration(progress_cb: ProgressCallback | None = None) -> CalibrationBundle:
    """Выполняет полный цикл калибровки и возвращает структурированный отчёт."""
    def _report(pct: int, stage: str) -> None:
        if progress_cb is not None:
            progress_cb(pct, stage)

    t_total = time.perf_counter()
    log.info(f"Запуск Volume Kit")

    # 1. Тик-сайзы, Robust ATR и Масштаб Цены
    _report(5, f"Запрос параметров бирж ({SYMBOL})...")
    binance_tick = get_binance_spot_tick_size(SYMBOL)
    bybit_tick = get_bybit_futures_tick_size(SYMBOL)

    _report(12, "Расчёт Robust ATR и масштаба (Binance Spot 1h / 15m)...")
    scale_spot_1h = compute_scale_info("binance_spot", SYMBOL, "1h", binance_tick, SPOT_CLUSTERS)
    scale_spot_15m = compute_scale_info("binance_spot", SYMBOL, "15m", binance_tick, SPOT_CLUSTERS)

    _report(20, "Расчёт Robust ATR и масштаба (ByBit Futures 15m / 5m)...")
    scale_fut_15m = compute_scale_info("bybit_futures", SYMBOL, "15m", bybit_tick, FUTURES_CLUSTERS)
    scale_fut_5m = compute_scale_info("bybit_futures", SYMBOL, "5m", bybit_tick, FUTURES_CLUSTERS)

    # 2. Загрузка и кэширование недостающих дней
    max_binance_days = max(DAYS_BINANCE_1H, DAYS_BINANCE_15M)
    max_bybit_days = max(DAYS_BYBIT_15M, DAYS_BYBIT_5M)

    _report(28, f"Проверка кэша и загрузка Binance Spot ({max_binance_days}д)...")
    binance_paths_all = ensure_days_cached("binance_spot", SYMBOL, get_utc_dates(max_binance_days))

    _report(48, f"Проверка кэша и загрузка ByBit Futures ({max_bybit_days}д)...")
    bybit_paths_all = ensure_days_cached("bybit_futures", SYMBOL, get_utc_dates(max_bybit_days))

    binance_paths_60d = binance_paths_all[-DAYS_BINANCE_1H:]
    binance_paths_30d = binance_paths_all[-DAYS_BINANCE_15M:]
    bybit_paths_30d = bybit_paths_all[-DAYS_BYBIT_15M:]
    bybit_paths_14d = bybit_paths_all[-DAYS_BYBIT_5M:]

    # 3. Кластерный анализ
    _report(64, "Кластерный анализ Binance Spot 15m...")
    res_spot_15m = analyze_timeframe(
        "binance_spot", SYMBOL, "15m", 15, DAYS_BINANCE_15M,
        binance_paths_30d, scale_spot_15m.price_scale, scale_spot_15m.price_step, reference_price=scale_spot_15m.reference_price
    )

    _report(73, "Кластерный анализ ByBit Futures 5m...")
    res_fut_5m = analyze_timeframe(
        "bybit_futures", SYMBOL, "5m", 5, DAYS_BYBIT_5M,
        bybit_paths_14d, scale_fut_5m.price_scale, scale_fut_5m.price_step, reference_price=scale_fut_5m.reference_price
    )

    _report(82, "Кластерный анализ Binance Spot 1h...")
    res_spot_1h = analyze_timeframe(
        "binance_spot", SYMBOL, "1h", 60, DAYS_BINANCE_1H,
        binance_paths_60d, scale_spot_1h.price_scale, scale_spot_1h.price_step, reference_price=scale_spot_1h.reference_price
    )

    _report(92, "Кластерный анализ ByBit Futures 15m...")
    res_fut_15m = analyze_timeframe(
        "bybit_futures", SYMBOL, "15m", 15, DAYS_BYBIT_15M,
        bybit_paths_30d, scale_fut_15m.price_scale, scale_fut_15m.price_step, reference_price=scale_fut_15m.reference_price
    )

    if SAVE_SORTED_CSV:
        log.info(f"Полные таблицы сохранены в: {OUTPUT_DIR}")

    elapsed = round(time.perf_counter() - t_total, 2)
    log.info(f"Калибровка {SYMBOL} завершена за {elapsed:.2f}с")

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
        "symbol": SYMBOL,
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


def _print_sorted_preview(title: str, df, top_n: int) -> None:
    if top_n <= 0:
        return
    print(f"\n  [{title} — TOP {top_n} из {len(df)}]")
    print(df.head(top_n).to_string(index=False))


def _print_category_report(
    category_title: str,
    spot_res: TimeframeAnalysis,
    fut_res: TimeframeAnalysis,
    spot_atr: float,
    fut_atr: float,
) -> None:
    spot_vol_1 = get_value_at_percent(spot_res.max_volume_sorted, "MaxVolume", 1.0)
    spot_vol_3 = get_value_at_percent(spot_res.max_volume_sorted, "MaxVolume", 3.0)
    spot_delta_1 = get_value_at_percent(spot_res.max_delta_sorted, "MaxDelta", 1.0)
    fut_vol_3 = get_value_at_percent(fut_res.max_volume_sorted, "MaxVolume", 3.0)

    _print_sorted_preview(f"Binance Spot {spot_res.timeframe} | MaxVolume", spot_res.max_volume_sorted, CONSOLE_TOP_ROWS)
    _print_sorted_preview(f"Binance Spot {spot_res.timeframe} | MaxDelta", spot_res.max_delta_sorted, CONSOLE_TOP_ROWS)
    _print_sorted_preview(f"ByBit Futures {fut_res.timeframe} | MaxVolume", fut_res.max_volume_sorted, CONSOLE_TOP_ROWS)

    width = 80
    line = "-" * width
    dbl = "=" * width

    print(f"\n{dbl}")
    print(f"  {category_title.upper()}  |  {SYMBOL}  |  Ref: Spot {spot_res.reference_price:,.1f} / Fut {fut_res.reference_price:,.1f}")
    print(dbl)

    print("  1. МАСШТАБ ЦЕНЫ (СЖАТИЕ КЛАСТЕРОВ)")
    print(line)
    print(f"  {'Инструмент':<22} {'Масштаб':>10} {'Шаг цены':>12} {'Robust ATR':>14} {'Окно / HL':>14}")
    print(line)
    print(
        f"  {f'Binance Spot  {spot_res.timeframe}':<22} "
        f"{spot_res.price_scale:>10d} "
        f"{spot_res.price_step:>12g} "
        f"{spot_atr:>14.2f} "
        f"{f'{spot_res.days}д / {spot_res.half_life_days:.0f}д':>14}"
    )
    print(
        f"  {f'ByBit Futures {fut_res.timeframe}':<22} "
        f"{fut_res.price_scale:>10d} "
        f"{fut_res.price_step:>12g} "
        f"{fut_atr:>14.2f} "
        f"{f'{fut_res.days}д / {fut_res.half_life_days:.0f}д':>14}"
    )
    print(line)

    print("  2. ФИЛЬТРЫ CLUSTERSEARCH")
    print(line)
    print(f"  {'Инструмент':<22} {'Фильтр':<16} {'Порог':>10} {'~Объём USD':>12} {'Позиция':>14}")
    print(line)

    rows: list[tuple[str, str, PercentileResult, float]] = [
        (f"Binance Spot  {spot_res.timeframe}", "MaxVolume (1%)", spot_vol_1, spot_res.reference_price),
        (f"Binance Spot  {spot_res.timeframe}", "MaxVolume (3%)", spot_vol_3, spot_res.reference_price),
        (f"Binance Spot  {spot_res.timeframe}", "|MaxDelta|(1%)", spot_delta_1, spot_res.reference_price),
        (f"ByBit Futures {fut_res.timeframe}", "MaxVolume (3%)", fut_vol_3, fut_res.reference_price),
    ]

    for instrument, metric, res, ref_price in rows:
        usd_str = _format_usd(res.value * ref_price)
        pos_str = f"#{res.row_num} / {res.total_rows}"
        print(f"  {instrument:<22} {metric:<16} {res.value:>10.2f} {usd_str:>12} {pos_str:>14}")

    print(dbl)


def main() -> None:
    bundle = run_calibration()
    _print_category_report("Трейдинг 15m", bundle.res_spot_15m, bundle.res_fut_5m, bundle.spot_15m_atr, bundle.fut_5m_atr)
    _print_category_report("Трейдинг 1h", bundle.res_spot_1h, bundle.res_fut_15m, bundle.spot_1h_atr, bundle.fut_15m_atr)
    print(f"\n  Готово за {bundle.report['elapsed_sec']:.2f}с\n")


if __name__ == "__main__":
    main()