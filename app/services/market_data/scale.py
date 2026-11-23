"""Масштаб цены кластеров и правила его округления."""

import logging

from app.core.config import AppSettings
from app.services.market_data.atr import (
    _get_atr_klines_count,
    _timeframe_to_minutes,
    calc_robust_atr,
)
from app.services.market_data.binance import _fetch_binance_spot_klines
from app.services.market_data.bybit import _fetch_bybit_futures_klines
from app.services.market_data.models import MarketScaleInfo

log = logging.getLogger("MarketData")


def round_scale(value: float, settings: AppSettings) -> int:
    """Плавно округляет масштаб цены до ближайшего шага из SCALE_ROUNDING_RULES."""
    if value <= 0:
        return 1
    for limit, step in settings.scale_rounding_rules:
        if value <= limit:
            return max(1, int(round(value / step) * step))
    return max(1, int(round(value)))


def compute_scale_info(
    exchange: str,
    symbol: str,
    timeframe: str,
    tick_size: float,
    target_clusters: int,
    settings: AppSettings,
) -> MarketScaleInfo:
    """Считает Robust ATR и Масштаб Цены по формуле: robust_ATR / tickSize / target_clusters."""
    tf_min = _timeframe_to_minutes(timeframe)
    total_bars = _get_atr_klines_count(tf_min, settings)

    if exchange == "binance_spot":
        ts, highs, lows, closes = _fetch_binance_spot_klines(symbol, timeframe, total_bars)
    elif exchange == "bybit_futures":
        ts, highs, lows, closes = _fetch_bybit_futures_klines(symbol, tf_min, total_bars)
    else:
        raise ValueError(f"Неизвестная биржа: {exchange}")

    atr = calc_robust_atr(ts, highs, lows, closes, timeframe_min=tf_min, settings=settings)
    raw_scale = atr / tick_size / target_clusters
    price_scale = round_scale(raw_scale, settings)
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
