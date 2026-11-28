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

from app.core.http_client import ProxyNotConfiguredError, create_http_session
from app.core.proxy import sanitize_error
from app.services.oi_regime.models import RegimeParams

log = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"
BYBIT_API = "https://api.bybit.com"
OKX_API = "https://www.okx.com"

OKX_OI_HISTORY_URL = f"{OKX_API}/api/v5/rubik/stat/contracts/open-interest-history"
OKX_OPEN_INTEREST_URL = f"{OKX_API}/api/v5/public/open-interest"

TIMEFRAMES: dict[str, dict[str, object]] = {
    "5m": {"step_ms": 300_000, "bybit": "5min", "okx": "5m"},
    "15m": {"step_ms": 900_000, "bybit": "15min", "okx": "15m"},
    "1h": {"step_ms": 3_600_000, "bybit": "1h", "okx": "1H"},
    "4h": {"step_ms": 14_400_000, "bybit": "4h", "okx": "4H"},
}

BINANCE_OI_LIMIT = 500
BYBIT_OI_LIMIT = 200
BYBIT_MAX_PAGES = 30
OKX_OI_LIMIT = 100
OKX_MAX_PAGES = 15
BINANCE_OI_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000
SECONDARY_RECOVERY_COOLDOWN_MS = 5 * 60 * 1000

# Фиксированный порядок бирж для подписей, статусов и агрегации.
EXCHANGE_ORDER: tuple[str, ...] = ("binance", "bybit", "okx")
SECONDARY_EXCHANGES: tuple[str, ...] = ("bybit", "okx")
EXCHANGE_LABELS: dict[str, str] = {
    "binance": "Binance",
    "bybit": "Bybit",
    "okx": "OKX",
}

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


def to_okx_swap_inst_id(symbol: str) -> str | None:
    """Конвертация тикера Binance в OKX swap instId (BTCUSDT → BTC-USDT-SWAP).

    Поддерживаются только USDT-маржинальные пары; для остальных возвращается ``None``,
    чтобы OKX-источник корректно отключился без падения мастер-загрузки Binance.
    """
    clean = str(symbol).strip().upper()
    if not clean:
        return None
    if clean.endswith("USDT") and len(clean) > len("USDT"):
        return f"{clean[:-4]}-USDT-SWAP"
    return None


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
    live_binance_oi: float | None
    spot_available: bool
    secondary_oi: dict[str, pd.Series] = field(default_factory=dict)
    live_secondary_oi: dict[str, tuple[int, float] | None] = field(default_factory=dict)
    exchange_available: dict[str, bool] = field(default_factory=dict)
    exchange_retry_ms: dict[str, int] = field(default_factory=dict)
    enabled_exchanges: tuple[str, ...] = ("binance",)
    warnings: list[str] = field(default_factory=list)
    fetched_at_ms: int = 0
    spot_symbol: str | None = None
    basis_klines: pd.DataFrame | None = None
    funding_series: pd.Series | None = None
    live_basis_pct: float | None = None
    live_funding_rate: float | None = None


@dataclass
class AlignedRegimeData:
    """Результат выравнивания сырых данных по сетке свечей."""

    frame: pd.DataFrame
    spot_available: bool
    exchange_valid: dict[str, bool] = field(default_factory=dict)
    active_exchanges: list[str] = field(default_factory=lambda: ["binance"])
    live_start_estimated_by_ex: dict[str, bool] = field(default_factory=dict)
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
            session = create_http_session()
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
            except ProxyNotConfiguredError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    delay = 0.35 * (2**attempt)
                    response = getattr(exc, "response", None)
                    if response is not None and getattr(response, "status_code", None) in (
                        418,
                        429,
                    ):
                        retry_after = response.headers.get("Retry-After")
                        try:
                            seconds = float(str(retry_after).strip())
                        except (TypeError, ValueError):
                            seconds = None
                        if seconds is not None:
                            delay = min(max(seconds, delay), 5.0)
                    time.sleep(delay)
                    self._check_cancel()
        raise RegimeFetchError(f"Не удалось получить {url}: {sanitize_error(last_error)}")


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
        """История ставки финансирования Binance Futures в процентах (последняя ставка на свечу).

        Binance отдаёт ``fundingRate`` десятичной дробью (0.0001 = 0.01%), поэтому
        значение переводится в проценты сразу при парсинге.
        """
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
                collected[int(item["fundingTime"])] = float(item["fundingRate"]) * 100.0
            except (KeyError, TypeError, ValueError):
                continue
        if not collected:
            return pd.Series(dtype=np.float64)
        return pd.Series(collected, dtype=np.float64).sort_index()

    def _fetch_premium_index(self, symbol: str) -> tuple[float | None, float | None]:
        """Текущий базис (%) и ставка финансирования (%) из premiumIndex.

        ``lastFundingRate`` приходит десятичной дробью и переводится в проценты здесь же.
        """
        payload = self._get_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(payload, dict):
            return None, None
        basis_pct: float | None = None
        mark = payload.get("markPrice")
        index_price = payload.get("indexPrice")
        if _finite(mark) and _finite(index_price) and float(index_price) > 0:
            basis_pct = (float(mark) - float(index_price)) / float(index_price) * 100.0
        funding = (
            float(payload["lastFundingRate"]) * 100.0
            if _finite(payload.get("lastFundingRate"))
            else None
        )
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

    # ==================== OKX OI ====================

    def _fetch_okx_oi(
        self,
        inst_id: str,
        period: str,
        need_points: int,
        desired_start: int,
        stage_cb: _STAGE_CB = None,
    ) -> pd.Series:
        """История OI OKX (oiCcy — уже в базовой монете, ctVal не нужен)."""
        collected: dict[int, float] = {}
        end_ms: int | None = None
        for _ in range(OKX_MAX_PAGES):
            self._check_cancel()
            if stage_cb is not None:
                stage_cb("OKX OI history...")
            params: dict[str, object] = {
                "instId": inst_id,
                "period": period,
                "limit": OKX_OI_LIMIT,
            }
            if end_ms is not None:
                params["end"] = int(end_ms)
            payload = self._get_json(OKX_OI_HISTORY_URL, params)
            if not isinstance(payload, dict) or str(payload.get("code")) != "0":
                raise RegimeFetchError(f"Ошибка OKX open-interest-history: {payload}")
            rows = payload.get("data") or []
            if not rows:
                break

            oldest: int | None = None
            fresh = 0
            for row in rows:
                try:
                    ts = int(row[0])
                    value = float(row[2])  # oiCcy — OI в базовой монете
                except (IndexError, TypeError, ValueError):
                    continue
                if ts not in collected:
                    fresh += 1
                collected[ts] = value
                oldest = ts if oldest is None else min(oldest, ts)

            if oldest is None or fresh == 0:
                break
            if len(rows) < OKX_OI_LIMIT:
                break
            if oldest <= desired_start:
                break
            end_ms = oldest - 1

        if not collected:
            return pd.Series(dtype=np.float64)
        ordered = pd.Series(collected, dtype=np.float64).sort_index()
        return ordered.tail(need_points + 5)

    def _fetch_okx_oi_latest(self, inst_id: str, period: str, limit: int = 5) -> pd.Series:
        payload = self._get_json(
            OKX_OI_HISTORY_URL,
            {"instId": inst_id, "period": period, "limit": int(limit)},
        )
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise RegimeFetchError(f"Ошибка OKX open-interest-history: {payload}")
        collected: dict[int, float] = {}
        for row in payload.get("data") or []:
            try:
                collected[int(row[0])] = float(row[2])
            except (IndexError, TypeError, ValueError):
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

    def _fetch_okx_live_oi(self, inst_id: str) -> tuple[int, float] | None:
        payload = self._get_json(
            OKX_OPEN_INTEREST_URL, {"instType": "SWAP", "instId": inst_id}
        )
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            return None
        items = payload.get("data") or []
        if not items:
            return None
        item = items[0]
        if not _finite(item.get("oiCcy")):
            return None
        try:
            ts = int(item.get("ts"))
        except (TypeError, ValueError):
            ts = _now_ms()
        return ts, float(item["oiCcy"])



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
        okx_period = str(cfg["okx"])
        now_ms = _now_ms()
        closed_needed = max(1, int(params.visible_bars) + int(params.z_window) + 10)
        enabled_exchanges: tuple[str, ...] = ("binance",)
        if params.use_bybit:
            enabled_exchanges += ("bybit",)
        if params.use_okx:
            enabled_exchanges += ("okx",)
        okx_inst_id = to_okx_swap_inst_id(clean)
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
        if params.use_okx:
            if okx_inst_id is None:
                _add_warning(
                    warnings,
                    f"OKX OI: тикер {clean} не поддерживается (нужен *USDT) — OKX исключён",
                )
                log.info(
                    "OKX OI недоступен для %s: неподдерживаемый тикер",
                    clean,
                    extra={"tool": "OI Regime"},
                )
            else:
                tasks["okx_oi"] = lambda: self._fetch_okx_oi(
                    okx_inst_id, okx_period, closed_needed + 2, desired_start, None
                )
                tasks["live_okx_oi"] = lambda: self._fetch_okx_live_oi(okx_inst_id)

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

        # ---------- Вторичные источники OI (Bybit / OKX) ----------
        secondary_oi: dict[str, pd.Series] = {}
        live_secondary_oi: dict[str, tuple[int, float] | None] = {}
        exchange_available: dict[str, bool] = {"binance": not binance_oi.empty}
        exchange_retry_ms: dict[str, int] = {"binance": 0}
        for ex in SECONDARY_EXCHANGES:
            if ex not in enabled_exchanges:
                continue
            label = EXCHANGE_LABELS[ex]
            if ex == "okx" and okx_inst_id is None:
                exchange_available[ex] = False
                exchange_retry_ms[ex] = 0
                live_secondary_oi[ex] = None
                continue

            series = results.get(f"{ex}_oi")
            available = isinstance(series, pd.Series) and not series.empty
            if available:
                secondary_oi[ex] = series  # type: ignore[assignment]
            else:
                exc = errors.get(f"{ex}_oi")
                _add_warning(
                    warnings,
                    (
                        f"{label} OI недоступен: {exc} — {label} исключён из агрегации"
                        if exc is not None
                        else f"{label} OI пуст — {label} исключён из агрегации"
                    ),
                )
                log.warning(
                    "%s OI недоступен для %s (%s) — биржа исключена из агрегации",
                    label,
                    clean,
                    tf,
                    extra={"tool": "OI Regime"},
                )

            live_raw = results.get(f"live_{ex}_oi")
            live_value: tuple[int, float] | None = None
            if ex == "okx":
                if isinstance(live_raw, tuple) and len(live_raw) == 2:
                    live_value = (int(live_raw[0]), float(live_raw[1]))
            elif isinstance(live_raw, float):
                live_value = (now_ms, float(live_raw))
            if live_value is None and f"live_{ex}_oi" in errors:
                _add_warning(warnings, f"Live OI {label}: {errors[f'live_{ex}_oi']}")
                log.warning(
                    "Live OI %s недоступен для %s: %s",
                    label,
                    clean,
                    errors[f"live_{ex}_oi"],
                    extra={"tool": "OI Regime"},
                )

            exchange_available[ex] = bool(available)
            exchange_retry_ms[ex] = 0
            live_secondary_oi[ex] = live_value

        # ---------- Live OI Binance ----------
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
            live_binance_oi=live_binance_oi,
            spot_available=spot_ok,
            secondary_oi=secondary_oi,
            live_secondary_oi=live_secondary_oi,
            exchange_available=exchange_available,
            exchange_retry_ms=exchange_retry_ms,
            enabled_exchanges=enabled_exchanges,
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
        okx_period = str(cfg["okx"])
        now_ms = _now_ms()
        closed_needed = max(1, int(params.visible_bars) + int(params.z_window) + 10)
        enabled_exchanges: tuple[str, ...] = ("binance",) + tuple(
            ex for ex in SECONDARY_EXCHANGES if getattr(params, f"use_{ex}", False)
        )
        okx_inst_id = to_okx_swap_inst_id(cached.symbol)
        persistent = (
            "Binance Spot",
            "Bybit OI",
            "OKX OI",
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
        recover_secondary: dict[str, bool] = {}
        for ex in SECONDARY_EXCHANGES:
            if ex not in enabled_exchanges:
                continue
            if ex == "okx" and okx_inst_id is None:
                continue
            recover_secondary[ex] = bool(
                not cached.exchange_available.get(ex, False)
                and (now_ms - int(cached.exchange_retry_ms.get(ex, 0) or 0))
                >= SECONDARY_RECOVERY_COOLDOWN_MS
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
        for ex in SECONDARY_EXCHANGES:
            if ex not in enabled_exchanges:
                continue
            if ex == "okx" and okx_inst_id is None:
                continue
            available = bool(cached.exchange_available.get(ex, False))
            if available:
                if ex == "bybit":
                    tasks["bybit_patch"] = lambda: self._fetch_bybit_oi_latest(
                        cached.symbol, bybit_interval, 5
                    )
                    tasks["live_bybit_oi"] = lambda: self._fetch_bybit_live_oi(cached.symbol)
                else:
                    tasks["okx_patch"] = lambda: self._fetch_okx_oi_latest(
                        okx_inst_id, okx_period, 5
                    )
                    tasks["live_okx_oi"] = lambda: self._fetch_okx_live_oi(okx_inst_id)
            elif recover_secondary.get(ex):
                if ex == "bybit":
                    tasks["bybit_recover"] = lambda: self._fetch_bybit_oi(
                        cached.symbol, bybit_interval, closed_needed + 2, desired_start, None
                    )
                    tasks["live_bybit_oi"] = lambda: self._fetch_bybit_live_oi(cached.symbol)
                else:
                    tasks["okx_recover"] = lambda: self._fetch_okx_oi(
                        okx_inst_id, okx_period, closed_needed + 2, desired_start, None
                    )
                    tasks["live_okx_oi"] = lambda: self._fetch_okx_live_oi(okx_inst_id)

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

        # ---------- Вторичные источники OI (Bybit / OKX) ----------
        secondary_oi: dict[str, pd.Series] = {}
        exchange_available: dict[str, bool] = {"binance": True}
        exchange_retry_ms: dict[str, int] = {"binance": 0}
        live_secondary_oi: dict[str, tuple[int, float] | None] = {}
        for ex in SECONDARY_EXCHANGES:
            if ex not in enabled_exchanges:
                continue
            label = EXCHANGE_LABELS[ex]
            series = cached.secondary_oi.get(ex)
            available = bool(cached.exchange_available.get(ex, False))
            retry_ms = int(cached.exchange_retry_ms.get(ex, 0) or 0)
            if ex == "okx" and okx_inst_id is None:
                exchange_available[ex] = False
                exchange_retry_ms[ex] = retry_ms
                live_secondary_oi[ex] = None
                continue

            patch = results.get(f"{ex}_patch")
            if available:
                if isinstance(patch, pd.Series) and not patch.empty:
                    series = _merge_series(series, patch, closed_needed + 60)
                elif f"{ex}_patch" in errors:
                    _add_warning(
                        warnings, f"Live OI history {label}: {errors[f'{ex}_patch']}"
                    )
                    log.warning(
                        "Live OI history %s недоступен для %s: %s",
                        label,
                        cached.symbol,
                        errors[f"{ex}_patch"],
                        extra={"tool": "OI Regime"},
                    )

            if recover_secondary.get(ex):
                recovered = results.get(f"{ex}_recover")
                if isinstance(recovered, pd.Series) and not recovered.empty:
                    series = _merge_series(
                        cached.secondary_oi.get(ex), recovered, closed_needed + 60
                    )
                    available = True
                    retry_ms = 0
                    warnings = [w for w in warnings if not w.startswith(f"{label} OI")]
                    log.info(
                        "OI Regime %s %s: %s OI восстановлен автоматически",
                        cached.symbol,
                        tf,
                        label,
                        extra={"tool": "OI Regime"},
                    )
                else:
                    available = False
                    retry_ms = now_ms
                    _add_warning(
                        warnings,
                        f"{label} OI недоступен — {label} исключён из агрегации "
                        "(повтор через 5 мин)",
                    )
                    log.info(
                        "OI Regime %s %s: %s OI всё ещё недоступен, повтор через 5 мин",
                        cached.symbol,
                        tf,
                        label,
                        extra={"tool": "OI Regime"},
                    )

            if series is None or series.empty:
                available = False
            if available:
                secondary_oi[ex] = series  # type: ignore[assignment]

            live_raw = results.get(f"live_{ex}_oi")
            live_value: tuple[int, float] | None = None
            if available:
                if ex == "okx":
                    if isinstance(live_raw, tuple) and len(live_raw) == 2:
                        live_value = (int(live_raw[0]), float(live_raw[1]))
                elif isinstance(live_raw, float):
                    live_value = (now_ms, float(live_raw))
                if live_value is None and f"live_{ex}_oi" in errors:
                    _add_warning(warnings, f"Live OI {label}: {errors[f'live_{ex}_oi']}")
                    log.warning(
                        "Live OI %s недоступен для %s: %s",
                        label,
                        cached.symbol,
                        errors[f"live_{ex}_oi"],
                        extra={"tool": "OI Regime"},
                    )

            exchange_available[ex] = bool(available)
            exchange_retry_ms[ex] = retry_ms
            live_secondary_oi[ex] = live_value

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
            live_binance_oi=live_binance_oi,
            spot_available=spot_ok,
            secondary_oi=secondary_oi,
            live_secondary_oi=live_secondary_oi,
            exchange_available=exchange_available,
            exchange_retry_ms=exchange_retry_ms,
            enabled_exchanges=enabled_exchanges,
            warnings=warnings,
            fetched_at_ms=now_ms,
            spot_symbol=cached.spot_symbol,
            basis_klines=basis_klines,
            funding_series=funding_series,
            live_basis_pct=live_basis_pct,
            live_funding_rate=live_funding_rate,
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


def _prepare_secondary_oi_grid(
    exchange_id: str,
    raw: RawRegimeData,
    grid: np.ndarray,
    bin_valid_start: int | None,
    bin_valid_end: int | None,
    warnings: list[str],
) -> tuple[pd.Series | None, bool]:
    """Проверяет покрытие вторичной биржи относительно валидного окна Binance OI."""
    label = EXCHANGE_LABELS.get(exchange_id, exchange_id)
    series = raw.secondary_oi.get(exchange_id)
    if not raw.exchange_available.get(exchange_id, False) or series is None or series.empty:
        return None, False
    if bin_valid_start is None or bin_valid_end is None:
        return None, False

    normalized = _normalize_snapshots(series, raw.step_ms)
    if normalized.empty:
        return None, False
    exchange_grid = normalized.reindex(grid)

    required_mask = (grid >= bin_valid_start) & (grid <= bin_valid_end)
    required_index = grid[required_mask]
    if required_index.size == 0:
        return None, False
    required = exchange_grid.reindex(required_index)
    missing = required.isna().to_numpy()
    if missing.size == 0:
        return None, False

    if bool(missing[0]):
        message = f"{label} OI не покрывает начало доступного окна Binance OI"
        _add_warning(warnings, message + f" — {label} исключён из агрегации")
        log.warning(message, extra={"tool": "OI Regime"})
        return None, False

    trailing_missing = bool(missing[-1])
    gap_mask = missing.copy()
    if trailing_missing:
        gap_mask[-1] = False
    for start, end in _consecutive_runs(gap_mask):
        if end - start + 1 >= 2:
            message = f"{label} OI имеет пропуск >= 2 шагов"
            _add_warning(warnings, message + f" — {label} исключён из агрегации")
            log.warning(message, extra={"tool": "OI Regime"})
            return None, False

    # Одиночные внутренние пропуски заполняем линейной интерполяцией между соседями,
    # чтобы не обнулять прирост OI на одной свече и не удваивать на следующей.
    filled = required.interpolate(method="linear")
    if trailing_missing:
        filled.iloc[-1] = np.nan
    remaining = filled.isna().to_numpy()
    if remaining.size > 1 and bool(remaining[:-1].any()):
        message = f"{label} OI содержит неустранимый пропуск"
        _add_warning(warnings, message + f" — {label} исключён из агрегации")
        log.warning(message, extra={"tool": "OI Regime"})
        return None, False

    result = exchange_grid.copy()
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

    exchange_valid: dict[str, bool] = {"binance": bin_valid_start is not None}
    secondary_grids: dict[str, pd.Series] = {}
    for ex in SECONDARY_EXCHANGES:
        if ex not in raw.enabled_exchanges:
            continue
        grid_ex, valid = _prepare_secondary_oi_grid(
            ex, raw, grid, bin_valid_start, bin_valid_end, warnings
        )
        if valid and grid_ex is not None:
            secondary_grids[ex] = grid_ex
            exchange_valid[ex] = True
    active_exchanges: list[str] = ["binance"] + [
        ex for ex in SECONDARY_EXCHANGES if exchange_valid.get(ex, False)
    ]

    delta_columns: dict[str, pd.Series] = {}
    oi_start_columns: dict[str, pd.Series] = {}
    oi_end_columns: dict[str, pd.Series] = {}
    if open_times.size:
        bin_start = bin_grid.reindex(open_times).to_numpy(dtype=np.float64)
        bin_end = bin_grid.reindex(open_times + step_ms).to_numpy(dtype=np.float64)
        delta_bin = pd.Series(bin_end - bin_start, index=open_times, dtype=np.float64)
        oi_start_columns["binance"] = pd.Series(
            bin_start, index=open_times, dtype=np.float64
        )
        oi_end_columns["binance"] = pd.Series(bin_end, index=open_times, dtype=np.float64)
    else:
        delta_bin = pd.Series(dtype=np.float64)
        oi_start_columns["binance"] = pd.Series(dtype=np.float64)
        oi_end_columns["binance"] = pd.Series(dtype=np.float64)
    delta_columns["binance"] = delta_bin

    for ex, grid_ex in secondary_grids.items():
        ex_start = grid_ex.reindex(open_times)
        ex_end = grid_ex.reindex(open_times + step_ms)
        delta_columns[ex] = (ex_end - ex_start).astype(np.float64)
        oi_start_columns[ex] = ex_start.astype(np.float64)
        oi_end_columns[ex] = ex_end.astype(np.float64)

    # Сумма ΔOI по всем непустым валидным биржам на каждом баре.
    # Набор бирж в числителе определяется ровно непустыми delta_oi_<ex>;
    # engine использует тот же признак для знаменателя (_oi_total_start).
    if open_times.size:
        total = delta_columns["binance"].copy()
        for ex in secondary_grids:
            total = total.add(delta_columns[ex], fill_value=0.0)
        total = total.where(delta_columns["binance"].notna())
    else:
        total = pd.Series(dtype=np.float64)

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
    for ex in secondary_grids:
        result[f"oi_{ex}_start"] = (
            oi_start_columns[ex].reindex(result.index).to_numpy(dtype=np.float64)
        )
        result[f"oi_{ex}_end"] = (
            oi_end_columns[ex].reindex(result.index).to_numpy(dtype=np.float64)
        )
        result[f"delta_oi_{ex}"] = (
            delta_columns[ex].reindex(result.index).to_numpy(dtype=np.float64)
        )
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

    live_start_estimated_by_ex: dict[str, bool] = {
        ex: False for ex in active_exchanges
    }
    if live_ts is not None and live_ts in result.index:
        bin_start_live, est_bin = _asof_value(bin_series, live_ts)
        bin_end_live = raw.live_binance_oi if _finite(raw.live_binance_oi) else None
        live_bin_delta = (
            bin_end_live - bin_start_live
            if bin_start_live is not None and bin_end_live is not None
            else None
        )
        live_total: float | None = None
        if live_bin_delta is not None:
            live_total = live_bin_delta
        live_start_estimated_by_ex["binance"] = bool(est_bin)

        result.loc[live_ts, "is_live"] = True
        result.loc[live_ts, "oi_binance_start"] = bin_start_live
        result.loc[live_ts, "oi_binance_end"] = bin_end_live
        result.loc[live_ts, "delta_oi_binance"] = live_bin_delta

        for ex in secondary_grids:
            ex_series = _normalize_snapshots(raw.secondary_oi.get(ex), step_ms)
            ex_start_live, est_ex = _asof_value(ex_series, live_ts)
            live_pair = raw.live_secondary_oi.get(ex)
            ex_end_live = (
                float(live_pair[1])
                if live_pair is not None and _finite(live_pair[1])
                else None
            )
            ex_delta_live = (
                ex_end_live - ex_start_live
                if ex_start_live is not None and ex_end_live is not None
                else None
            )
            live_start_estimated_by_ex[ex] = bool(est_ex)
            result.loc[live_ts, f"oi_{ex}_start"] = ex_start_live
            result.loc[live_ts, f"oi_{ex}_end"] = ex_end_live
            result.loc[live_ts, f"delta_oi_{ex}"] = ex_delta_live
            # Числитель live-бара включает ту же биржу, что дала непустую дельту;
            # engine использует этот же признак для знаменателя ΔOI %.
            if ex_delta_live is not None and live_total is not None:
                live_total += ex_delta_live

        result.loc[live_ts, "live_start_estimated"] = any(
            live_start_estimated_by_ex.get(ex, False) for ex in active_exchanges
        )
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
        spot_available=spot_ok,
        exchange_valid=exchange_valid,
        active_exchanges=active_exchanges,
        live_start_estimated_by_ex=live_start_estimated_by_ex,
        warnings=warnings,
    )

