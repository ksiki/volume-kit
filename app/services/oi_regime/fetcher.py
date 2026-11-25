"""REST-клиенты Binance/Bybit и выравнивание OI по свечам (без Qt)."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
import requests

from app.services.oi_regime.models import RegimeParams

log = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"
BYBIT_API = "https://api.bybit.com"

TIMEFRAMES: dict[str, dict[str, object]] = {
    "5m": {"step_ms": 300_000, "bybit": "5min"},
    "15m": {"step_ms": 900_000, "bybit": "15min"},
    "1h": {"step_ms": 3_600_000, "bybit": "1h"},
    "4h": {"step_ms": 14_400_000, "bybit": "4h"},
}

BINANCE_OI_LIMIT = 500
BYBIT_OI_LIMIT = 200
BYBIT_MAX_PAGES = 30
BINANCE_OI_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000
BYBIT_RECOVERY_COOLDOWN_MS = 5 * 60 * 1000

_STAGE_CB = Callable[[str], None] | None
_CANCEL_CB = Callable[[], bool] | None

_QUOTE_ASSETS = ("USDT", "USDC", "FDUSD", "TUSD", "BUSD", "BTC", "ETH", "EUR", "USD")

_SPOT_NUMERIC_PREFIXES = ("1000000", "1000")


class RegimeFetchError(RuntimeError):
    """Ошибка получения обязательных рыночных данных."""


class RegimeFetchCancelled(RegimeFetchError):
    """Загрузка прервана по запросу вызывающей стороны."""


def base_asset(symbol: str) -> str:
    """Возвращает базовый актив тикера (BTC для BTCUSDT)."""
    clean = str(symbol).strip().upper()
    for quote in _QUOTE_ASSETS:
        if clean.endswith(quote) and len(clean) > len(quote):
            return clean[: -len(quote)]
    return clean


def spot_symbol_candidates(symbol: str) -> list[str]:
    """Варианты спотового тикера: сам тикер и версия без числового префикса 1000/1000000."""
    clean = str(symbol).strip().upper()
    candidates = [clean]
    for prefix in _SPOT_NUMERIC_PREFIXES:
        if clean.startswith(prefix) and len(clean) > len(prefix):
            stripped = clean[len(prefix) :]
            if stripped and stripped not in candidates:
                candidates.append(stripped)
            break
    return candidates


def timeframe_config(timeframe: str) -> dict[str, object]:
    """Возвращает конфигурацию ТФ; при неизвестном значении — fallback 1h."""
    clean = str(timeframe).strip().lower()
    if clean in TIMEFRAMES:
        return TIMEFRAMES[clean]
    log.warning(
        "Неизвестный таймфрейм OI Regime %r — используется 1h",
        timeframe,
        extra={"tool": "OI Regime"},
    )
    return TIMEFRAMES["1h"]



def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite(value: object) -> bool:
    try:
        return bool(np.isfinite(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _add_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


@dataclass
class RawRegimeData:
    """Сырые данные бирж, пригодные для повторного расчёта из памяти."""

    symbol: str
    timeframe: str
    step_ms: int
    fut_klines: pd.DataFrame
    spot_klines: pd.DataFrame | None
    binance_oi: pd.Series
    bybit_oi: pd.Series | None
    live_binance_oi: float | None
    live_bybit_oi: float | None
    spot_available: bool
    bybit_available: bool
    warnings: list[str] = field(default_factory=list)
    fetched_at_ms: int = 0
    spot_symbol: str | None = None
    basis_klines: pd.DataFrame | None = None
    funding_series: pd.Series | None = None
    live_basis_pct: float | None = None
    live_funding_rate: float | None = None
    bybit_retry_ms: int = 0


@dataclass
class AlignedRegimeData:
    """Результат выравнивания сырых данных по сетке свечей."""

    frame: pd.DataFrame
    bybit_valid: bool
    spot_available: bool
    warnings: list[str] = field(default_factory=list)


def _normalize_snapshots(series: pd.Series | None, step_ms: int) -> pd.Series:
    """Округляет timestamp снимков к ближайшей границе шага и убирает дубли."""
    if series is None or len(series) == 0:
        return pd.Series(dtype=np.float64)
    clean = pd.Series(
        pd.to_numeric(series.to_numpy(dtype=np.float64), errors="coerce"),
        index=pd.to_numeric(series.index, errors="coerce"),
    )
    clean = clean[np.isfinite(clean.index.to_numpy(dtype=np.float64))]
    clean = clean[np.isfinite(clean.to_numpy(dtype=np.float64))]
    if clean.empty:
        return pd.Series(dtype=np.float64)

    half = step_ms // 2
    rounded = ((clean.index.to_numpy(dtype=np.int64) + half) // step_ms) * step_ms
    grouped = pd.DataFrame({"ts": rounded, "value": clean.to_numpy(dtype=np.float64)})
    grouped = grouped.groupby("ts", sort=True, as_index=False)["value"].last()
    return pd.Series(
        grouped["value"].to_numpy(dtype=np.float64), index=grouped["ts"].to_numpy()
    )


def _merge_klines(
    base: pd.DataFrame | None, patch: pd.DataFrame | None, max_rows: int
) -> pd.DataFrame:
    if base is None or base.empty:
        result = patch.copy() if patch is not None else pd.DataFrame()
    elif patch is None or patch.empty:
        result = base.copy()
    else:
        result = pd.concat([base, patch])
    if result.empty:
        return result
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result.tail(max_rows)


def _merge_series(base: pd.Series | None, patch: pd.Series | None, max_points: int) -> pd.Series:
    if base is None or base.empty:
        result = patch.copy() if patch is not None else pd.Series(dtype=np.float64)
    elif patch is None or patch.empty:
        result = base.copy()
    else:
        result = pd.concat([base, patch])
    if result.empty:
        return result
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result.tail(max_points)


class OIRegimeFetcher:
    """Все загрузки OI/свечей/деривативного контекста для инструмента.

    Сессии requests создаются отдельно для каждого потока (thread-local), так как
    один экземпляр ``requests.Session`` небезопасен при конкурентных вызовах.
    """

    def __init__(
        self,
        timeout: tuple[float, float] = (3.05, 8.0),
        cancel_check: _CANCEL_CB = None,
    ) -> None:
        self._timeout = timeout
        self._cancel_check = cancel_check
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()
        self._session = self._session_for_thread()

    # -------------------- HTTP --------------------

    def set_cancel_check(self, cancel_check: _CANCEL_CB) -> None:
        """Позволяет включить проверку отмены уже после создания загрузчика."""
        self._cancel_check = cancel_check

    def _session_for_thread(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": "VolumeKit/1.0 (+oi-regime)"})
            self._local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _check_cancel(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            raise RegimeFetchCancelled("Загрузка OI Regime отменена")

    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
        self._local = threading.local()

    # ==================== HTTP ====================

    def _get_json(self, url: str, params: dict[str, object]) -> object:
        last_error: Exception | None = None
        session = self._session_for_thread()
        for attempt in range(3):
            self._check_cancel()
            try:
                response = session.get(url, params=params, timeout=self._timeout)
                if response.status_code in (418, 429) or response.status_code >= 500:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code} для {url}", response=response
                    )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.35 * (2**attempt))
                    self._check_cancel()
        raise RegimeFetchError(f"Не удалось получить {url}: {last_error}")


    # ==================== KLINES ====================

    def _fetch_klines(
        self, base_url: str, symbol: str, interval: str, limit: int
    ) -> pd.DataFrame:
        is_spot = base_url == BINANCE_SPOT
        url = f"{base_url}/api/v3/klines" if is_spot else f"{base_url}/fapi/v1/klines"
        raw = self._get_json(
            url, {"symbol": symbol, "interval": interval, "limit": int(limit)}
        )
        if not isinstance(raw, list) or not raw:
            return pd.DataFrame()
        records: list[dict[str, float | int]] = []
        for row in raw:
            if not isinstance(row, list) or len(row) < 10:
                continue
            try:
                records.append(
                    {
                        "open_time_ms": int(row[0]),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                        "taker_buy": float(row[9]),
                    }
                )
            except (TypeError, ValueError):
                continue
        if not records:
            return pd.DataFrame()
        frame = pd.DataFrame(records).set_index("open_time_ms").sort_index()
        return frame[~frame.index.duplicated(keep="last")]

    def _fetch_spot_klines(
        self, symbol: str, interval: str, limit: int
    ) -> tuple[pd.DataFrame | None, str | None, str | None]:
        """Загружает спот-свечи Binance с fallback на тикер без префикса 1000/1000000.

        Возвращает (dataframe | None, фактический тикер | None, текст ошибки | None).
        """
        candidates = spot_symbol_candidates(symbol)
        last_error: str | None = None
        for position, candidate in enumerate(candidates):
            try:
                frame = self._fetch_klines(BINANCE_SPOT, candidate, interval, limit)
            except Exception as exc:
                last_error = str(exc)
                frame = None
            if frame is not None and not frame.empty:
                if candidate != str(symbol).strip().upper():
                    log.info(
                        "Binance Spot: для %s используется спотовый тикер %s",
                        symbol,
                        candidate,
                        extra={"tool": "OI Regime"},
                    )
                return frame, candidate, None
            if frame is not None and frame.empty:
                last_error = "пустой ответ"
            if position + 1 >= len(candidates):
                break
            log.info(
                "Binance Spot: тикер %s недоступен (%s), пробуем %s",
                candidate,
                last_error,
                candidates[position + 1],
                extra={"tool": "OI Regime"},
            )
        return None, None, last_error

    # ==================== DERIVATIVE CONTEXT ====================

    def _fetch_basis_klines(
        self, symbol: str, interval: str, limit: int
    ) -> pd.DataFrame:
        """Исторический premium index (базис) Binance Futures в процентах."""
        raw = self._get_json(
            f"{BINANCE_FAPI}/fapi/v1/premiumIndexKlines",
            {"symbol": symbol, "interval": interval, "limit": int(limit)},
        )
        if not isinstance(raw, list) or not raw:
            return pd.DataFrame()
        records: list[dict[str, float]] = []
        for row in raw:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                records.append(
                    {
                        "open_time_ms": int(row[0]),
                        "basis_pct": float(row[4]) * 100.0,
                    }
                )
            except (TypeError, ValueError):
                continue
        if not records:
            return pd.DataFrame()
        frame = pd.DataFrame(records).set_index("open_time_ms").sort_index()
        return frame[~frame.index.duplicated(keep="last")]

    def _fetch_funding_history(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> pd.Series:
        """История ставки финансирования Binance Futures (последняя ставка на свечу)."""
        payload = self._get_json(
            f"{BINANCE_FAPI}/fapi/v1/fundingRate",
            {
                "symbol": symbol,
                "startTime": int(max(0, start_ms)),
                "endTime": int(end_ms),
                "limit": 1000,
            },
        )
        if not isinstance(payload, list):
            return pd.Series(dtype=np.float64)
        collected: dict[int, float] = {}
        for item in payload:
            try:
                collected[int(item["fundingTime"])] = float(item["fundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
        if not collected:
            return pd.Series(dtype=np.float64)
        return pd.Series(collected, dtype=np.float64).sort_index()

    def _fetch_premium_index(self, symbol: str) -> tuple[float | None, float | None]:
        """Текущий базис (%) и ставка финансирования из premiumIndex."""
        payload = self._get_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(payload, dict):
            return None, None
        basis_pct: float | None = None
        mark = payload.get("markPrice")
        index_price = payload.get("indexPrice")
        if _finite(mark) and _finite(index_price) and float(index_price) > 0:
            basis_pct = (float(mark) - float(index_price)) / float(index_price) * 100.0
        funding = float(payload["lastFundingRate"]) if _finite(payload.get("lastFundingRate")) else None
        return basis_pct, funding


    # ==================== OI HISTORY ====================

    def _fetch_binance_oi(
        self,
        symbol: str,
        period: str,
        need_points: int,
        desired_start: int,
        now_ms: int,
        stage_cb: _STAGE_CB = None,
    ) -> pd.Series:
        collected: dict[int, float] = {}
        end_time = now_ms
        pages = 0
        while pages < 12:
            self._check_cancel()
            if stage_cb is not None:
                stage_cb("Binance OI history...")
            payload = self._get_json(
                f"{BINANCE_FAPI}/futures/data/openInterestHist",
                {
                    "symbol": symbol,
                    "period": period,
                    "limit": BINANCE_OI_LIMIT,
                    "endTime": int(end_time),
                },
            )
            if not isinstance(payload, list) or not payload:
                break
            for item in payload:
                try:
                    collected[int(item["timestamp"])] = float(item["sumOpenInterest"])
                except (KeyError, TypeError, ValueError):
                    continue
            try:
                oldest = int(payload[0]["timestamp"])
            except (KeyError, TypeError, ValueError):
                break
            pages += 1
            if len(payload) < BINANCE_OI_LIMIT:
                break
            if oldest <= desired_start:
                break
            if oldest < now_ms - BINANCE_OI_MAX_AGE_MS - 3_600_000:
                break
            end_time = oldest - 1

        if not collected:
            return pd.Series(dtype=np.float64)
        ordered = pd.Series(collected, dtype=np.float64).sort_index()
        return ordered.tail(need_points + 5)

    def _fetch_binance_oi_latest(self, symbol: str, period: str, limit: int = 5) -> pd.Series:
        payload = self._get_json(
            f"{BINANCE_FAPI}/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": int(limit)},
        )
        if not isinstance(payload, list):
            return pd.Series(dtype=np.float64)
        collected: dict[int, float] = {}
        for item in payload:
            try:
                collected[int(item["timestamp"])] = float(item["sumOpenInterest"])
            except (KeyError, TypeError, ValueError):
                continue
        return pd.Series(collected, dtype=np.float64).sort_index()


    def _fetch_bybit_oi(
        self,
        symbol: str,
        bybit_interval: str,
        need_points: int,
        desired_start: int,
        stage_cb: _STAGE_CB = None,
    ) -> pd.Series:
        collected: dict[int, float] = {}
        cursor: str | None = None
        for _ in range(BYBIT_MAX_PAGES):
            self._check_cancel()
            if stage_cb is not None:
                stage_cb("Bybit OI history...")
            params: dict[str, object] = {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": bybit_interval,
                "limit": BYBIT_OI_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor
            payload = self._get_json(f"{BYBIT_API}/v5/market/open-interest", params)
            if not isinstance(payload, dict) or payload.get("retCode") != 0:
                raise RegimeFetchError(f"Ошибка Bybit open-interest: {payload}")
            result = payload.get("result") or {}
            items = result.get("list") or []
            if not items:
                break
            oldest = None
            for item in items:
                try:
                    ts = int(item["timestamp"])
                    value = float(item["openInterest"])
                except (KeyError, TypeError, ValueError):
                    continue
                collected[ts] = value
                oldest = ts if oldest is None else min(oldest, ts)
            cursor = result.get("nextPageCursor") or None
            if oldest is None or not cursor:
                break
            if oldest <= desired_start:
                break

        if not collected:
            return pd.Series(dtype=np.float64)
        ordered = pd.Series(collected, dtype=np.float64).sort_index()
        return ordered.tail(need_points + 5)

    def _fetch_bybit_oi_latest(
        self, symbol: str, bybit_interval: str, limit: int = 5
    ) -> pd.Series:
        payload = self._get_json(
            f"{BYBIT_API}/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": bybit_interval,
                "limit": int(limit),
            },
        )
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise RegimeFetchError(f"Ошибка Bybit open-interest: {payload}")
        collected: dict[int, float] = {}
        for item in (payload.get("result") or {}).get("list") or []:
            try:
                collected[int(item["timestamp"])] = float(item["openInterest"])
            except (KeyError, TypeError, ValueError):
                continue
        return pd.Series(collected, dtype=np.float64).sort_index()

    # ==================== LIVE OI ====================

    def _fetch_binance_live_oi(self, symbol: str) -> float | None:
        payload = self._get_json(f"{BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": symbol})
        if isinstance(payload, dict) and _finite(payload.get("openInterest")):
            return float(payload["openInterest"])
        return None

    def _fetch_bybit_live_oi(self, symbol: str) -> float | None:
        payload = self._get_json(
            f"{BYBIT_API}/v5/market/tickers", {"category": "linear", "symbol": symbol}
        )
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            return None
        items = (payload.get("result") or {}).get("list") or []
        if not items:
            return None
        value = items[0].get("openInterest")
        return float(value) if _finite(value) else None



    # ==================== PUBLIC FETCH ====================

    @staticmethod
    def _run_parallel(
        tasks: dict[str, Callable[[], object]]
    ) -> tuple[dict[str, object], dict[str, Exception]]:
        """Выполняет независимые загрузки параллельно, собирая результаты и ошибки."""
        results: dict[str, object] = {}
        errors: dict[str, Exception] = {}
        if not tasks:
            return results, errors
        workers = max(1, min(len(tasks), 6))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="oi-regime") as pool:
            futures = {pool.submit(task): name for name, task in tasks.items()}
            for future, name in futures.items():
                try:
                    results[name] = future.result()
                except RegimeFetchCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - необязательный источник
                    errors[name] = exc
        return results, errors

    @staticmethod
    def _desired_start(
        fut_klines: pd.DataFrame, step_ms: int, now_ms: int, closed_needed: int
    ) -> int:
        """Начало окна истории OI: последние closed_needed закрытых свечей, не старше 30 дней."""
        if fut_klines is None or fut_klines.empty:
            return now_ms - BINANCE_OI_MAX_AGE_MS + step_ms
        closed = fut_klines.loc[fut_klines.index + step_ms <= now_ms]
        if not closed.empty:
            start = int(closed.index[-min(len(closed), closed_needed)])
        else:
            start = int(fut_klines.index[0])
        return max(start, now_ms - BINANCE_OI_MAX_AGE_MS + step_ms)

    @staticmethod
    def _has_time_gap(
        cached_klines: pd.DataFrame,
        patch: pd.DataFrame,
        step_ms: int,
        now_ms: int,
    ) -> bool:
        """True, если между последней закрытой свечой кэша и патчем пропущено >2 интервалов."""
        if cached_klines is None or cached_klines.empty or patch is None or patch.empty:
            return False
        closed = cached_klines.loc[cached_klines.index + step_ms <= now_ms]
        if closed.empty:
            return False
        first_new = int(patch.index[0])
        last_closed = int(closed.index[-1])
        if first_new <= last_closed:
            return False
        return (first_new - last_closed) > 2 * step_ms

    def fetch_full(
        self, symbol: str, params: RegimeParams, stage_cb: _STAGE_CB = None
    ) -> RawRegimeData:
        """Полная загрузка истории свечей, OI и текущих Live-значений."""
        clean = str(symbol).strip().upper()
        if not clean:
            raise RegimeFetchError("Не задан тикер для OI Regime")
        tf = str(params.timeframe).strip().lower()
        cfg = timeframe_config(tf)
        if tf not in TIMEFRAMES:
            tf = "1h"
        step_ms = int(cfg["step_ms"])
        bybit_interval = str(cfg["bybit"])
        now_ms = _now_ms()
        closed_needed = max(1, int(params.visible_bars) + int(params.z_window) + 10)
        warnings: list[str] = []

        if stage_cb is not None:
            stage_cb("Binance Futures klines...")
        try:
            fut = self._fetch_klines(BINANCE_FAPI, clean, tf, min(1500, closed_needed + 2))
        except Exception as exc:
            raise RegimeFetchError(f"Binance Futures klines: {exc}") from exc
        if fut.empty:
            raise RegimeFetchError(f"Binance Futures не вернул свечи для {clean} ({tf})")

        desired_start = self._desired_start(fut, step_ms, now_ms, closed_needed)

        if stage_cb is not None:
            stage_cb("Параллельная загрузка Spot / OI / деривативов...")
        self._check_cancel()
        tasks: dict[str, Callable[[], object]] = {
            "spot": lambda: self._fetch_spot_klines(clean, tf, min(1000, closed_needed + 2)),
            "binance_oi": lambda: self._fetch_binance_oi(
                clean, tf, closed_needed + 2, desired_start, now_ms, None
            ),
            "live_binance_oi": lambda: self._fetch_binance_live_oi(clean),
            "basis": lambda: self._fetch_basis_klines(clean, tf, min(1000, closed_needed + 2)),
            "funding": lambda: self._fetch_funding_history(
                clean, desired_start - step_ms, now_ms + step_ms
            ),
            "premium_index": lambda: self._fetch_premium_index(clean),
        }
        if params.use_bybit:
            tasks["bybit_oi"] = lambda: self._fetch_bybit_oi(
                clean, bybit_interval, closed_needed + 2, desired_start, None
            )
            tasks["live_bybit_oi"] = lambda: self._fetch_bybit_live_oi(clean)

        results, errors = self._run_parallel(tasks)
        self._check_cancel()

        # ---------- Binance Spot ----------
        spot: pd.DataFrame | None = None
        spot_symbol: str | None = None
        spot_ok = False
        spot_error: str | None = None
        if "spot" in results and isinstance(results["spot"], tuple):
            spot_frame, spot_symbol, spot_error = results["spot"]  # type: ignore[misc]
            if isinstance(spot_frame, pd.DataFrame) and not spot_frame.empty:
                spot, spot_ok = spot_frame, True
        elif "spot" in errors:
            spot_error = str(errors["spot"])
        if not spot_ok:
            _add_warning(
                warnings,
                f"Binance Spot: спот-дельта недоступна ({spot_error or 'пустой ответ'})",
            )
            log.info(
                "Binance Spot недоступен для %s (%s): %s — спот-дельта отключена",
                clean,
                tf,
                spot_error,
                extra={"tool": "OI Regime"},
            )

        # ---------- Binance OI ----------
        binance_oi: pd.Series
        if "binance_oi" in results and isinstance(results["binance_oi"], pd.Series):
            binance_oi = results["binance_oi"]  # type: ignore[assignment]
        else:
            binance_oi = pd.Series(dtype=np.float64)
            exc = errors.get("binance_oi")
            if exc is not None:
                _add_warning(warnings, f"Binance OI history недоступен: {exc}")
                log.warning(
                    "Binance OI history недоступен для %s (%s): %s",
                    clean,
                    tf,
                    exc,
                    extra={"tool": "OI Regime"},
                )
        if binance_oi.empty:
            _add_warning(warnings, "Binance OI history пуст — матрица аномалий недоступна")
            log.warning(
                "Binance OI history пуст для %s (%s)",
                clean,
                tf,
                extra={"tool": "OI Regime"},
            )

        # ---------- Bybit OI ----------
        bybit_oi: pd.Series | None = None
        bybit_ok = False
        if params.use_bybit:
            if "bybit_oi" in results and isinstance(results["bybit_oi"], pd.Series):
                candidate = results["bybit_oi"]
                if not candidate.empty:
                    bybit_oi, bybit_ok = candidate, True
            if not bybit_ok:
                exc = errors.get("bybit_oi")
                _add_warning(
                    warnings,
                    (
                        f"Bybit OI недоступен: {exc} — расчёт только по Binance"
                        if exc is not None
                        else "Bybit OI пуст — расчёт только по Binance"
                    ),
                )
                log.warning(
                    "Bybit OI недоступен для %s (%s) — fallback Binance only",
                    clean,
                    tf,
                    extra={"tool": "OI Regime"},
                )

        # ---------- Live OI ----------
        live_binance_oi: float | None = None
        if isinstance(results.get("live_binance_oi"), float):
            live_binance_oi = results["live_binance_oi"]  # type: ignore[assignment]
        elif "live_binance_oi" in errors:
            _add_warning(warnings, f"Binance Live OI недоступен: {errors['live_binance_oi']}")
            log.warning(
                "Binance Live OI недоступен для %s: %s",
                clean,
                errors["live_binance_oi"],
                extra={"tool": "OI Regime"},
            )
        live_bybit_oi: float | None = None
        if params.use_bybit and bybit_ok:
            if isinstance(results.get("live_bybit_oi"), float):
                live_bybit_oi = results["live_bybit_oi"]  # type: ignore[assignment]
            elif "live_bybit_oi" in errors:
                _add_warning(warnings, f"Bybit Live OI недоступен: {errors['live_bybit_oi']}")
                log.warning(
                    "Bybit Live OI недоступен для %s: %s",
                    clean,
                    errors["live_bybit_oi"],
                    extra={"tool": "OI Regime"},
                )

        # ---------- Basis / Funding (non-fatal) ----------
        basis_klines = results.get("basis")
        if not isinstance(basis_klines, pd.DataFrame) or basis_klines.empty:
            basis_klines = None
        funding_series = results.get("funding")
        if not isinstance(funding_series, pd.Series) or funding_series.empty:
            funding_series = None
        live_basis_pct: float | None = None
        live_funding_rate: float | None = None
        premium = results.get("premium_index")
        if isinstance(premium, tuple):
            live_basis_pct, live_funding_rate = premium  # type: ignore[misc]
        if "basis" in errors or "funding" in errors or "premium_index" in errors:
            log.warning(
                "Деривативный контекст OI Regime недоступен для %s (%s): %s",
                clean,
                tf,
                errors,
                extra={"tool": "OI Regime"},
            )

        return RawRegimeData(
            symbol=clean,
            timeframe=tf,
            step_ms=step_ms,
            fut_klines=fut,
            spot_klines=spot,
            binance_oi=binance_oi,
            bybit_oi=bybit_oi,
            live_binance_oi=live_binance_oi,
            live_bybit_oi=live_bybit_oi,
            spot_available=spot_ok,
            bybit_available=bybit_ok,
            warnings=warnings,
            fetched_at_ms=now_ms,
            spot_symbol=spot_symbol,
            basis_klines=basis_klines,
            funding_series=funding_series,
            live_basis_pct=live_basis_pct,
            live_funding_rate=live_funding_rate,
        )


    def fetch_live(
        self, cached: RawRegimeData, params: RegimeParams, stage_cb: _STAGE_CB = None
    ) -> RawRegimeData:
        """Дешёвое обновление; при разрыве сетки автоматически выполняет fetch_full."""
        tf = str(params.timeframe).strip().lower()
        cfg = timeframe_config(tf)
        if tf not in TIMEFRAMES:
            tf = "1h"
        step_ms = int(cfg["step_ms"])
        bybit_interval = str(cfg["bybit"])
        now_ms = _now_ms()
        closed_needed = max(1, int(params.visible_bars) + int(params.z_window) + 10)
        persistent = (
            "Binance Spot",
            "Bybit OI",
            "Binance OI history ограничена",
            "Binance OI history пуст",
            "Binance OI history недоступен",
        )
        warnings = [w for w in cached.warnings if w.startswith(persistent)]

        fut_patch = pd.DataFrame()
        try:
            fut_patch = self._fetch_klines(BINANCE_FAPI, cached.symbol, tf, 3)
        except RegimeFetchCancelled:
            raise
        except Exception as exc:
            _add_warning(warnings, f"Live klines Binance: {exc}")
            log.warning(
                "Live klines Binance недоступны для %s: %s",
                cached.symbol,
                exc,
                extra={"tool": "OI Regime"},
            )

        if self._has_time_gap(cached.fut_klines, fut_patch, step_ms, now_ms):
            log.info(
                "OI Regime %s %s: разрыв временной сетки > 2 интервалов — полное обновление",
                cached.symbol,
                tf,
                extra={"tool": "OI Regime"},
            )
            return self.fetch_full(cached.symbol, params, stage_cb)

        fut = _merge_klines(cached.fut_klines, fut_patch, closed_needed + 60)
        spot = cached.spot_klines.copy() if cached.spot_klines is not None else None
        spot_ok = cached.spot_available
        spot_symbol = cached.spot_symbol or cached.symbol
        recover_bybit = bool(
            params.use_bybit
            and not cached.bybit_available
            and (now_ms - int(cached.bybit_retry_ms or 0)) >= BYBIT_RECOVERY_COOLDOWN_MS
        )
        desired_start = self._desired_start(fut, step_ms, now_ms, closed_needed)

        self._check_cancel()
        tasks: dict[str, Callable[[], object]] = {
            "oi_patch": lambda: self._fetch_binance_oi_latest(cached.symbol, tf, 5),
            "live_binance_oi": lambda: self._fetch_binance_live_oi(cached.symbol),
            "basis_patch": lambda: self._fetch_basis_klines(cached.symbol, tf, 3),
            "premium_index": lambda: self._fetch_premium_index(cached.symbol),
            "funding_patch": lambda: self._fetch_funding_history(
                cached.symbol, now_ms - 7 * 86_400_000, now_ms + step_ms
            ),
        }
        if cached.spot_available:
            tasks["spot_patch"] = lambda: self._fetch_klines(BINANCE_SPOT, spot_symbol, tf, 3)
        if params.use_bybit and cached.bybit_available:
            tasks["bybit_patch"] = lambda: self._fetch_bybit_oi_latest(
                cached.symbol, bybit_interval, 5
            )
            tasks["live_bybit_oi"] = lambda: self._fetch_bybit_live_oi(cached.symbol)
        if recover_bybit:
            tasks["bybit_recover"] = lambda: self._fetch_bybit_oi(
                cached.symbol, bybit_interval, closed_needed + 2, desired_start, None
            )
            tasks["live_bybit_oi"] = lambda: self._fetch_bybit_live_oi(cached.symbol)

        results, errors = self._run_parallel(tasks)
        self._check_cancel()

        if cached.spot_available:
            spot_patch = results.get("spot_patch")
            if isinstance(spot_patch, pd.DataFrame) and not spot_patch.empty:
                spot = _merge_klines(cached.spot_klines, spot_patch, closed_needed + 60)
                spot_ok = bool(spot is not None and not spot.empty)
            elif "spot_patch" in errors:
                _add_warning(warnings, f"Live klines Spot: {errors['spot_patch']}")
                log.warning(
                    "Live klines Spot недоступны для %s: %s",
                    cached.symbol,
                    errors["spot_patch"],
                    extra={"tool": "OI Regime"},
                )

        binance_oi = cached.binance_oi
        oi_patch = results.get("oi_patch")
        if isinstance(oi_patch, pd.Series) and not oi_patch.empty:
            binance_oi = _merge_series(cached.binance_oi, oi_patch, closed_needed + 60)
        elif "oi_patch" in errors:
            _add_warning(warnings, f"Live OI history Binance: {errors['oi_patch']}")
            log.warning(
                "Live OI history Binance недоступен для %s: %s",
                cached.symbol,
                errors["oi_patch"],
                extra={"tool": "OI Regime"},
            )

        bybit_oi = cached.bybit_oi
        bybit_ok = bool(cached.bybit_available and params.use_bybit)
        bybit_retry_ms = int(cached.bybit_retry_ms or 0)
        if params.use_bybit and cached.bybit_available:
            bybit_patch = results.get("bybit_patch")
            if isinstance(bybit_patch, pd.Series) and not bybit_patch.empty:
                bybit_oi = _merge_series(cached.bybit_oi, bybit_patch, closed_needed + 60)
            elif "bybit_patch" in errors:
                _add_warning(warnings, f"Live OI history Bybit: {errors['bybit_patch']}")
                log.warning(
                    "Live OI history Bybit недоступен для %s: %s",
                    cached.symbol,
                    errors["bybit_patch"],
                    extra={"tool": "OI Regime"},
                )
        if recover_bybit:
            recovered = results.get("bybit_recover")
            if isinstance(recovered, pd.Series) and not recovered.empty:
                bybit_oi = _merge_series(cached.bybit_oi, recovered, closed_needed + 60)
                bybit_ok = True
                bybit_retry_ms = 0
                warnings = [w for w in warnings if not w.startswith("Bybit OI")]
                log.info(
                    "OI Regime %s %s: Bybit OI восстановлен автоматически",
                    cached.symbol,
                    tf,
                    extra={"tool": "OI Regime"},
                )
            else:
                bybit_retry_ms = now_ms
                _add_warning(
                    warnings,
                    "Bybit OI недоступен — расчёт только по Binance (повтор через 5 мин)",
                )
                log.info(
                    "OI Regime %s %s: Bybit OI всё ещё недоступен, повтор через 5 мин",
                    cached.symbol,
                    tf,
                    extra={"tool": "OI Regime"},
                )

        live_binance_oi: float | None = None
        if isinstance(results.get("live_binance_oi"), float):
            live_binance_oi = results["live_binance_oi"]  # type: ignore[assignment]
        elif "live_binance_oi" in errors:
            _add_warning(warnings, f"Live OI Binance: {errors['live_binance_oi']}")
            log.warning(
                "Live OI Binance недоступен для %s: %s",
                cached.symbol,
                errors["live_binance_oi"],
                extra={"tool": "OI Regime"},
            )

        live_bybit_oi: float | None = None
        if params.use_bybit and bybit_ok:
            if isinstance(results.get("live_bybit_oi"), float):
                live_bybit_oi = results["live_bybit_oi"]  # type: ignore[assignment]
            elif "live_bybit_oi" in errors:
                _add_warning(warnings, f"Live OI Bybit: {errors['live_bybit_oi']}")
                log.warning(
                    "Live OI Bybit недоступен для %s: %s",
                    cached.symbol,
                    errors["live_bybit_oi"],
                    extra={"tool": "OI Regime"},
                )

        basis_klines = cached.basis_klines
        basis_patch = results.get("basis_patch")
        if isinstance(basis_patch, pd.DataFrame) and not basis_patch.empty:
            basis_klines = _merge_klines(basis_klines, basis_patch, closed_needed + 60)
        funding_series = cached.funding_series
        funding_patch = results.get("funding_patch")
        if isinstance(funding_patch, pd.Series) and not funding_patch.empty:
            funding_series = _merge_series(funding_series, funding_patch, closed_needed + 60)
        live_basis_pct = cached.live_basis_pct
        live_funding_rate = cached.live_funding_rate
        premium = results.get("premium_index")
        if isinstance(premium, tuple):
            fresh_basis, fresh_funding = premium  # type: ignore[misc]
            if fresh_basis is not None:
                live_basis_pct = fresh_basis
            if fresh_funding is not None:
                live_funding_rate = fresh_funding

        return RawRegimeData(
            symbol=cached.symbol,
            timeframe=tf,
            step_ms=step_ms,
            fut_klines=fut,
            spot_klines=spot,
            binance_oi=binance_oi,
            bybit_oi=bybit_oi,
            live_binance_oi=live_binance_oi,
            live_bybit_oi=live_bybit_oi,
            spot_available=spot_ok,
            bybit_available=bool(bybit_ok and params.use_bybit),
            warnings=warnings,
            fetched_at_ms=now_ms,
            spot_symbol=cached.spot_symbol,
            basis_klines=basis_klines,
            funding_series=funding_series,
            live_basis_pct=live_basis_pct,
            live_funding_rate=live_funding_rate,
            bybit_retry_ms=bybit_retry_ms,
        )




# ==================== ALIGNMENT ====================


def _consecutive_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Возвращает [start, end] непрерывных True-участков маски."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for idx, flag in enumerate(mask.tolist()):
        if flag and start is None:
            start = idx
        elif not flag and start is not None:
            runs.append((start, idx - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def _asof_value(series: pd.Series | None, ts: int) -> tuple[float | None, bool]:
    """Возвращает последнее значение с индексом <= ts и признак приблизительности."""
    if series is None or series.empty:
        return None, False
    subset = series.loc[series.index <= int(ts)]
    if subset.empty:
        return None, False
    last_index = int(subset.index[-1])
    value = subset.iloc[-1]
    if not _finite(value):
        return None, False
    return float(value), last_index != int(ts)


def _align_asof(series: pd.Series | None, target_index: pd.Index) -> pd.Series:
    """Выравнивает разреженный ряд (funding) на сетку баров: последнее значение <= ts."""
    target = np.asarray(target_index, dtype=np.int64)
    empty = pd.Series(np.nan, index=pd.Index(target), dtype=np.float64)
    if series is None or series.empty or target.size == 0:
        return empty
    ordered = series[~series.index.duplicated(keep="last")].sort_index()
    left = pd.DataFrame({"ts": target}).sort_values("ts")
    right = pd.DataFrame(
        {
            "ts": ordered.index.to_numpy(dtype=np.int64),
            "value": ordered.to_numpy(dtype=np.float64),
        }
    ).sort_values("ts")
    merged = pd.merge_asof(left, right, on="ts", direction="backward")
    aligned = pd.Series(
        merged["value"].to_numpy(dtype=np.float64),
        index=merged["ts"].to_numpy(dtype=np.int64),
    )
    return aligned.reindex(pd.Index(target))


def _prepare_bybit_grid(
    raw: RawRegimeData,
    grid: np.ndarray,
    bin_valid_start: int | None,
    bin_valid_end: int | None,
    warnings: list[str],
) -> tuple[pd.Series | None, bool]:
    """Проверяет покрытие Bybit относительно валидного окна Binance OI."""
    if not raw.bybit_available or raw.bybit_oi is None or raw.bybit_oi.empty:
        return None, False
    if bin_valid_start is None or bin_valid_end is None:
        return None, False

    by = _normalize_snapshots(raw.bybit_oi, raw.step_ms)
    if by.empty:
        return None, False
    by_grid = by.reindex(grid)

    required_mask = (grid >= bin_valid_start) & (grid <= bin_valid_end)
    required_index = grid[required_mask]
    if required_index.size == 0:
        return None, False
    required = by_grid.reindex(required_index)
    missing = required.isna().to_numpy()
    if missing.size == 0:
        return None, False

    if bool(missing[0]):
        message = "Bybit OI не покрывает начало доступного окна Binance OI"
        _add_warning(warnings, message + " — расчёт только по Binance")
        log.warning(message, extra={"tool": "OI Regime"})
        return None, False

    trailing_missing = bool(missing[-1])
    gap_mask = missing.copy()
    if trailing_missing:
        gap_mask[-1] = False
    for start, end in _consecutive_runs(gap_mask):
        if end - start + 1 >= 2:
            message = "Bybit OI имеет пропуск >= 2 шагов"
            _add_warning(warnings, message + " — расчёт только по Binance")
            log.warning(message, extra={"tool": "OI Regime"})
            return None, False

    # Одиночные внутренние пропуски заполняем линейной интерполяцией между соседями,
    # чтобы не обнулять прирост OI на одной свече и не удваивать на следующей.
    filled = required.interpolate(method="linear")
    if trailing_missing:
        filled.iloc[-1] = np.nan
    remaining = filled.isna().to_numpy()
    if remaining.size > 1 and bool(remaining[:-1].any()):
        message = "Bybit OI содержит неустранимый пропуск"
        _add_warning(warnings, message + " — расчёт только по Binance")
        log.warning(message, extra={"tool": "OI Regime"})
        return None, False

    result = by_grid.copy()
    result.loc[filled.index] = filled.to_numpy(dtype=np.float64)
    return result, True


def align_raw(
    raw: RawRegimeData, params: RegimeParams, now_ms: int | None = None
) -> AlignedRegimeData:
    """Строит DataFrame свечей с OI-приростами [t; t+step] и дельтами."""
    now = _now_ms() if now_ms is None else int(now_ms)
    step_ms = int(raw.step_ms)
    warnings = list(raw.warnings)
    closed_needed = max(1, int(params.visible_bars) + int(params.z_window) + 10)

    fut = raw.fut_klines
    if fut is None or fut.empty:
        raise RegimeFetchError("Нет свечей Binance Futures для выравнивания")
    fut = fut.copy()
    fut = fut[~fut.index.duplicated(keep="last")].sort_index()

    is_closed = (fut.index.to_numpy(dtype=np.int64) + step_ms) <= now
    closed = fut.loc[is_closed].tail(closed_needed)
    live_open = (now // step_ms) * step_ms
    live = fut.loc[[live_open]] if live_open in fut.index else None
    if live is not None and int(live.index[0]) in set(closed.index):
        live = None
    open_times = closed.index.to_numpy(dtype=np.int64)
    live_ts = int(live.index[0]) if live is not None else None

    if open_times.size == 0 and live_ts is None:
        raise RegimeFetchError("После фильтрации не осталось свечей для OI Regime")

    bin_series = _normalize_snapshots(raw.binance_oi, step_ms)
    grid_parts = [open_times, open_times + step_ms]
    if live_ts is not None:
        grid_parts.append(np.array([live_ts], dtype=np.int64))
    grid = np.unique(np.concatenate(grid_parts))
    bin_grid = bin_series.reindex(grid)
    valid_bin = bin_grid.dropna()
    bin_valid_start = int(valid_bin.index[0]) if not valid_bin.empty else None
    bin_valid_end = int(valid_bin.index[-1]) if not valid_bin.empty else None
    if (
        bin_valid_start is not None
        and open_times.size
        and bin_valid_start > int(open_times[0])
    ):
        _add_warning(
            warnings,
            "Binance OI history ограничена 30 днями — часть старых баров без OI",
        )

    by_grid, bybit_valid = _prepare_bybit_grid(
        raw, grid, bin_valid_start, bin_valid_end, warnings
    )

    if open_times.size:
        bin_start = bin_grid.reindex(open_times).to_numpy(dtype=np.float64)
        bin_end = bin_grid.reindex(open_times + step_ms).to_numpy(dtype=np.float64)
        delta_bin = pd.Series(bin_end - bin_start, index=open_times, dtype=np.float64)
    else:
        delta_bin = pd.Series(dtype=np.float64)

    if bybit_valid and by_grid is not None and open_times.size:
        by_start = by_grid.reindex(open_times).to_numpy(dtype=np.float64)
        by_end = by_grid.reindex(open_times + step_ms).to_numpy(dtype=np.float64)
        delta_by = pd.Series(by_end - by_start, index=open_times, dtype=np.float64)
        total = pd.Series(np.nan, index=delta_bin.index, dtype=np.float64)
        both = delta_bin.notna() & delta_by.notna()
        total.loc[both] = delta_bin.loc[both] + delta_by.loc[both]
    else:
        delta_by = pd.Series(np.nan, index=delta_bin.index, dtype=np.float64)
        total = delta_bin.copy()

    all_index = pd.Index(
        open_times.tolist() + ([live_ts] if live_ts is not None else [])
    )
    if raw.spot_klines is not None and not raw.spot_klines.empty:
        spot = raw.spot_klines.copy()
        spot = spot[~spot.index.duplicated(keep="last")].sort_index()
        spot_vol = spot["volume"].reindex(all_index)
        spot_tb = spot["taker_buy"].reindex(all_index)
        spot_delta_all = 2.0 * spot_tb - spot_vol
        spot_ok = True
    else:
        spot_delta_all = pd.Series(np.nan, index=all_index, dtype=np.float64)
        spot_ok = False

    candles = closed.copy()
    if live is not None:
        candles = pd.concat([candles, live]).sort_index()
    candles = candles[~candles.index.duplicated(keep="last")]

    result = candles.copy()
    result["close_time_ms"] = result.index.to_numpy(dtype=np.int64) + step_ms - 1
    result["spot_available"] = spot_ok
    result["spot_delta"] = spot_delta_all.reindex(result.index).to_numpy(dtype=np.float64)
    result["oi_binance_start"] = bin_grid.reindex(result.index).to_numpy(dtype=np.float64)
    result["oi_binance_end"] = bin_grid.reindex(result.index + step_ms).to_numpy(
        dtype=np.float64
    )
    result["delta_oi_binance"] = delta_bin.reindex(result.index).to_numpy(dtype=np.float64)
    if bybit_valid and by_grid is not None:
        result["oi_bybit_start"] = by_grid.reindex(result.index).to_numpy(dtype=np.float64)
        result["oi_bybit_end"] = by_grid.reindex(result.index + step_ms).to_numpy(
            dtype=np.float64
        )
    else:
        result["oi_bybit_start"] = np.nan
        result["oi_bybit_end"] = np.nan
    result["delta_oi_bybit"] = delta_by.reindex(result.index).to_numpy(dtype=np.float64)
    result["delta_oi_total"] = total.reindex(result.index).to_numpy(dtype=np.float64)
    result["is_live"] = False
    result["live_start_estimated"] = False
    result["has_oi"] = result["delta_oi_total"].notna()

    # ---- Деривативный контекст: базис (premium index) и ставка финансирования ----
    if raw.basis_klines is not None and not raw.basis_klines.empty and "basis_pct" in raw.basis_klines:
        basis_series = raw.basis_klines["basis_pct"]
        basis_series = basis_series[~basis_series.index.duplicated(keep="last")].sort_index()
        result["basis_pct"] = basis_series.reindex(result.index).to_numpy(dtype=np.float64)
    else:
        result["basis_pct"] = np.nan
    result["funding_rate"] = _align_asof(raw.funding_series, result.index).to_numpy(
        dtype=np.float64
    )

    if live_ts is not None and live_ts in result.index:
        bin_start_live, est_bin = _asof_value(bin_series, live_ts)
        bin_end_live = raw.live_binance_oi if _finite(raw.live_binance_oi) else None
        live_bin_delta = (
            bin_end_live - bin_start_live
            if bin_start_live is not None and bin_end_live is not None
            else None
        )
        by_start_live: float | None = None
        by_end_live: float | None = None
        est_by = False
        if bybit_valid and raw.bybit_oi is not None:
            by_series = _normalize_snapshots(raw.bybit_oi, step_ms)
            by_start_live, est_by = _asof_value(by_series, live_ts)
            by_end_live = raw.live_bybit_oi if _finite(raw.live_bybit_oi) else None
        live_by_delta = (
            by_end_live - by_start_live
            if by_start_live is not None and by_end_live is not None
            else None
        )
        if bybit_valid:
            live_total = (
                live_bin_delta + live_by_delta
                if live_bin_delta is not None and live_by_delta is not None
                else None
            )
        else:
            live_total = live_bin_delta

        result.loc[live_ts, "is_live"] = True
        result.loc[live_ts, "live_start_estimated"] = bool(est_bin or est_by)
        result.loc[live_ts, "oi_binance_start"] = bin_start_live
        result.loc[live_ts, "oi_binance_end"] = bin_end_live
        result.loc[live_ts, "delta_oi_binance"] = live_bin_delta
        result.loc[live_ts, "oi_bybit_start"] = by_start_live
        result.loc[live_ts, "oi_bybit_end"] = by_end_live
        result.loc[live_ts, "delta_oi_bybit"] = live_by_delta
        result.loc[live_ts, "delta_oi_total"] = live_total
        result.loc[live_ts, "has_oi"] = live_total is not None
        result.loc[live_ts, "basis_pct"] = (
            float(raw.live_basis_pct) if _finite(raw.live_basis_pct) else np.nan
        )
        result.loc[live_ts, "funding_rate"] = (
            float(raw.live_funding_rate) if _finite(raw.live_funding_rate) else np.nan
        )

    result["fut_delta"] = 2.0 * result["taker_buy"] - result["volume"]
    result = result[~result.index.duplicated(keep="last")].sort_index()

    return AlignedRegimeData(
        frame=result,
        bybit_valid=bybit_valid,
        spot_available=spot_ok,
        warnings=warnings,
    )

