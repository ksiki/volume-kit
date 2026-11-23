"""Модель параметров масштаба рынка."""

from dataclasses import dataclass


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
