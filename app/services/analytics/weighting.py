"""Веса time-decay и демпфирование дней-выбросов."""

import logging

import numpy as np
import pandas as pd

from app.core.config import AppSettings

log = logging.getLogger("Analytics")


def _get_half_life_days(timeframe_min: int, total_days: int, settings: AppSettings) -> float:
    if timeframe_min in settings.half_life_days:
        return float(settings.half_life_days[timeframe_min])
    return max(2.0, total_days / 3.0)


def _compute_day_damping_weights(
    utc_dates: pd.Series,
    values: np.ndarray,
    label: str,
    exchange: str,
    timeframe: str,
    settings: AppSettings,
) -> np.ndarray:
    """Плавно снижает вес экстремальных дней-выбросов (p90 дня > OUTLIER_DAY_MAX_RATIO * медианы)."""
    tmp = pd.DataFrame({"date": utc_dates.values, "val": values})
    day_stats = tmp.groupby("date")["val"].agg(
        count="count",
        p90=lambda s: float(np.percentile(s, 90)),
    )
    valid_days = day_stats[day_stats["count"] >= 5]
    if len(valid_days) < 3:
        return np.ones(len(values), dtype=np.float64)

    global_med_p90 = float(valid_days["p90"].median())
    if global_med_p90 <= 0:
        return np.ones(len(values), dtype=np.float64)

    threshold = global_med_p90 * settings.outlier_day_max_ratio
    damping_map: dict[str, float] = {}
    damped_dates: list[str] = []

    for date_str, row in day_stats.iterrows():
        p90 = float(row["p90"])
        if p90 > threshold:
            factor = max(settings.outlier_day_min_weight, threshold / p90)
            damping_map[str(date_str)] = factor
            damped_dates.append(f"{date_str}(x{factor:.2f})")
        else:
            damping_map[str(date_str)] = 1.0

    if damped_dates:
        log.info(
            f"Демпфирование выбросов [{exchange} {timeframe} | {label}]: {', '.join(damped_dates)}"
        )

    return utc_dates.map(damping_map).fillna(1.0).to_numpy(dtype=np.float64)


def _check_tail_concentration(
    sorted_df: pd.DataFrame,
    metric_label: str,
    exchange: str,
    timeframe: str,
) -> None:
    """Предупреждает в лог, если один день формирует >40% взвешенного топ-1%."""
    if sorted_df.empty:
        return

    weights = sorted_df["Weight"].to_numpy(dtype=np.float64)
    total_w = float(weights.sum())
    if total_w <= 0:
        return

    cum_share = np.cumsum(weights) / total_w
    cutoff_idx = int(np.searchsorted(cum_share, 0.01, side="left")) + 1
    top_slice = sorted_df.iloc[:max(1, cutoff_idx)]
    top_w_sum = float(top_slice["Weight"].sum())
    if top_w_sum <= 0:
        return

    by_day = top_slice.groupby("_utc_date")["Weight"].sum()
    max_day = str(by_day.idxmax())
    max_share = float(by_day.max() / top_w_sum)

    if max_share > 0.40:
        log.warning(
            f"Концентрация хвоста [{exchange} {timeframe} | {metric_label}]: "
            f"день {max_day} занимает {max_share * 100:.1f}% топ-1%"
        )
