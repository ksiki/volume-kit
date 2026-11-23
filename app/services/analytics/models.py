"""Модели результатов кластерного анализа."""

from dataclasses import dataclass

import pandas as pd


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
