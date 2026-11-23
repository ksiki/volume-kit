"""Кластерный анализ таймфрейма: нормировка, time-decay и перцентили."""

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.config import AppSettings
from app.core.paths import OUTPUT_DIR
from app.services.analytics.bars import _calc_day_bars, _get_reference_price
from app.services.analytics.models import PercentileResult, TimeframeAnalysis
from app.services.analytics.weighting import (
    _check_tail_concentration,
    _compute_day_damping_weights,
    _get_half_life_days,
)

log = logging.getLogger(__name__)


def analyze_timeframe(
    exchange: str,
    symbol: str,
    timeframe: str,
    timeframe_min: int,
    days: int,
    parquet_paths: list[Path],
    price_scale: int,
    price_step: float,
    reference_price: float | None = None,
    *,
    settings: AppSettings,
) -> TimeframeAnalysis:
    """
    Прогоняет историю таймфрейма:
    - ценовая нормировка объёма и шага кластера;
    - модуль дельты (|MaxDelta|);
    - исключение выходных для ТФ <= 15m;
    - экспоненциальный time-decay и демпфирование дней-выбросов.
    """
    t_start = time.perf_counter()
    if reference_price is None or reference_price <= 0:
        reference_price = _get_reference_price(parquet_paths)

    daily_frames: list[pd.DataFrame] = []
    total_trades = 0

    for path in parquet_paths:
        bars_df, n_trades = _calc_day_bars(path, timeframe_min, price_step, reference_price, settings)
        if not bars_df.empty:
            daily_frames.append(bars_df)
        total_trades += n_trades

    if not daily_frames:
        raise RuntimeError(f"Нет данных для расчёта {exchange} {timeframe}")

    all_bars = pd.concat(daily_frames, ignore_index=True)
    all_bars = all_bars.sort_values("bar_time_ms", kind="mergesort").reset_index(drop=True)
    dt_utc = pd.to_datetime(all_bars["bar_time_ms"], unit="ms", utc=True)

    # Исключаем Сб/Вс (UTC) для внутридневных ТФ <= 15m
    excluded_weekend_bars = 0
    if timeframe_min <= 60:
        weekday_mask = dt_utc.dt.dayofweek < 5
        if int(weekday_mask.sum()) >= 50:
            excluded_weekend_bars = int((~weekday_mask).sum())
            all_bars = all_bars.loc[weekday_mask].reset_index(drop=True)
            dt_utc = dt_utc.loc[weekday_mask].reset_index(drop=True)
        else:
            log.warning(
                f"Слишком мало будничных баров для {exchange} {timeframe} — выходные оставлены"
            )

    dt_display = (
        dt_utc + pd.Timedelta(hours=settings.tz_offset_hours)
        if settings.tz_offset_hours != 0
        else dt_utc
    )
    all_bars["Time"] = dt_display.dt.strftime("%d.%m.%Y %H:%M:%S")
    all_bars["_utc_date"] = dt_utc.dt.strftime("%Y-%m-%d")
    all_bars["MaxVolume"] = all_bars["MaxVolume"].round(2)
    all_bars["MaxDelta"] = all_bars["MaxDelta"].round(2)

    # Time-Decay веса по возрасту бара
    max_bar_ms = int(all_bars["bar_time_ms"].max())
    age_days = (max_bar_ms - all_bars["bar_time_ms"].to_numpy(dtype=np.float64)) / 86_400_000.0
    half_life = _get_half_life_days(timeframe_min, days, settings)
    time_weights = np.power(0.5, age_days / half_life)

    # Демпфирование дней-выбросов
    vol_damp = _compute_day_damping_weights(
        all_bars["_utc_date"], all_bars["MaxVolume"].to_numpy(), "MaxVolume", exchange, timeframe, settings
    )
    delta_damp = _compute_day_damping_weights(
        all_bars["_utc_date"], all_bars["MaxDelta"].to_numpy(), "MaxDelta", exchange, timeframe, settings
    )

    all_bars["WeightVol"] = time_weights * vol_damp
    all_bars["WeightDelta"] = time_weights * delta_damp

    max_vol_df = (
        all_bars[["Time", "MaxVolume", "WeightVol", "_utc_date"]]
        .rename(columns={"WeightVol": "Weight"})
        .sort_values(by="MaxVolume", ascending=False, kind="mergesort")
        .reset_index(drop=True)
    )
    max_vol_df.insert(0, "Rank", np.arange(1, len(max_vol_df) + 1))

    max_delta_df = (
        all_bars[["Time", "MaxDelta", "WeightDelta", "_utc_date"]]
        .rename(columns={"WeightDelta": "Weight"})
        .sort_values(by="MaxDelta", ascending=False, kind="mergesort")
        .reset_index(drop=True)
    )
    max_delta_df.insert(0, "Rank", np.arange(1, len(max_delta_df) + 1))

    _check_tail_concentration(max_vol_df, "MaxVolume", exchange, timeframe)
    _check_tail_concentration(max_delta_df, "MaxDelta", exchange, timeframe)

    max_vol_df = max_vol_df.drop(columns=["_utc_date"])
    max_delta_df = max_delta_df.drop(columns=["_utc_date"])

    if settings.save_sorted_csv:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        prefix = f"{exchange}_{symbol}_{timeframe}_{days}d"
        max_vol_df.assign(Weight=max_vol_df["Weight"].round(4)).to_csv(
            OUTPUT_DIR / f"{prefix}_MaxVolume.csv", index=False, sep=";"
        )
        max_delta_df.assign(Weight=max_delta_df["Weight"].round(4)).to_csv(
            OUTPUT_DIR / f"{prefix}_MaxDelta.csv", index=False, sep=";"
        )

    elapsed = time.perf_counter() - t_start
    speed = total_trades / elapsed if elapsed > 0 else 0
    log.info(
        f"Расчёт {exchange:13s} {timeframe:3s} ({days:2d}д, HL={half_life:2.0f}д, масштаб={price_scale}) | "
        f"Баров: {len(all_bars):4,d} (вых. -{excluded_weekend_bars}) | "
        f"Сделок: {total_trades:11,d} | {elapsed:4.2f}с ({speed:,.0f} сд/с)"
    )

    return TimeframeAnalysis(
        exchange=exchange,
        timeframe=timeframe,
        days=days,
        price_scale=price_scale,
        price_step=price_step,
        reference_price=reference_price,
        half_life_days=half_life,
        total_bars=len(all_bars),
        excluded_weekend_bars=excluded_weekend_bars,
        total_trades=total_trades,
        max_volume_sorted=max_vol_df,
        max_delta_sorted=max_delta_df,
    )


def get_value_at_percent(sorted_df: pd.DataFrame, col: str, pct: float) -> PercentileResult:
    """Возвращает значение на взвешенном перцентиле `pct`% от накопленной массы весов."""
    total = len(sorted_df)
    if total == 0:
        return PercentileResult(value=0.0, row_num=0, total_rows=0, pct=pct)

    weights = sorted_df["Weight"].to_numpy(dtype=np.float64)
    weight_sum = float(weights.sum())
    if weight_sum <= 0:
        raise ValueError("Сумма весов равна нулю — невозможно рассчитать перцентиль")

    cum_share = np.cumsum(weights) / weight_sum
    idx = int(np.searchsorted(cum_share, pct / 100.0, side="left"))
    idx = max(0, min(total - 1, idx))

    return PercentileResult(
        value=float(sorted_df.iloc[idx][col]),
        row_num=idx + 1,
        total_rows=total,
        pct=pct,
    )
