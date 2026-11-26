"""HTTP-клиент ByBit Futures: тик-сайз и загрузка свечей."""

import numpy as np

from app.core.http_client import http_request


def get_bybit_futures_tick_size(symbol: str) -> float:
    url = "https://api.bybit.com/v5/market/instruments-info"
    resp = http_request("GET", url, params={"category": "linear", "symbol": symbol}, timeout=15)
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

        resp = http_request("GET", url, params=params, timeout=15)
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
