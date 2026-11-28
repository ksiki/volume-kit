"""Консольный отчёт Volume Kit."""

from typing import Any

from app.core.config import AppSettings, get_config_service
from app.core.logging_setup import configure_logging
from app.services.calibration import run_calibration


def _print_sorted_preview(title: str, df, top_n: int) -> None:
    if top_n <= 0 or df is None or df.empty:
        return
    print(f"\n  [{title} — TOP {top_n} из {len(df)}]")
    print(df.head(top_n).to_string(index=False))


def _print_market(market: dict[str, Any], settings: AppSettings) -> None:
    width = 96
    line = "-" * width
    dbl = "=" * width

    print(f"\n{dbl}")
    print(
        f"  {str(market.get('title', '')).upper()}  |  {settings.symbol}  |  "
        f"{market.get('summary', '')}"
    )
    print(dbl)
    print(
        f"  {'ТФ':<5} {'Глубина':>8} {'Масштаб':>9} {'Шаг':>8} "
        f"{'ATR':>12} {'Окно / HL':>13}  Метрики"
    )
    print(line)

    for row in market.get("rows") or []:
        error = row.get("error")
        if error:
            print(f"  {row.get('timeframe', ''):<5} ОШИБКА: {error}")
            continue

        scale = row.get("scale") or {}
        window = f"{row.get('days', 0)}д / {row.get('half_life_days', 0)}д"
        metrics: list[str] = []
        for key in ("vol_1", "vol_3", "delta_1"):
            payload = row.get(key)
            if payload:
                metrics.append(
                    f"{key}={payload['value']} ({payload['usd']}, {payload['pos']})"
                )

        print(
            f"  {str(row.get('timeframe', '')):<5} "
            f"{str(row.get('days', 0)) + 'д':>8} "
            f"{scale.get('value', '—'):>9} "
            f"{row.get('step_str') or '—':>8} "
            f"{row.get('atr_str') or '—':>12} "
            f"{window:>13}  {'; '.join(metrics)}"
        )

    print(dbl)


def main() -> None:
    configure_logging(console=True)
    settings = get_config_service().current
    bundle = run_calibration(settings=settings)

    if settings.console_top_rows > 0:
        for key, analysis in sorted(bundle.results.items()):
            _print_sorted_preview(
                f"{key} | MaxVolume",
                analysis.max_volume_sorted,
                settings.console_top_rows,
            )
            _print_sorted_preview(
                f"{key} | MaxDelta",
                analysis.max_delta_sorted,
                settings.console_top_rows,
            )

    for market in bundle.report.get("markets") or []:
        _print_market(market, settings)

    print(f"\n  Готово за {bundle.report['elapsed_sec']:.2f}с\n")
