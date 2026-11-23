"""Получение текущей цены Binance Spot без зависимостей от Qt."""

import json
import urllib.parse
import urllib.request


_BINANCE_SPOT_PRICE_URLS = (
    "https://api.binance.com/api/v3/ticker/price",
    "https://data-api.binance.vision/api/v3/ticker/price",
)


def fetch_binance_spot_price(symbol: str, timeout: float = 3.0) -> float | None:
    """Запрашивает текущую цену на Binance Spot для указанного тикера."""
    query = urllib.parse.urlencode({"symbol": symbol})
    headers = {"User-Agent": "Volume Kit/1.0"}

    for base_url in _BINANCE_SPOT_PRICE_URLS:
        req = urllib.request.Request(f"{base_url}?{query}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            price = float(data["price"])
            if price > 0:
                return price
        except Exception:
            continue
    return None
