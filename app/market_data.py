import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests

from .config import ATR_LOOKBACK_DAYS, ATR_WINSOR_PCT, HALF_LIFE_DAYS, SCALE_ROUNDING_RULES

log = logging.getLogger("MarketData")


@dataclass
class MarketScaleInfo:
    exchange: str
    timeframe: str
    tick_size: float
    atr: float
    target_clusters: int
    raw_scale: float
    price_scale: int
    price_step: float
    reference_price: float = 0.0


def _timeframe_to_minutes(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"Неподдерживаемый таймфрейм: {timeframe}")


def _get_atr_klines_count(timeframe_min: int) -> int:
    days = ATR_LOOKBACK_DAYS.get(timeframe_min)
    if days is None:
        nearest_tf = min(ATR_LOOKBACK_DAYS.keys(), key=lambda k: abs(k - timeframe_min))
        days = ATR_LOOKBACK_DAYS[nearest_tf]
    bars = int(math.ceil(days * 1440 / timeframe_min))
    return max(bars, 200)


def round_scale(value: float) -> int:
    """Плавно округляет масштаб цены до ближайшего шага из SCALE_ROUNDING_RULES."""
    if value <= 0:
        return 1
    for limit, step in SCALE_ROUNDING_RULES:
        if value <= limit:
            return max(1, int(round(value / step) * step))
    return max(1, int(round(value)))


def _get_atr_half_life_days(timeframe_min: int) -> float:
    hl = HALF_LIFE_DAYS.get(timeframe_min)
    if hl is not None and hl > 0:
        return float(hl)
    if HALF_LIFE_DAYS:
        nearest_tf = min(HALF_LIFE_DAYS.keys(), key=lambda k: abs(k - timeframe_min))
        if HALF_LIFE_DAYS[nearest_tf] > 0:
            return float(HALF_LIFE_DAYS[nearest_tf])
    return 7.0


def calc_robust_atr(
    timestamps_ms: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timeframe_min: int,
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

    upper_cap = float(np.percentile(tr_pct, ATR_WINSOR_PCT))
    winsorized_tr_pct = np.minimum(tr_pct, upper_cap)

    max_ts_ms = int(ts_tr.max())
    age_days = (max_ts_ms - ts_tr.astype(np.float64)) / 86_400_000.0
    half_life = _get_atr_half_life_days(timeframe_min)
    weights = np.power(0.5, age_days / half_life)

    robust_tr_pct = float(np.average(winsorized_tr_pct, weights=weights))
    current_price = float(closes[-1])
    return robust_tr_pct * current_price


def get_binance_spot_tick_size(symbol: str) -> float:
    url = f"https://api.binance.com/api/v3/exchangeInfo?symbol={symbol}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    for f in resp.json()["symbols"][0]["filters"]:
        if f["filterType"] == "PRICE_FILTER":
            return float(f["tickSize"])
    raise ValueError(f"Не найден PRICE_FILTER для Binance Spot {symbol}")


def _fetch_binance_spot_klines(
    symbol: str,
    interval: str,
    total_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Загружает `total_bars` закрытых свечей Binance Spot с пагинацией назад."""
    url = "https://api.binance.com/api/v3/klines"
    collected: dict[int, tuple[float, float, float]] = {}
    end_time: int | None = None
    first_call = True

    while len(collected) < total_bars:
        remaining = total_bars - len(collected)
        batch_limit = min(1000, remaining + (1 if first_call else 0))
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "limit": batch_limit,
        }
        if end_time is not None:
            params["endTime"] = end_time

        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        if not raw:
            break

        if first_call:
            raw = raw[:-1]
            first_call = False
        if not raw:
            break

        oldest_ts = int(raw[0][0])
        for c in raw:
            collected[int(c[0])] = (float(c[2]), float(c[3]), float(c[4]))

        if len(raw) < batch_limit - 1:
            break
        if end_time is not None and oldest_ts >= end_time:
            break
        end_time = oldest_ts - 1

    sorted_ts = sorted(collected.keys())[-total_bars:]
    ts = np.array(sorted_ts, dtype=np.int64)
    highs = np.array([collected[t][0] for t in sorted_ts], dtype=np.float64)
    lows = np.array([collected[t][1] for t in sorted_ts], dtype=np.float64)
    closes = np.array([collected[t][2] for t in sorted_ts], dtype=np.float64)
    return ts, highs, lows, closes


def get_bybit_futures_tick_size(symbol: str) -> float:
    url = "https://api.bybit.com/v5/market/instruments-info"
    resp = requests.get(url, params={"category": "linear", "symbol": symbol}, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("retCode") != 0:
        raise RuntimeError(f"Ошибка ByBit API instruments-info: {payload}")
    return float(payload["result"]["list"][0]["priceFilter"]["tickSize"])


def _fetch_bybit_futures_klines(
    symbol: str,
    interval_min: int,
    total_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Загружает `total_bars` закрытых свечей ByBit Linear Futures с пагинацией назад."""
    url = "https://api.bybit.com/v5/market/kline"
    collected: dict[int, tuple[float, float, float]] = {}
    end_time: int | None = None
    first_call = True

    while len(collected) < total_bars:
        remaining = total_bars - len(collected)
        batch_limit = min(1000, remaining + (1 if first_call else 0))
        params: dict[str, str | int] = {
            "category": "linear",
            "symbol": symbol,
            "interval": str(interval_min),
            "limit": batch_limit,
        }
        if end_time is not None:
            params["end"] = end_time

        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Ошибка ByBit API kline: {payload}")

        raw_list = payload["result"]["list"]
        if not raw_list:
            break

        rows = sorted(raw_list, key=lambda x: int(x[0]))
        if first_call:
            rows = rows[:-1]
            first_call = False
        if not rows:
            break

        oldest_ts = int(rows[0][0])
        for c in rows:
            collected[int(c[0])] = (float(c[2]), float(c[3]), float(c[4]))

        if len(raw_list) < batch_limit - 1:
            break
        if end_time is not None and oldest_ts >= end_time:
            break
        end_time = oldest_ts - 1

    sorted_ts = sorted(collected.keys())[-total_bars:]
    ts = np.array(sorted_ts, dtype=np.int64)
    highs = np.array([collected[t][0] for t in sorted_ts], dtype=np.float64)
    lows = np.array([collected[t][1] for t in sorted_ts], dtype=np.float64)
    closes = np.array([collected[t][2] for t in sorted_ts], dtype=np.float64)
    return ts, highs, lows, closes


def compute_scale_info(
    exchange: str,
    symbol: str,
    timeframe: str,
    tick_size: float,
    target_clusters: int,
) -> MarketScaleInfo:
    """Считает Robust ATR и Масштаб Цены по формуле: robust_ATR / tickSize / target_clusters."""
    tf_min = _timeframe_to_minutes(timeframe)
    total_bars = _get_atr_klines_count(tf_min)

    if exchange == "binance_spot":
        ts, highs, lows, closes = _fetch_binance_spot_klines(symbol, timeframe, total_bars)
    elif exchange == "bybit_futures":
        ts, highs, lows, closes = _fetch_bybit_futures_klines(symbol, tf_min, total_bars)
    else:
        raise ValueError(f"Неизвестная биржа: {exchange}")

    atr = calc_robust_atr(ts, highs, lows, closes, timeframe_min=tf_min)
    raw_scale = atr / tick_size / target_clusters
    price_scale = round_scale(raw_scale)
    price_step = round(tick_size * price_scale, 8)

    log.info(
        f"RobustATR ({len(closes):4d} баров) | {exchange:13s} {timeframe:3s} | "
        f"ATR={atr:7.2f} | raw={raw_scale:7.2f} -> Масштаб={price_scale} (шаг={price_step})"
    )

    return MarketScaleInfo(
        exchange=exchange,
        timeframe=timeframe,
        tick_size=tick_size,
        atr=atr,
        target_clusters=target_clusters,
        raw_scale=raw_scale,
        price_scale=price_scale,
        price_step=price_step,
        reference_price=float(closes[-1]),
    )