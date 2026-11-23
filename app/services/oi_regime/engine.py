"""Математическое ядро OI & Delta Regime: robust Z, режимы и контекстные теги."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.services.oi_regime.fetcher import (
    AlignedRegimeData,
    RawRegimeData,
    align_raw,
    base_asset,
)
from app.services.oi_regime.models import (
    REGIME_META,
    SPOT_STATUS_DIV,
    SPOT_STATUS_NEUTRAL,
    SPOT_STATUS_SYNC,
    SPOT_STATUS_UNAVAILABLE,
    AnomalyEvent,
    CandleBar,
    RegimeBundle,
    RegimeParams,
)

log = logging.getLogger(__name__)

_EPS = 1e-12
_MAD_TO_SIGMA = 0.6745
_MEANAD_TO_SIGMA = 1.253314


def _mad_last(window: np.ndarray) -> float:
    values = np.asarray(window, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    median = float(np.median(values))
    return float(np.median(np.abs(values - median)))


def _meanad_last(window: np.ndarray) -> float:
    values = np.asarray(window, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    median = float(np.median(values))
    return float(np.mean(np.abs(values - median)))


def _rolling_robust_z(series: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    """Z по предшествующим N значениям: shift(1) исключает самозагрязнение."""
    window = max(1, int(window))
    values = pd.to_numeric(series, errors="coerce").astype(np.float64)
    prev = values.shift(1)

    median = prev.rolling(window, min_periods=window).median()
    mad = prev.rolling(window, min_periods=window).apply(_mad_last, raw=True)
    meanad = prev.rolling(window, min_periods=window).apply(_meanad_last, raw=True)
    scale = meanad * _MEANAD_TO_SIGMA
    valid_count = prev.notna().rolling(window, min_periods=0).sum()

    z = pd.Series(np.nan, index=values.index, dtype=np.float64)
    base = values.notna() & median.notna()

    primary = base & mad.notna() & (mad > _EPS)
    z.loc[primary] = (
        _MAD_TO_SIGMA * (values.loc[primary] - median.loc[primary]) / mad.loc[primary]
    )

    fallback = base & (mad.isna() | (mad <= _EPS)) & scale.notna() & (scale > _EPS)
    z.loc[fallback] = (values.loc[fallback] - median.loc[fallback]) / scale.loc[fallback]

    degenerate = (
        base
        & (mad.isna() | (mad <= _EPS))
        & (scale.isna() | (scale <= _EPS))
    )
    z.loc[degenerate] = 0.0

    warmup = (valid_count < window) | values.isna()
    return z, warmup.astype(bool)


def _opt_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _sign(value: float | None) -> int:
    if value is None or not np.isfinite(value) or value == 0:
        return 0
    return 1 if value > 0 else -1


def _absorption(
    bar_open: float,
    bar_close: float,
    bar_high: float,
    bar_low: float,
    z_fut: float | None,
    params: RegimeParams,
) -> tuple[bool, str | None, str | None]:
    if z_fut is None:
        return False, None, None
    bar_range = bar_high - bar_low
    close_pos = (bar_close - bar_low) / bar_range if bar_range > 0 else 0.5

    if z_fut >= params.absorption_min_z_delta:
        reasons: list[str] = []
        if bar_close <= bar_open:
            reasons.append("медвежья свеча на позитивной дельте")
        if close_pos < params.absorption_close_ratio:
            reasons.append("закрытие в нижней трети")
        if reasons:
            return True, "LONG_TRAP", "; ".join(reasons)

    if z_fut <= -params.absorption_min_z_delta:
        reasons = []
        if bar_close >= bar_open:
            reasons.append("бычья свеча на негативной дельте")
        if close_pos > (1.0 - params.absorption_close_ratio):
            reasons.append("закрытие в верхней трети")
        if reasons:
            return True, "SHORT_TRAP", "; ".join(reasons)

    return False, None, None


def _spot_status(
    z_spot: float | None,
    z_fut: float | None,
    z_doi: float | None,
    spot_available: bool,
    params: RegimeParams,
) -> str:
    if not spot_available or z_spot is None:
        return SPOT_STATUS_UNAVAILABLE if not spot_available else SPOT_STATUS_NEUTRAL

    spot_sign = _sign(z_spot)
    fut_sign = _sign(z_fut)
    abs_spot = abs(z_spot)
    abs_doi = abs(z_doi) if z_doi is not None else 0.0

    if spot_sign != 0 and spot_sign == fut_sign and abs_spot >= params.spot_sync_min_z:
        return SPOT_STATUS_SYNC

    divergence = (
        spot_sign != 0
        and fut_sign != 0
        and spot_sign != fut_sign
        and abs_spot >= params.spot_div_min_z
    )
    dead_zone = (
        abs_spot < params.spot_dead_max_z
        and z_doi is not None
        and abs_doi >= params.spot_dead_extreme_oi_z
    )
    if divergence or dead_zone:
        return SPOT_STATUS_DIV

    return SPOT_STATUS_NEUTRAL


def analyze_frame(
    aligned: AlignedRegimeData,
    params: RegimeParams,
    symbol: str,
    *,
    fetched_at_ms: int,
    step_ms: int,
) -> RegimeBundle:
    """Считает Z-score, режимы, теги и собирает видимый bundle."""
    frame = aligned.frame
    if frame is None or frame.empty:
        raise RuntimeError("Нет данных для расчёта OI & Delta Regime")

    z_doi, warm_doi = _rolling_robust_z(frame["delta_oi_total"], params.z_window)
    z_fut, warm_fut = _rolling_robust_z(frame["fut_delta"], params.z_window)
    z_spot, _warm_spot = _rolling_robust_z(frame["spot_delta"], params.z_window)

    bars_full: list[CandleBar] = []
    for ts, row in frame.iterrows():
        open_time_ms = int(ts)
        z_doi_val = _opt_float(z_doi.loc[ts])
        z_fut_val = _opt_float(z_fut.loc[ts])
        z_spot_val = _opt_float(z_spot.loc[ts])
        delta_total = _opt_float(row.get("delta_oi_total"))
        delta_bin = _opt_float(row.get("delta_oi_binance"))
        delta_by = _opt_float(row.get("delta_oi_bybit"))
        spot_delta = _opt_float(row.get("spot_delta"))
        spot_available = bool(row.get("spot_available", False)) and spot_delta is not None
        is_warmup = bool(warm_doi.loc[ts] or warm_fut.loc[ts])

        is_anomaly = (
            not is_warmup
            and z_doi_val is not None
            and z_fut_val is not None
            and abs(z_doi_val) >= params.threshold_oi
            and abs(z_fut_val) >= params.threshold_delta
        )

        regime_key: str | None = None
        regime_label: str | None = None
        marker: str | None = None
        color: str | None = None
        if is_anomaly and z_doi_val is not None and z_fut_val is not None:
            if z_fut_val > 0 and z_doi_val > 0:
                regime_key = "LONG_BUILD"
            elif z_fut_val < 0 and z_doi_val < 0:
                regime_key = "LONG_CLOSE"
            elif z_fut_val > 0 and z_doi_val < 0:
                regime_key = "SHORT_CLOSE"
            elif z_fut_val < 0 and z_doi_val > 0:
                regime_key = "SHORT_BUILD"
            if regime_key is not None:
                regime_label, marker, color = REGIME_META[regime_key]

        bar_open = float(row["open"])
        bar_high = float(row["high"])
        bar_low = float(row["low"])
        bar_close = float(row["close"])
        is_absorption, absorption_side, absorption_reason = _absorption(
            bar_open, bar_close, bar_high, bar_low, z_fut_val, params
        )
        spot_status = _spot_status(
            z_spot_val, z_fut_val, z_doi_val, spot_available, params
        )

        bars_full.append(
            CandleBar(
                open_time_ms=open_time_ms,
                close_time_ms=int(row.get("close_time_ms", open_time_ms + step_ms - 1)),
                open=bar_open,
                high=bar_high,
                low=bar_low,
                close=bar_close,
                volume_base=float(row["volume"]),
                taker_buy_base=float(row["taker_buy"]),
                fut_delta=float(row["fut_delta"]),
                spot_available=spot_available,
                spot_delta=spot_delta,
                oi_binance_start=_opt_float(row.get("oi_binance_start")),
                oi_binance_end=_opt_float(row.get("oi_binance_end")),
                oi_bybit_start=_opt_float(row.get("oi_bybit_start")),
                oi_bybit_end=_opt_float(row.get("oi_bybit_end")),
                delta_oi_binance=delta_bin,
                delta_oi_bybit=delta_by,
                delta_oi_total=delta_total,
                is_live=bool(row.get("is_live", False)),
                live_start_estimated=bool(row.get("live_start_estimated", False)),
                is_warmup=is_warmup,
                has_oi=delta_total is not None,
                z_doi=z_doi_val,
                z_fut_delta=z_fut_val,
                z_spot_delta=z_spot_val,
                regime_key=regime_key,
                regime_label=regime_label,
                marker=marker,
                color=color,
                is_anomaly=bool(is_anomaly and regime_key is not None),
                is_absorption=is_absorption,
                absorption_side=absorption_side,
                absorption_reason=absorption_reason,
                spot_status=spot_status,
            )
        )


    visible = bars_full[-max(1, int(params.visible_bars)) :]
    events: list[AnomalyEvent] = []
    for index, bar in enumerate(visible):
        if not bar.is_anomaly or bar.regime_key is None:
            continue
        events.append(
            AnomalyEvent(
                bar_index=index,
                open_time_ms=bar.open_time_ms,
                timeframe=params.timeframe,
                regime_key=bar.regime_key,
                regime_label=bar.regime_label or "",
                marker=bar.marker or "",
                color=bar.color or "#64748B",
                z_doi=bar.z_doi or 0.0,
                z_fut_delta=bar.z_fut_delta or 0.0,
                z_spot_delta=bar.z_spot_delta,
                fut_delta=bar.fut_delta,
                spot_delta=bar.spot_delta,
                delta_oi_total=bar.delta_oi_total,
                delta_oi_binance=bar.delta_oi_binance,
                delta_oi_bybit=bar.delta_oi_bybit,
                spot_status=bar.spot_status,
                is_absorption=bar.is_absorption,
                absorption_reason=bar.absorption_reason,
                is_live=bar.is_live,
            )
        )
    events.reverse()

    display_time = datetime.fromtimestamp(
        max(0, int(fetched_at_ms)) / 1000.0, tz=timezone.utc
    ) + timedelta(hours=int(params.tz_offset_hours))
    live_open = next((bar.open_time_ms for bar in visible if bar.is_live), None)

    return RegimeBundle(
        symbol=str(symbol).strip().upper(),
        base_asset=base_asset(symbol),
        timeframe=params.timeframe,
        params=params,
        bars=visible,
        events=events,
        anomaly_count=len(events),
        warmup_rows=sum(1 for bar in visible if bar.is_warmup),
        spot_available=bool(aligned.spot_available)
        and any(bar.spot_delta is not None for bar in visible),
        bybit_available=bool(aligned.bybit_valid),
        sources_label="Binance + Bybit" if aligned.bybit_valid else "Binance only",
        warnings=list(aligned.warnings),
        updated_at=display_time.strftime("%H:%M:%S"),
        updated_at_ms=int(fetched_at_ms),
        step_ms=int(step_ms),
        live_open_time_ms=live_open,
    )


def build_bundle(
    raw: RawRegimeData,
    params: RegimeParams,
    symbol: str,
    now_ms: int | None = None,
) -> RegimeBundle:
    """Полный конвейер: выравнивание сырых данных + расчёт матрицы."""
    aligned = align_raw(raw, params, now_ms=now_ms)
    return analyze_frame(
        aligned,
        params,
        symbol,
        fetched_at_ms=raw.fetched_at_ms,
        step_ms=raw.step_ms,
    )

