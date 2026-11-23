import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import (
    CLUSTER_MODE,
    HALF_LIFE_DAYS,
    OUTLIER_DAY_MAX_RATIO,
    OUTLIER_DAY_MIN_WEIGHT,
    OUTPUT_DIR,
    SAVE_SORTED_CSV,
    TZ_OFFSET_HOURS,
)

log = logging.getLogger("Analytics")


@dataclass
class PercentileResult:
    value: float
    row_num: int
    total_rows: int
    pct: float


@dataclass
class TimeframeAnalysis:
    exchange: str
    timeframe: str
    days: int
    price_scale: int
    price_step: float
    reference_price: float
    half_life_days: float
    total_bars: int
    excluded_weekend_bars: int
    total_trades: int
    max_volume_sorted: pd.DataFrame
    max_delta_sorted: pd.DataFrame


def _get_half_life_days(timeframe_min: int, total_days: int) -> float:
    if timeframe_min in HALF_LIFE_DAYS:
        return float(HALF_LIFE_DAYS[timeframe_min])
    return max(2.0, total_days / 3.0)


def _get_reference_price(parquet_paths: list[Path]) -> float:
    """Определяет актуальную опорную цену (медиана последних 1000 сделок самого свежего дня)."""
    for path in reversed(parquet_paths):
        df = pd.read_parquet(path, columns=["price"])
        if not df.empty:
            ref_price = float(np.median(df["price"].values[-1000:]))
            if ref_price > 0:
                return ref_price
    raise RuntimeError("Не удалось определить актуальную опорную цену из кэша")


def _calc_day_bars(
    parquet_path: Path,
    timeframe_min: int,
    price_step: float,
    reference_price: float,
) -> tuple[pd.DataFrame, int]:
    """
    Читает один день из Parquet и считает MaxVolume и |MaxDelta| для каждого бара
    с нормировкой объёма и шага кластера на текущую опорную цену.
    """
    df = pd.read_parquet(parquet_path, columns=["ts_ms", "price", "qty", "is_ask"])
    n_trades = len(df)
    if n_trades == 0:
        return pd.DataFrame(), 0

    bar_ms = timeframe_min * 60 * 1000
    bar_time_ms = (df["ts_ms"].values // bar_ms) * bar_ms

    prices = df["price"].values
    qty = df["qty"].values
    day_median_price = float(np.median(prices))

    # Пропорциональный шаг кластера и эквивалент объёма в монетах по текущей цене
    day_price_step = max(price_step * (day_median_price / reference_price), 1e-8)
    adj_qty = qty * (prices / reference_price)

    if CLUSTER_MODE == "round":
        cluster_price = np.round(np.floor((prices + 1e-9) / day_price_step + 0.5) * day_price_step, 8)
    elif CLUSTER_MODE == "ceil":
        cluster_price = np.round(np.ceil((prices - 1e-9) / day_price_step) * day_price_step, 8)
    else:
        cluster_price = np.round(np.floor((prices + 1e-9) / day_price_step) * day_price_step, 8)

    signed_qty = np.where(df["is_ask"].values, adj_qty, -adj_qty)

    work = pd.DataFrame({
        "bar_time_ms": bar_time_ms,
        "cluster_price": cluster_price,
        "qty": adj_qty,
        "signed_qty": signed_qty,
    })

    clusters = (
        work.groupby(["bar_time_ms", "cluster_price"], sort=False, as_index=False)
        .agg(vol=("qty", "sum"), delta=("signed_qty", "sum"))
    )
    clusters["abs_delta"] = clusters["delta"].abs()

    bars = (
        clusters.groupby("bar_time_ms", sort=True, as_index=False)
        .agg(MaxVolume=("vol", "max"), MaxDelta=("abs_delta", "max"))
    )
    return bars, n_trades


def _compute_day_damping_weights(
    utc_dates: pd.Series,
    values: np.ndarray,
    label: str,
    exchange: str,
    timeframe: str,
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

    threshold = global_med_p90 * OUTLIER_DAY_MAX_RATIO
    damping_map: dict[str, float] = {}
    damped_dates: list[str] = []

    for date_str, row in day_stats.iterrows():
        p90 = float(row["p90"])
        if p90 > threshold:
            factor = max(OUTLIER_DAY_MIN_WEIGHT, threshold / p90)
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
        bars_df, n_trades = _calc_day_bars(path, timeframe_min, price_step, reference_price)
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

    dt_display = dt_utc + pd.Timedelta(hours=TZ_OFFSET_HOURS) if TZ_OFFSET_HOURS != 0 else dt_utc
    all_bars["Time"] = dt_display.dt.strftime("%d.%m.%Y %H:%M:%S")
    all_bars["_utc_date"] = dt_utc.dt.strftime("%Y-%m-%d")
    all_bars["MaxVolume"] = all_bars["MaxVolume"].round(2)
    all_bars["MaxDelta"] = all_bars["MaxDelta"].round(2)

    # Time-Decay веса по возрасту бара
    max_bar_ms = int(all_bars["bar_time_ms"].max())
    age_days = (max_bar_ms - all_bars["bar_time_ms"].to_numpy(dtype=np.float64)) / 86_400_000.0
    half_life = _get_half_life_days(timeframe_min, days)
    time_weights = np.power(0.5, age_days / half_life)

    # Демпфирование дней-выбросов
    vol_damp = _compute_day_damping_weights(
        all_bars["_utc_date"], all_bars["MaxVolume"].to_numpy(), "MaxVolume", exchange, timeframe
    )
    delta_damp = _compute_day_damping_weights(
        all_bars["_utc_date"], all_bars["MaxDelta"].to_numpy(), "MaxDelta", exchange, timeframe
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

    if SAVE_SORTED_CSV:
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