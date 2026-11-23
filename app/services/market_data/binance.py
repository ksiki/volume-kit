"""HTTP-клиент Binance Spot: тик-сайз и загрузка свечей."""

import requests
import numpy as np


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
