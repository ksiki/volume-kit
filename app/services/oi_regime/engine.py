"""Математическое ядро OI & Delta Regime: robust Z, режимы и контекстные теги."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.services.oi_regime.fetcher import (
    EXCHANGE_LABELS,
    AlignedRegimeData,
    RawRegimeData,
    align_raw,
    base_asset,
)
from app.services.oi_regime.models import (
    REGIME_META,
    SPOT_STATUS_DIV,
    SPOT_STATUS_NEUTRAL,
    SPOT_STATUS_PASSIVE,
    SPOT_STATUS_SYNC,
    SPOT_STATUS_UNAVAILABLE,
    AnomalyEvent,
    CandleBar,
    MultiBarZone,
    OutcomeHorizonRow,
    OutcomeStats,
    RegimeBundle,
    RegimeOutcomeRow,
    RegimeParams,
    TrapLevel,
)

log = logging.getLogger(__name__)

_EPS = 1e-12
_MAD_TO_SIGMA = 0.6745
_MEANAD_TO_SIGMA = 1.253314

_OUTCOME_HORIZONS = (3, 6, 12)
_OUTCOME_GROUP_ORDER = (
    "LONG_BUILD",
    "LONG_CLOSE",
    "SHORT_BUILD",
    "SHORT_CLOSE",
    "ABSORPTION",
)
_OUTCOME_LABELS = {
    "LONG_BUILD": "Покупатель набирает позицию",
    "LONG_CLOSE": "Покупатель закрывает позицию",
    "SHORT_BUILD": "Продавец набирает позицию",
    "SHORT_CLOSE": "Продавец закрывает позицию",
    "ABSORPTION": "С поглощением",
}

_MAX_TRAP_LEVELS = 8


# ==================== ROBUST Z ====================


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


def _rolling_robust_z(
    series: pd.Series, window: int, shift: int = 1
) -> tuple[pd.Series, pd.Series]:
    """Robust Z по предшествующим N значениям с настраиваемым сдвигом базы."""
    window = max(1, int(window))
    shift = max(1, int(shift))
    values = pd.to_numeric(series, errors="coerce").astype(np.float64)
    prev = values.shift(shift)

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


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _numeric_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=np.float64)
    return pd.to_numeric(frame[name], errors="coerce").astype(np.float64)


# ==================== ПРЕДРАСЧЁТЫ ====================


def _oi_total_start(frame: pd.DataFrame, active_exchanges: list[str]) -> pd.Series:
    """Суммарный стартовый OI бирж, участвующих в ΔOI на каждом баре.

    Набор бирж определяется ровно непустыми ``delta_oi_<ex>``: это гарантирует,
    что числитель (``delta_oi_total``) и знаменатель (стартовый OI) на каждом баре
    собраны по одному и тому же набору валидных бирж.
    """
    bin_start = _numeric_column(frame, "oi_binance_start")
    bin_delta = _numeric_column(frame, "delta_oi_binance")
    total = bin_start.copy()
    for ex in active_exchanges:
        if ex == "binance":
            continue
        ex_start = _numeric_column(frame, f"oi_{ex}_start")
        ex_delta = _numeric_column(frame, f"delta_oi_{ex}")
        contribution = ex_start.where(ex_delta.notna(), 0.0).fillna(0.0)
        total = total.add(contribution, fill_value=0.0)
    return total.where(bin_delta.notna())


def _delta_oi_pct_series(frame: pd.DataFrame, active_exchanges: list[str]) -> pd.Series:
    """Относительный прирост OI в % к стартовому значению активных бирж."""
    delta_total = _numeric_column(frame, "delta_oi_total")
    total_start = _oi_total_start(frame, active_exchanges)
    result = pd.Series(np.nan, index=frame.index, dtype=np.float64)
    valid = delta_total.notna() & total_start.notna() & (total_start > _EPS)
    result.loc[valid] = delta_total.loc[valid] / total_start.loc[valid] * 100.0
    return result


def _atr14_context(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Wilder ATR(14) строго по закрытым свечам и ATR предыдущей закрытой свечи.

    Незакрытая LIVE-свеча не участвует в расчёте и не «дёргает» сглаживание.
    Возвращает две серии на индексе кадра: ATR текущей закрытой свечи и ATR
    предыдущей закрытой свечи (для отношения размаха бара к предыдущему ATR).
    """
    empty = pd.Series(np.nan, index=frame.index, dtype=np.float64)
    if frame is None or frame.empty:
        return empty, empty.copy()

    if "is_live" in frame:
        live_mask = frame["is_live"].astype(bool)
    else:
        live_mask = pd.Series(False, index=frame.index, dtype=bool)
    closed = frame.loc[~live_mask]
    if closed.empty:
        return empty, empty.copy()

    high = _numeric_column(closed, "high")
    low = _numeric_column(closed, "low")
    close = _numeric_column(closed, "close")
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    atr = tr.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    prev_atr = atr.shift(1)

    atr_full = pd.Series(np.nan, index=frame.index, dtype=np.float64)
    prev_full = pd.Series(np.nan, index=frame.index, dtype=np.float64)
    atr_full.loc[closed.index] = atr.to_numpy(dtype=np.float64)
    prev_full.loc[closed.index] = prev_atr.to_numpy(dtype=np.float64)
    return atr_full, prev_full



def _range_ratio_series(frame: pd.DataFrame, window: int) -> pd.Series:
    """Отношение диапазона свечи к медиане диапазонов предыдущих N свечей."""
    bar_range = _numeric_column(frame, "high") - _numeric_column(frame, "low")
    bar_range = bar_range.where(bar_range >= 0)
    lookback = max(3, int(window))
    median_prev = bar_range.shift(1).rolling(
        lookback, min_periods=max(3, min(lookback, 5))
    ).median()
    return bar_range / median_prev.where(median_prev > _EPS)


def _multi_bar_series(
    frame: pd.DataFrame, delta_oi_pct: pd.Series, params: RegimeParams
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Скользящие суммы k баров и их Z-score со сдвигом базы на k (без самозагрязнения)."""
    k = _multi_window(params)
    fut_delta = _numeric_column(frame, "fut_delta")
    multi_doi = delta_oi_pct.rolling(k, min_periods=k).sum()
    multi_delta = fut_delta.rolling(k, min_periods=k).sum()
    z_multi_doi, _ = _rolling_robust_z(multi_doi, params.z_window, shift=k)
    z_multi_delta, _ = _rolling_robust_z(multi_delta, params.z_window, shift=k)
    return multi_doi, multi_delta, z_multi_doi, z_multi_delta


def _multi_window(params: RegimeParams) -> int:
    """Размер окна мульти-бар режима (защищён от некорректных значений)."""
    return max(3, min(6, int(params.multi_bar_window)))


def _multi_bar_persistence(
    frame: pd.DataFrame,
    multi_doi: pd.Series,
    multi_delta: pd.Series,
    params: RegimeParams,
) -> pd.Series:
    """Persistence-фильтр: реальное накопление/разгрузка вместо эха одиночной свечи."""
    k = _multi_window(params)
    result = pd.Series(False, index=frame.index, dtype=bool)
    n = len(frame)
    if n < k:
        return result

    doi = _numeric_column(frame, "delta_oi_total").to_numpy(dtype=np.float64)
    fut = _numeric_column(frame, "fut_delta").to_numpy(dtype=np.float64)
    sum_doi = multi_doi.to_numpy(dtype=np.float64)
    sum_delta = multi_delta.to_numpy(dtype=np.float64)
    min_matches = max(2, k // 2)

    for i in range(k - 1, n):
        doi_total = sum_doi[i]
        delta_total = sum_delta[i]
        if not np.isfinite(doi_total) or not np.isfinite(delta_total):
            continue
        doi_sign = _sign(float(doi_total))
        delta_sign = _sign(float(delta_total))
        if doi_sign == 0 or delta_sign == 0:
            continue

        start = i - k + 1
        window_doi = doi[start : i + 1]
        window_fut = fut[start : i + 1]
        if not np.all(np.isfinite(window_doi)) or not np.all(np.isfinite(window_fut)):
            continue

        doi_matches = int(np.count_nonzero(np.sign(window_doi) == doi_sign))
        delta_matches = int(np.count_nonzero(np.sign(window_fut) == delta_sign))
        if doi_matches < min_matches or delta_matches < min_matches:
            continue

        abs_doi = np.abs(window_doi)
        abs_delta = np.abs(window_fut)
        sum_abs_doi = float(abs_doi.sum())
        sum_abs_delta = float(abs_delta.sum())
        dominant_doi = sum_abs_doi > _EPS and float(abs_doi.max()) > 0.8 * sum_abs_doi
        dominant_delta = (
            sum_abs_delta > _EPS and float(abs_delta.max()) > 0.8 * sum_abs_delta
        )
        if dominant_doi and dominant_delta:
            continue

        result.iloc[i] = True
    return result


def _multi_bar_zones(
    bars: list[CandleBar], visible_offset: int, params: RegimeParams
) -> list[MultiBarZone]:
    """Склеивает пересекающиеся окна одного режима в непрерывные зоны."""
    k = _multi_window(params)
    runs: list[list[object]] = []
    for full_index, bar in enumerate(bars):
        key = bar.multi_regime_key
        if key is None:
            continue
        window_start = max(0, full_index - k + 1)
        window_end = full_index
        if runs and runs[-1][0] == key and window_start <= int(runs[-1][2]) + 1:
            runs[-1][2] = window_end
        else:
            runs.append([key, window_start, window_end])

    zones: list[MultiBarZone] = []
    first_visible = max(0, int(visible_offset))
    last_visible = len(bars) - 1
    for key_obj, start_obj, end_obj in runs:
        key = str(key_obj)
        start = max(int(start_obj), first_visible)
        end = min(int(end_obj), last_visible)
        if start > end:
            continue
        segment = bars[start : end + 1]
        if not segment:
            continue

        doi_values = [bar.delta_oi_total for bar in segment if bar.delta_oi_total is not None]
        pct_values = [bar.delta_oi_pct for bar in segment if bar.delta_oi_pct is not None]
        z_doi_values = [bar.z_doi for bar in segment if bar.z_doi is not None]
        z_delta_values = [
            bar.z_fut_delta for bar in segment if bar.z_fut_delta is not None
        ]
        peak_z_doi = max(z_doi_values, key=abs) if z_doi_values else 0.0
        peak_z_delta = max(z_delta_values, key=abs) if z_delta_values else 0.0
        label, _marker, color = REGIME_META.get(key, ("", "", "#64748B"))

        zones.append(
            MultiBarZone(
                start_index=start - first_visible,
                end_index=end - first_visible,
                start_time_ms=segment[0].open_time_ms,
                end_time_ms=segment[-1].close_time_ms,
                regime_key=key,
                regime_label=label,
                color=color,
                bar_count=len(segment),
                sum_delta_oi=float(sum(doi_values)) if doi_values else 0.0,
                sum_delta_oi_pct=float(sum(pct_values)) if pct_values else None,
                sum_fut_delta=float(sum(bar.fut_delta for bar in segment)),
                peak_z_doi=float(peak_z_doi),
                peak_z_delta=float(peak_z_delta),
                price_low=float(min(bar.low for bar in segment)),
                price_high=float(max(bar.high for bar in segment)),
            )
        )
    return zones



# ==================== ТЕГИ ====================


def _absorption(
    bar_open: float,
    bar_close: float,
    bar_high: float,
    bar_low: float,
    z_fut: float | None,
    range_ratio: float | None,
    regime_key: str | None,
    params: RegimeParams,
) -> tuple[bool, str | None, str | None]:
    """Поглощение: свечной отбой против дельты либо «усилие без результата»."""
    if z_fut is None:
        return False, None, None
    bar_range = bar_high - bar_low
    close_pos = (bar_close - bar_low) / bar_range if bar_range > 0 else 0.5
    effort = (
        range_ratio is not None
        and np.isfinite(range_ratio)
        and range_ratio <= params.absorption_effort_range_ratio
    )

    if z_fut >= params.absorption_min_z_delta:
        reasons: list[str] = []
        if bar_close <= bar_open:
            reasons.append("медвежья свеча на позитивной дельте")
        if close_pos < params.absorption_close_ratio:
            reasons.append("закрытие в нижней трети")
        if effort:
            reasons.append("высокая дельта при сжатом диапазоне")
        if reasons:
            if regime_key == "SHORT_CLOSE":
                reasons.insert(0, "лимитное сопротивление при закрытии шортов")
            return True, "LONG_TRAP", "; ".join(reasons)

    if z_fut <= -params.absorption_min_z_delta:
        reasons = []
        if bar_close >= bar_open:
            reasons.append("бычья свеча на негативной дельте")
        if close_pos > (1.0 - params.absorption_close_ratio):
            reasons.append("закрытие в верхней трети")
        if effort:
            reasons.append("высокая дельта при сжатом диапазоне")
        if reasons:
            if regime_key == "LONG_CLOSE":
                reasons.insert(0, "лимитная поддержка при закрытии лонгов")
            return True, "SHORT_TRAP", "; ".join(reasons)

    return False, None, None



def _spot_status(
    z_spot: float | None,
    z_fut: float | None,
    z_doi: float | None,
    spot_available: bool,
    params: RegimeParams,
) -> str:
    """Статус спота: подтверждение, дивергенция, пассивный спот или нейтрально."""
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
    if divergence:
        return SPOT_STATUS_DIV

    passive = (
        abs_spot < params.spot_dead_max_z
        and z_doi is not None
        and abs_doi >= params.spot_dead_extreme_oi_z
    )
    if passive:
        return SPOT_STATUS_PASSIVE

    return SPOT_STATUS_NEUTRAL


# ==================== УРОВНИ И СТАТИСТИКА ====================


def _trap_levels(bars: list[CandleBar], params: RegimeParams) -> list[TrapLevel]:
    """Строит уровни «застрявших» участников и отбрасывает уже снятые рынком."""
    levels: list[TrapLevel] = []
    if not bars:
        return levels
    closed_last = len(bars) - 1
    while closed_last >= 0 and bars[closed_last].is_live:
        closed_last -= 1
    if closed_last < 0:
        return levels

    for index, bar in enumerate(bars):
        if bar.is_live or bar.is_warmup:
            continue
        strength = max(abs(bar.z_doi or 0.0), abs(bar.z_fut_delta or 0.0))
        side: str | None = None
        if (
            bar.regime_key in ("LONG_BUILD", "SHORT_BUILD")
            and strength >= params.trap_level_min_z
        ):
            side = "SUPPORT" if bar.regime_key == "LONG_BUILD" else "RESISTANCE"
        if side is None:
            strong_absorption = bar.is_absorption and (
                bar.is_anomaly or strength >= params.trap_level_min_z
            )
            if not strong_absorption:
                continue
            if bar.absorption_side == "LONG_TRAP":
                side = "RESISTANCE"
            elif bar.absorption_side == "SHORT_TRAP":
                side = "SUPPORT"
            else:
                continue

        price_low = min(bar.open, bar.close)
        price_high = max(bar.open, bar.close)
        if price_high - price_low <= _EPS:
            price_low, price_high = bar.low, bar.high
        regime_key = bar.regime_key or (
            "ABSORPTION_RESISTANCE" if side == "RESISTANCE" else "ABSORPTION_SUPPORT"
        )

        mitigated = False
        end_index = closed_last
        for follow in range(index + 1, closed_last + 1):
            candidate = bars[follow]
            if side == "SUPPORT" and candidate.close < bar.low:
                mitigated, end_index = True, follow
                break
            if side == "RESISTANCE" and candidate.close > bar.high:
                mitigated, end_index = True, follow
                break
        if mitigated:
            continue

        levels.append(
            TrapLevel(
                start_index=index,
                end_index=end_index,
                open_time_ms=bar.open_time_ms,
                price_low=float(price_low),
                price_high=float(price_high),
                price_mid=float((bar.high + bar.low) / 2.0),
                side=side,
                regime_key=regime_key,
                strength=float(strength),
                is_mitigated=False,
            )
        )
    return levels[-_MAX_TRAP_LEVELS:]


def _median_or_none(values: list[float]) -> float | None:
    clean = [float(v) for v in values if np.isfinite(float(v))]
    if not clean:
        return None
    return float(np.median(clean))


def _outcome_stats(bars: list[CandleBar]) -> OutcomeStats:
    """Медианная отработка событий по горизонтам без заглядывания в будущее."""
    groups: dict[str, list[int]] = {}
    for index, bar in enumerate(bars):
        if bar.is_live or bar.is_warmup:
            continue
        if bar.is_anomaly and bar.regime_key:
            groups.setdefault(bar.regime_key, []).append(index)
        if bar.is_absorption:
            groups.setdefault("ABSORPTION", []).append(index)

    rows: list[RegimeOutcomeRow] = []
    for key in _OUTCOME_GROUP_ORDER:
        positions = groups.get(key, [])
        if not positions:
            continue
        horizons: list[OutcomeHorizonRow] = []
        for horizon in _OUTCOME_HORIZONS:
            mfe: list[float] = []
            mae: list[float] = []
            closes: list[float] = []
            for pos in positions:
                if pos + horizon >= len(bars):
                    continue
                window = bars[pos + 1 : pos + horizon + 1]
                if len(window) < horizon or any(item.is_live for item in window):
                    continue
                base_close = bars[pos].close
                if base_close <= 0:
                    continue
                mfe.append((max(item.high for item in window) - base_close) / base_close * 100.0)
                mae.append((min(item.low for item in window) - base_close) / base_close * 100.0)
                closes.append((window[-1].close - base_close) / base_close * 100.0)
            horizons.append(
                OutcomeHorizonRow(
                    horizon=horizon,
                    n=len(mfe),
                    mfe_pct=_median_or_none(mfe),
                    mae_pct=_median_or_none(mae),
                    close_pct=_median_or_none(closes),
                )
            )
        rows.append(
            RegimeOutcomeRow(
                regime_key=key,
                label=_OUTCOME_LABELS.get(key, key),
                count=len(positions),
                horizons=horizons,
            )
        )
    return OutcomeStats(rows=rows, generated_bars=len(bars))



# ==================== ОСНОВНОЙ РАСЧЁТ ====================


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

    active_exchanges: list[str] = list(aligned.active_exchanges or ["binance"])
    z_window = max(1, int(params.z_window))
    delta_oi_total_s = _numeric_column(frame, "delta_oi_total")
    fut_delta_s = _numeric_column(frame, "fut_delta")
    delta_oi_pct_s = _delta_oi_pct_series(frame, active_exchanges)
    atr_s, atr_prev_s = _atr14_context(frame)

    # Сила — по %ΔOI; если стартовый OI неизвестен, используем абсолютный ΔOI.
    z_pct, warm_pct = _rolling_robust_z(delta_oi_pct_s, z_window)
    z_abs, warm_abs = _rolling_robust_z(delta_oi_total_s, z_window)
    z_doi = z_pct.where(delta_oi_pct_s.notna(), z_abs)
    warm_doi = warm_pct.where(delta_oi_pct_s.notna(), warm_abs).astype(bool)
    z_fut, warm_fut = _rolling_robust_z(fut_delta_s, z_window)
    z_spot, _warm_spot = _rolling_robust_z(_numeric_column(frame, "spot_delta"), z_window)
    range_ratio_s = _range_ratio_series(frame, z_window)
    multi_doi_s, multi_delta_s, z_multi_doi_s, z_multi_delta_s = _multi_bar_series(
        frame, delta_oi_pct_s, params
    )
    multi_persist_s = _multi_bar_persistence(frame, multi_doi_s, multi_delta_s, params)

    bars_full: list[CandleBar] = []
    last_closed_atr: float | None = None
    for ts, row in frame.iterrows():
        open_time_ms = int(ts)
        z_doi_val = _opt_float(z_doi.loc[ts])
        z_fut_val = _opt_float(z_fut.loc[ts])
        z_spot_val = _opt_float(z_spot.loc[ts])
        delta_total = _opt_float(row.get("delta_oi_total"))
        delta_by_exchange: dict[str, float] = {}
        oi_start_by_exchange: dict[str, float] = {}
        oi_end_by_exchange: dict[str, float] = {}
        for ex in active_exchanges:
            ex_delta = _opt_float(row.get(f"delta_oi_{ex}"))
            if ex_delta is not None:
                delta_by_exchange[ex] = ex_delta
            ex_start = _opt_float(row.get(f"oi_{ex}_start"))
            if ex_start is not None:
                oi_start_by_exchange[ex] = ex_start
            ex_end = _opt_float(row.get(f"oi_{ex}_end"))
            if ex_end is not None:
                oi_end_by_exchange[ex] = ex_end
        delta_pct = _opt_float(row.get("delta_oi_pct")) if "delta_oi_pct" in row else None
        if delta_pct is None:
            delta_pct = _opt_float(delta_oi_pct_s.loc[ts])
        spot_delta = _opt_float(row.get("spot_delta"))
        spot_available = bool(row.get("spot_available", False)) and spot_delta is not None
        is_live = bool(row.get("is_live", False))
        is_warmup = bool(warm_doi.loc[ts] or warm_fut.loc[ts])
        fut_delta = _opt_float(row.get("fut_delta"))
        range_ratio = _opt_float(range_ratio_s.loc[ts])

        regime_key: str | None = None
        regime_label: str | None = None
        marker: str | None = None
        color: str | None = None
        strength_ok = (
            not is_warmup
            and z_doi_val is not None
            and z_fut_val is not None
            and abs(z_doi_val) >= params.threshold_oi
            and abs(z_fut_val) >= params.threshold_delta
        )
        # Квадрант режима определяется фактическими знаками ΔOI и дельты,
        # а не знаками Z-score (Z может быть отрицательным при слабом росте).
        doi_sign = _sign(delta_total)
        fut_sign = _sign(fut_delta)
        z_doi_sign = _sign(z_doi_val)
        z_fut_sign = _sign(z_fut_val)
        signs_match = (
            doi_sign != 0
            and fut_sign != 0
            and doi_sign == z_doi_sign
            and fut_sign == z_fut_sign
        )
        is_anomaly = bool(strength_ok and signs_match)
        if is_anomaly:
            if fut_sign > 0 and doi_sign > 0:
                regime_key = "LONG_BUILD"
            elif fut_sign < 0 and doi_sign < 0:
                regime_key = "LONG_CLOSE"
            elif fut_sign > 0 and doi_sign < 0:
                regime_key = "SHORT_CLOSE"
            elif fut_sign < 0 and doi_sign > 0:
                regime_key = "SHORT_BUILD"
            if regime_key is not None:
                regime_label, marker, color = REGIME_META[regime_key]

        bar_open = float(row["open"])
        bar_high = float(row["high"])
        bar_low = float(row["low"])
        bar_close = float(row["close"])
        is_absorption, absorption_side, absorption_reason = _absorption(
            bar_open,
            bar_close,
            bar_high,
            bar_low,
            z_fut_val,
            range_ratio,
            regime_key,
            params,
        )
        spot_status = _spot_status(
            z_spot_val, z_fut_val, z_doi_val, spot_available, params
        )

        # ---- Доминирование одной биржи в ΔOI ----
        dominant_exchange: str | None = None
        dominant_share: float | None = None
        is_single_exchange_led = False
        if len(delta_by_exchange) >= 2:
            abs_values = {ex: abs(value) for ex, value in delta_by_exchange.items()}
            total_abs = float(sum(abs_values.values()))
            if total_abs > _EPS:
                dominant_exchange = max(abs_values, key=abs_values.__getitem__)
                dominant_share = abs_values[dominant_exchange] / total_abs
                threshold = 0.70 if len(delta_by_exchange) >= 3 else 0.75
                is_single_exchange_led = bool(
                    dominant_share >= threshold and abs_values[dominant_exchange] > _EPS
                )

        # ---- Wilder ATR(14): по закрытым свечам, LIVE наследует последний ATR ----
        bar_range = max(0.0, bar_high - bar_low)
        atr14_val: float | None
        range_atr_val: float | None = None
        if is_live:
            atr14_val = last_closed_atr
            if atr14_val is not None and atr14_val > _EPS:
                range_atr_val = bar_range / atr14_val
        else:
            atr14_val = _opt_float(atr_s.loc[ts])
            prev_atr_val = _opt_float(atr_prev_s.loc[ts])
            if prev_atr_val is not None and prev_atr_val > _EPS:
                range_atr_val = bar_range / prev_atr_val
            if atr14_val is not None:
                last_closed_atr = atr14_val
        atr14_pct_val = (
            atr14_val / bar_close * 100.0
            if atr14_val is not None and bar_close > _EPS
            else None
        )

        live_progress = 1.0
        if is_live:
            live_progress = _clamp01(
                (int(fetched_at_ms) - open_time_ms) / max(1, step_ms)
            )

        multi_doi_val = _opt_float(multi_doi_s.loc[ts])
        multi_delta_val = _opt_float(multi_delta_s.loc[ts])
        z_multi_doi_val = _opt_float(z_multi_doi_s.loc[ts])
        z_multi_delta_val = _opt_float(z_multi_delta_s.loc[ts])
        multi_regime_key: str | None = None
        if (
            multi_doi_val is not None
            and multi_delta_val is not None
            and z_multi_doi_val is not None
            and z_multi_delta_val is not None
            and abs(z_multi_doi_val) >= params.multi_bar_threshold_oi
            and abs(z_multi_delta_val) >= params.multi_bar_threshold_delta
            and _sign(multi_doi_val) == _sign(z_multi_doi_val) != 0
            and _sign(multi_delta_val) == _sign(z_multi_delta_val) != 0
            and bool(multi_persist_s.loc[ts])
        ):
            if multi_delta_val > 0 and multi_doi_val > 0:
                multi_regime_key = "LONG_BUILD"
            elif multi_delta_val < 0 and multi_doi_val < 0:
                multi_regime_key = "LONG_CLOSE"
            elif multi_delta_val > 0 and multi_doi_val < 0:
                multi_regime_key = "SHORT_CLOSE"
            elif multi_delta_val < 0 and multi_doi_val > 0:
                multi_regime_key = "SHORT_BUILD"

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
                fut_delta=float(fut_delta or 0.0),
                spot_available=spot_available,
                spot_delta=spot_delta,
                delta_oi_total=delta_total,
                is_live=is_live,
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
                delta_oi_pct=delta_pct,
                delta_oi_by_exchange=delta_by_exchange,
                oi_start_by_exchange=oi_start_by_exchange,
                oi_end_by_exchange=oi_end_by_exchange,
                dominant_exchange=dominant_exchange,
                dominant_share=dominant_share,
                is_single_exchange_led=is_single_exchange_led,
                live_progress=live_progress,
                range_ratio=range_ratio,
                is_standalone_absorption=bool(
                    is_absorption and not is_anomaly and not is_warmup
                ),
                basis_pct=_opt_float(row.get("basis_pct")),
                funding_rate=_opt_float(row.get("funding_rate")),
                multi_regime_key=multi_regime_key,
                z_multi_doi=z_multi_doi_val,
                z_multi_delta=z_multi_delta_val,
                atr14=atr14_val,
                atr14_pct=atr14_pct_val,
                range_atr=range_atr_val,
            )
        )

    visible = bars_full[-max(1, int(params.visible_bars)) :]
    multi_zones = _multi_bar_zones(
        bars_full, len(bars_full) - len(visible), params
    )
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
                spot_status=bar.spot_status,
                is_absorption=bar.is_absorption,
                absorption_reason=bar.absorption_reason,
                is_live=bar.is_live,
                delta_oi_pct=bar.delta_oi_pct,
                delta_oi_by_exchange=dict(bar.delta_oi_by_exchange),
                dominant_exchange=bar.dominant_exchange,
                dominant_share=bar.dominant_share,
                is_single_exchange_led=bar.is_single_exchange_led,
                live_progress=bar.live_progress,
                basis_pct=bar.basis_pct,
                funding_rate=bar.funding_rate,
                is_standalone_absorption=bar.is_standalone_absorption,
                atr14=bar.atr14,
                range_atr=bar.range_atr,
            )
        )
    events.reverse()

    display_time = datetime.fromtimestamp(
        max(0, int(fetched_at_ms)) / 1000.0, tz=timezone.utc
    ) + timedelta(hours=int(params.tz_offset_hours))
    live_open = next((bar.open_time_ms for bar in visible if bar.is_live), None)
    oi_unavailable_before: int | None = None
    for index, bar in enumerate(visible):
        if bar.has_oi:
            if index > 0:
                oi_unavailable_before = index
            break

    current_atr14: float | None = None
    current_atr14_pct: float | None = None
    for bar in reversed(visible):
        if not bar.is_live and bar.atr14 is not None:
            current_atr14 = bar.atr14
            current_atr14_pct = bar.atr14_pct
            break

    secondary_active = [ex for ex in active_exchanges if ex != "binance"]
    if secondary_active:
        sources_label = "Binance + " + " + ".join(
            EXCHANGE_LABELS.get(ex, ex) for ex in secondary_active
        )
    else:
        sources_label = "Binance only"

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
        active_exchanges=active_exchanges,
        sources_label=sources_label,
        current_atr14=current_atr14,
        current_atr14_pct=current_atr14_pct,
        warnings=list(aligned.warnings),
        updated_at=display_time.strftime("%H:%M:%S"),
        updated_at_ms=int(fetched_at_ms),
        step_ms=int(step_ms),
        live_open_time_ms=live_open,
        trap_levels=_trap_levels(visible, params),
        multi_zones=multi_zones,
        outcome_stats=_outcome_stats(visible),
        oi_unavailable_before_index=oi_unavailable_before,
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

