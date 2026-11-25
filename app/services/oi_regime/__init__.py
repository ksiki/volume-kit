"""Сервис OI & Delta Regime: загрузка данных, выравнивание и robust Z-score."""

from app.services.oi_regime.engine import analyze_frame, build_bundle
from app.services.oi_regime.fetcher import (
    AlignedRegimeData,
    OIRegimeFetcher,
    RawRegimeData,
    RegimeFetchCancelled,
    RegimeFetchError,
    align_raw,
    base_asset,
    spot_symbol_candidates,
)
from app.services.oi_regime.models import (
    REGIME_META,
    AnomalyEvent,
    CandleBar,
    OutcomeStats,
    RegimeBundle,
    RegimeParams,
    TrapLevel,
)

__all__ = [
    "REGIME_META",
    "AlignedRegimeData",
    "AnomalyEvent",
    "CandleBar",
    "OIRegimeFetcher",
    "OutcomeStats",
    "RawRegimeData",
    "RegimeBundle",
    "RegimeFetchCancelled",
    "RegimeFetchError",
    "RegimeParams",
    "TrapLevel",
    "align_raw",
    "analyze_frame",
    "base_asset",
    "build_bundle",
    "spot_symbol_candidates",
]
