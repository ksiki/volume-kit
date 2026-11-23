"""Сервис OI & Delta Regime: загрузка данных, выравнивание и robust Z-score."""

from app.services.oi_regime.engine import analyze_frame, build_bundle
from app.services.oi_regime.fetcher import (
    AlignedRegimeData,
    OIRegimeFetcher,
    RawRegimeData,
    RegimeFetchError,
    align_raw,
    base_asset,
)
from app.services.oi_regime.models import (
    REGIME_META,
    AnomalyEvent,
    CandleBar,
    RegimeBundle,
    RegimeParams,
)

__all__ = [
    "REGIME_META",
    "AlignedRegimeData",
    "AnomalyEvent",
    "CandleBar",
    "OIRegimeFetcher",
    "RawRegimeData",
    "RegimeBundle",
    "RegimeFetchError",
    "RegimeParams",
    "align_raw",
    "analyze_frame",
    "base_asset",
    "build_bundle",
]
