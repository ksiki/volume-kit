"""Консольный отчёт Volume Kit."""

from app.core.config import AppSettings, get_config_service
from app.core.logging_setup import configure_logging
from app.services.analytics import PercentileResult, TimeframeAnalysis, get_value_at_percent
from app.services.calibration import _format_usd, run_calibration


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
    settings: AppSettings,
) -> None:
    spot_vol_1 = get_value_at_percent(spot_res.max_volume_sorted, "MaxVolume", 1.0)
    spot_vol_3 = get_value_at_percent(spot_res.max_volume_sorted, "MaxVolume", 3.0)
    spot_delta_1 = get_value_at_percent(spot_res.max_delta_sorted, "MaxDelta", 1.0)
    fut_vol_3 = get_value_at_percent(fut_res.max_volume_sorted, "MaxVolume", 3.0)

    _print_sorted_preview(f"Binance Spot {spot_res.timeframe} | MaxVolume", spot_res.max_volume_sorted, settings.console_top_rows)
    _print_sorted_preview(f"Binance Spot {spot_res.timeframe} | MaxDelta", spot_res.max_delta_sorted, settings.console_top_rows)
    _print_sorted_preview(f"ByBit Futures {fut_res.timeframe} | MaxVolume", fut_res.max_volume_sorted, settings.console_top_rows)

    width = 80
    line = "-" * width
    dbl = "=" * width

    print(f"\n{dbl}")
    print(f"  {category_title.upper()}  |  {settings.symbol}  |  Ref: Spot {spot_res.reference_price:,.1f} / Fut {fut_res.reference_price:,.1f}")
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
    configure_logging(console=True)
    settings = get_config_service().current
    bundle = run_calibration(settings=settings)
    _print_category_report("Трейдинг 15m", bundle.res_spot_15m, bundle.res_fut_5m, bundle.spot_15m_atr, bundle.fut_5m_atr, settings=settings)
    _print_category_report("Трейдинг 1h", bundle.res_spot_1h, bundle.res_fut_15m, bundle.spot_1h_atr, bundle.fut_15m_atr, settings=settings)
    print(f"\n  Готово за {bundle.report['elapsed_sec']:.2f}с\n")
