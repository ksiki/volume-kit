"""Оркестрация полной калибровки Volume Kit: Binance Spot и ByBit Futures."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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
    MarketScaleInfo,
    compute_scale_info,
    get_binance_spot_tick_size,
    get_bybit_futures_tick_size,
)
from app.services.storage import ensure_days_cached, get_cache_path, get_utc_dates

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]

SPOT_KEY = "binance_spot"
FUTURES_KEY = "bybit_futures"

SPOT_TIMEFRAMES: tuple[tuple[str, int], ...] = (
    ("4h", 240),
    ("1h", 60),
    ("15m", 15),
    ("5m", 5),
)
FUTURES_TIMEFRAMES: tuple[tuple[str, int], ...] = (
    ("1h", 60),
    ("15m", 15),
    ("5m", 5),
)

# Klines-запросы лёгкие, кластерный анализ — тяжёлый по CPU/диску.
SCALE_WORKERS = 4
ANALYSIS_WORKERS = 3


@dataclass(frozen=True)
class MarketSpec:
    """Независимая категория расчёта: рынок и его наборы таймфреймов."""

    key: str
    title: str
    timeframes: tuple[tuple[str, int], ...]

    @property
    def is_spot(self) -> bool:
        return self.key == SPOT_KEY


MARKETS: tuple[MarketSpec, ...] = (
    MarketSpec(SPOT_KEY, "Binance Spot", SPOT_TIMEFRAMES),
    MarketSpec(FUTURES_KEY, "ByBit Futures", FUTURES_TIMEFRAMES),
)


@dataclass
class CalibrationBundle:
    """Результат полного цикла калибровки."""

    report: dict[str, Any]
    results: dict[str, TimeframeAnalysis]
    errors: dict[str, str]


def task_key(exchange: str, timeframe: str) -> str:
    """Ключ задачи расчёта одной пары (рынок, таймфрейм)."""
    return f"{exchange}:{timeframe}"


def _timeframe_days(settings: AppSettings) -> dict[tuple[str, str], int]:
    """Глубина истории (дни) для каждой пары (рынок, таймфрейм)."""
    return {
        (SPOT_KEY, "4h"): settings.days_binance_4h,
        (SPOT_KEY, "1h"): settings.days_binance_1h,
        (SPOT_KEY, "15m"): settings.days_binance_15m,
        (SPOT_KEY, "5m"): settings.days_binance_5m,
        (FUTURES_KEY, "1h"): settings.days_bybit_1h,
        (FUTURES_KEY, "15m"): settings.days_bybit_15m,
        (FUTURES_KEY, "5m"): settings.days_bybit_5m,
    }


def _half_life_days(timeframe_min: int, days: int, settings: AppSettings) -> int:
    half_life = settings.half_life_days.get(timeframe_min)
    if half_life is None or half_life <= 0:
        half_life = max(2.0, days / 3.0)
    return int(round(half_life))


def _timeframe_count_label(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        noun = "таймфрейм"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        noun = "таймфрейма"
    else:
        noun = "таймфреймов"
    return f"{count} {noun}"


def market_specs(settings: AppSettings) -> list[dict[str, Any]]:
    """Статическая спецификация карточек рынков (для плейсхолдеров до расчёта)."""
    days_map = _timeframe_days(settings)
    specs: list[dict[str, Any]] = []

    for market in MARKETS:
        clusters = settings.spot_clusters if market.is_spot else settings.futures_clusters
        rows: list[dict[str, Any]] = []
        for timeframe, minutes in market.timeframes:
            days = days_map[(market.key, timeframe)]
            rows.append(
                {
                    "timeframe": timeframe,
                    "minutes": minutes,
                    "days": days,
                    "half_life_days": _half_life_days(minutes, days, settings),
                }
            )

        specs.append(
            {
                "id": market.key,
                "title": market.title,
                "exchange_key": market.key,
                "clusters": clusters,
                "timeframe_count": len(market.timeframes),
                "summary": (
                    f"{_timeframe_count_label(len(market.timeframes))} • "
                    f"сжатие {clusters} кластеров"
                ),
                "rows": rows,
            }
        )

    return specs


def _format_usd(amount: float) -> str:
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount:.0f}"


def _compact_number(value: float) -> str:
    """Число с двумя знаками без лишних нулей (507.20 -> 507.2)."""
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _format_percentile(res: PercentileResult, reference_price: float) -> dict[str, str]:
    return {
        "value": f"{res.value:.2f}",
        "usd": _format_usd(res.value * reference_price),
        "pos": f"#{res.row_num} / {res.total_rows}",
    }


def _build_row(
    market: MarketSpec,
    timeframe: str,
    minutes: int,
    days: int,
    settings: AppSettings,
    *,
    scale: MarketScaleInfo | None,
    analysis: TimeframeAnalysis | None,
    error: str | None,
    missing_days: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "timeframe": timeframe,
        "minutes": minutes,
        "days": days,
        "half_life_days": _half_life_days(minutes, days, settings),
        "step_str": None,
        "atr_str": None,
        "scale": None,
        "vol_1": None,
        "vol_3": None,
        "delta_1": None,
        "missing_days": missing_days,
        "error": error,
    }

    if scale is None or analysis is None:
        return row

    row["half_life_days"] = int(round(analysis.half_life_days))
    row["step_str"] = f"{scale.price_step:g}"
    row["atr_str"] = f"{scale.atr:.2f}"
    row["scale"] = {
        "value": str(int(scale.price_scale)),
        "meta": f"Шаг {scale.price_step:g} • ATR {_compact_number(scale.atr)}",
    }

    reference_price = analysis.reference_price
    if market.is_spot:
        row["vol_1"] = _format_percentile(
            get_value_at_percent(analysis.max_volume_sorted, "MaxVolume", 1.0),
            reference_price,
        )
        row["vol_3"] = _format_percentile(
            get_value_at_percent(analysis.max_volume_sorted, "MaxVolume", 3.0),
            reference_price,
        )
        row["delta_1"] = _format_percentile(
            get_value_at_percent(analysis.max_delta_sorted, "MaxDelta", 1.0),
            reference_price,
        )
    else:
        row["vol_3"] = _format_percentile(
            get_value_at_percent(analysis.max_volume_sorted, "MaxVolume", 3.0),
            reference_price,
        )

    return row


def _market_reference_price(
    market: MarketSpec,
    results: dict[str, TimeframeAnalysis],
    scales: dict[str, MarketScaleInfo],
) -> float | None:
    for timeframe, _ in market.timeframes:
        key = task_key(market.key, timeframe)
        analysis = results.get(key)
        if analysis is not None and analysis.reference_price > 0:
            return round(analysis.reference_price, 2)
        scale = scales.get(key)
        if scale is not None and scale.reference_price > 0:
            return round(scale.reference_price, 2)
    return None



def run_calibration(
    progress_cb: ProgressCallback | None = None,
    *,
    settings: AppSettings | None = None,
) -> CalibrationBundle:
    """Выполняет полный цикл калибровки по двум рынкам и возвращает отчёт."""
    if settings is None:
        settings = get_config_service().current

    report_lock = threading.Lock()
    last_pct = {"value": -1}

    def _report(pct: int, stage: str) -> None:
        if progress_cb is None:
            return
        with report_lock:
            safe_pct = max(last_pct["value"], max(0, min(100, int(pct))))
            last_pct["value"] = safe_pct
            progress_cb(safe_pct, stage)

    t_total = time.perf_counter()
    log.info("Запуск Volume Kit")

    specs = market_specs(settings)
    days_map = _timeframe_days(settings)
    clusters = {
        SPOT_KEY: settings.spot_clusters,
        FUTURES_KEY: settings.futures_clusters,
    }

    # 1. Параметры бирж (тик-сайзы) — два независимых запроса.
    _report(3, f"Запрос параметров бирж ({settings.symbol})...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_binance = pool.submit(get_binance_spot_tick_size, settings.symbol)
        future_bybit = pool.submit(get_bybit_futures_tick_size, settings.symbol)
        tick_sizes = {
            SPOT_KEY: future_binance.result(),
            FUTURES_KEY: future_bybit.result(),
        }

    # 2. Robust ATR и масштаб цены для всех наборов параллельно.
    _report(10, "Расчёт Robust ATR и масштаба...")
    scale_tasks = [
        (market.key, timeframe)
        for market in MARKETS
        for timeframe, _ in market.timeframes
    ]
    scales: dict[str, MarketScaleInfo] = {}
    errors: dict[str, str] = {}
    scale_done = 0

    with ThreadPoolExecutor(max_workers=SCALE_WORKERS) as pool:
        submitted = {
            pool.submit(
                compute_scale_info,
                exchange,
                settings.symbol,
                timeframe,
                tick_sizes[exchange],
                clusters[exchange],
                settings,
            ): (exchange, timeframe)
            for exchange, timeframe in scale_tasks
        }
        for future in as_completed(submitted):
            exchange, timeframe = submitted[future]
            key = task_key(exchange, timeframe)
            scale_done += 1
            try:
                scales[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - ошибка одного ТФ не рушит расчёт
                errors[key] = str(exc)
                log.exception(
                    f"Ошибка масштаба {exchange} {timeframe}: {exc}",
                    extra={"tool": "Calibrator"},
                )
            _report(
                10 + int(20 * scale_done / max(1, len(scale_tasks))),
                f"Масштаб и ATR · {scale_done}/{len(scale_tasks)}",
            )

    # 3. Кэш сделок: Binance Spot (до 120 дней) и ByBit Futures параллельно.
    market_days = {
        market.key: max(
            days_map[(market.key, timeframe)] for timeframe, _ in market.timeframes
        )
        for market in MARKETS
    }
    dates_by_market = {
        key: get_utc_dates(days) for key, days in market_days.items()
    }
    missing_expected = {
        key: sum(
            1
            for date_str in dates
            if not get_cache_path(key, settings.symbol, date_str).exists()
        )
        for key, dates in dates_by_market.items()
    }
    total_missing = sum(missing_expected.values())

    cache_done = {key: 0 for key in market_days}
    cache_errors: dict[str, str] = {}

    def _on_cache_progress(exchange: str, done: int, total: int) -> None:
        cache_done[exchange] = done
        overall = sum(cache_done.values())
        _report(
            30 + int(25 * overall / max(1, total_missing)),
            f"Загрузка истории · {overall}/{total_missing} дней",
        )

    _report(
        30,
        "Проверка кэша и загрузка истории "
        f"(Binance {market_days[SPOT_KEY]}д / ByBit {market_days[FUTURES_KEY]}д)...",
    )

    download_workers = max(2, settings.download_workers // 2)
    paths_by_market: dict[str, list[Path]] = {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted_cache = {
            pool.submit(
                ensure_days_cached,
                key,
                settings.symbol,
                dates_by_market[key],
                settings,
                max_workers=download_workers,
                progress_cb=_on_cache_progress,
            ): key
            for key in market_days
        }
        for future in as_completed(submitted_cache):
            exchange = submitted_cache[future]
            try:
                paths_by_market[exchange] = future.result()
            except Exception as exc:  # noqa: BLE001 - ошибка рынка не рушит второй рынок
                cache_errors[exchange] = str(exc)
                log.exception(
                    f"Ошибка загрузки истории {exchange}: {exc}",
                    extra={"tool": "Calibrator"},
                )

    _report(55, "История загружена, проверка целостности...")

    existing_dates: dict[str, set[str]] = {
        key: {path.stem for path in paths_by_market.get(key, [])}
        for key in market_days
    }
    for key, dates in dates_by_market.items():
        if key in cache_errors:
            continue
        missing_dates = [
            date_str for date_str in dates if date_str not in existing_dates[key]
        ]
        if missing_dates:
            preview = ", ".join(missing_dates[:5])
            suffix = "..." if len(missing_dates) > 5 else ""
            log.warning(
                f"Кэш {key}: отсутствует {len(missing_dates)} дн. "
                f"({preview}{suffix}) — расчёт по доступным данным"
            )

    def _missing_in_window(exchange: str, days: int) -> int:
        if exchange in cache_errors:
            return 0
        window = dates_by_market[exchange][-days:]
        returned = existing_dates.get(exchange, set())
        return sum(1 for date_str in window if date_str not in returned)

    # 4. Кластерный анализ всех наборов параллельно.
    analysis_tasks: list[
        tuple[MarketSpec, str, int, int, list[Path], MarketScaleInfo]
    ] = []
    for market in MARKETS:
        for timeframe, minutes in market.timeframes:
            key = task_key(market.key, timeframe)
            if key in errors:
                continue
            if market.key in cache_errors:
                errors[key] = f"История недоступна: {cache_errors[market.key]}"
                continue
            scale = scales.get(key)
            if scale is None:
                continue

            days = days_map[(market.key, timeframe)]
            window_paths = paths_by_market.get(market.key, [])[-days:]
            if not window_paths:
                errors[key] = "Нет данных в кэше для расчёта"
                log.error(
                    f"Нет данных в кэше: {market.key} {timeframe} ({days}д)",
                    extra={"tool": "Calibrator"},
                )
                continue
            analysis_tasks.append(
                (market, timeframe, minutes, days, window_paths, scale)
            )

    results: dict[str, TimeframeAnalysis] = {}
    analysis_done = 0
    _report(55, f"Кластерный анализ · 0/{len(analysis_tasks)}")

    with ThreadPoolExecutor(max_workers=ANALYSIS_WORKERS) as pool:
        submitted_analysis = {
            pool.submit(
                analyze_timeframe,
                market.key,
                settings.symbol,
                timeframe,
                minutes,
                days,
                window_paths,
                scale.price_scale,
                scale.price_step,
                scale.reference_price,
                settings=settings,
            ): (market, timeframe)
            for market, timeframe, minutes, days, window_paths, scale in analysis_tasks
        }
        for future in as_completed(submitted_analysis):
            market, timeframe = submitted_analysis[future]
            key = task_key(market.key, timeframe)
            analysis_done += 1
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - ошибка одного ТФ не рушит остальные
                errors[key] = str(exc)
                log.exception(
                    f"Ошибка кластерного анализа {market.key} {timeframe}: {exc}",
                    extra={"tool": "Calibrator"},
                )
            total_tasks = len(analysis_tasks)
            _report(
                55 + int(43 * analysis_done / max(1, total_tasks)),
                f"Кластерный анализ · {analysis_done}/{total_tasks}",
            )

    if settings.save_sorted_csv:
        log.info(f"Полные таблицы сохранены в: {OUTPUT_DIR}")

    # 5. Сборка отчёта в стабильном порядке (не зависит от порядка потоков).
    markets_report: list[dict[str, Any]] = []
    for market, spec in zip(MARKETS, specs):
        rows = [
            _build_row(
                market,
                timeframe,
                minutes,
                days_map[(market.key, timeframe)],
                settings,
                scale=scales.get(task_key(market.key, timeframe)),
                analysis=results.get(task_key(market.key, timeframe)),
                error=errors.get(task_key(market.key, timeframe)),
                missing_days=_missing_in_window(
                    market.key, days_map[(market.key, timeframe)]
                ),
            )
            for timeframe, minutes in market.timeframes
        ]
        markets_report.append({**spec, "rows": rows})

    elapsed = round(time.perf_counter() - t_total, 2)
    log.info(f"Калибровка {settings.symbol} завершена за {elapsed:.2f}с")

    report: dict[str, Any] = {
        "symbol": settings.symbol,
        "spot_ref_price": _market_reference_price(MARKETS[0], results, scales),
        "fut_ref_price": _market_reference_price(MARKETS[1], results, scales),
        "elapsed_sec": elapsed,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
        "markets": markets_report,
    }

    _report(100, f"Готово за {elapsed:.2f}с")

    return CalibrationBundle(report=report, results=results, errors=errors)
