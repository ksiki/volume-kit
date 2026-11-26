"""Получение текущей цены Binance Spot без зависимостей от Qt."""

from __future__ import annotations

from app.core.http_client import create_http_session

_BINANCE_SPOT_PRICE_URLS = (
    "https://api.binance.com/api/v3/ticker/price",
    "https://data-api.binance.vision/api/v3/ticker/price",
)


def fetch_binance_spot_price(symbol: str, timeout: float = 3.0) -> float | None:
    """Запрашивает текущую цену на Binance Spot для указанного тикера."""

    for base_url in _BINANCE_SPOT_PRICE_URLS:
        try:
            with create_http_session() as session:
                response = session.get(
                    base_url,
                    params={"symbol": symbol},
                    timeout=timeout,
                )
                response.raise_for_status()
                data = response.json()
            price = float(data["price"])
            if price > 0:
                return price
        except Exception:  # noqa: BLE001, S112 - сервис цены не должен ломать UI
            continue
    return None
