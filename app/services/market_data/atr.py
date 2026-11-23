"""Robust ATR: винзоризация, time-decay и фильтрация выходных."""

import math

import numpy as np
import pandas as pd

from app.core.config import AppSettings


def _timeframe_to_minutes(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"Неподдерживаемый таймфрейм: {timeframe}")


def _get_atr_klines_count(timeframe_min: int, settings: AppSettings) -> int:
    days = settings.atr_lookback_days.get(timeframe_min)
    if days is None:
        nearest_tf = min(settings.atr_lookback_days.keys(), key=lambda k: abs(k - timeframe_min))
        days = settings.atr_lookback_days[nearest_tf]
    bars = int(math.ceil(days * 1440 / timeframe_min))
    return max(bars, 200)


def _get_atr_half_life_days(timeframe_min: int, settings: AppSettings) -> float:
    hl = settings.half_life_days.get(timeframe_min)
    if hl is not None and hl > 0:
        return float(hl)
    if settings.half_life_days:
        nearest_tf = min(settings.half_life_days.keys(), key=lambda k: abs(k - timeframe_min))
        if settings.half_life_days[nearest_tf] > 0:
            return float(settings.half_life_days[nearest_tf])
    return 7.0


def calc_robust_atr(
    timestamps_ms: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timeframe_min: int,
    settings: AppSettings,
) -> float:
    """
    Устойчивый расчёт базовой волатильности:
    1. Считает относительный True Range (TR / prev_close) по всем непрерывным свечам.
    2. Для внутридневных ТФ (<= 1h) исключает выходные (Сб/Вс UTC).
    3. Ограничивает выбросы сверху по перцентилю ATR_WINSOR_PCT.
    4. Усредняет с экспоненциальным затуханием (time-decay по HALF_LIFE_DAYS)
       и переводит обратно в доллары по актуальной цене закрытия.
    """
    if len(closes) < 20:
        raise ValueError(f"Недостаточно свечей для расчёта Robust ATR: получено {len(closes)}")

    prev_close = closes[:-1]
    tr = np.maximum.reduce([
        highs[1:] - lows[1:],
        np.abs(highs[1:] - prev_close),
        np.abs(lows[1:] - prev_close),
    ])
    ts_tr = timestamps_ms[1:]

    valid_mask = prev_close > 0
    tr_pct = tr[valid_mask] / prev_close[valid_mask]
    ts_tr = ts_tr[valid_mask]

    if tr_pct.size < 20:
        raise ValueError(f"Недостаточно валидных свечей для расчёта Robust ATR: {tr_pct.size}")

    if timeframe_min <= 60:
        weekdays = pd.to_datetime(ts_tr, unit="ms", utc=True).dayofweek.values
        mask = weekdays < 5
        if np.count_nonzero(mask) >= 50:
            tr_pct = tr_pct[mask]
            ts_tr = ts_tr[mask]

    upper_cap = float(np.percentile(tr_pct, settings.atr_winsor_pct))
    winsorized_tr_pct = np.minimum(tr_pct, upper_cap)

    max_ts_ms = int(ts_tr.max())
    age_days = (max_ts_ms - ts_tr.astype(np.float64)) / 86_400_000.0
    half_life = _get_atr_half_life_days(timeframe_min, settings)
    weights = np.power(0.5, age_days / half_life)

    robust_tr_pct = float(np.average(winsorized_tr_pct, weights=weights))
    current_price = float(closes[-1])
    return robust_tr_pct * current_price
