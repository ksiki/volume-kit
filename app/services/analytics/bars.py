"""Расчёт баров и опорной цены по одному дню из Parquet-кэша."""

from pathlib import Path

import numpy as np
import pandas as pd

from app.core.config import AppSettings


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
    settings: AppSettings,
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

    if settings.cluster_mode == "round":
        cluster_price = np.round(np.floor((prices + 1e-9) / day_price_step + 0.5) * day_price_step, 8)
    elif settings.cluster_mode == "ceil":
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
