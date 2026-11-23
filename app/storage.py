import gzip
import io
import logging
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .config import CACHE_DIR, DOWNLOAD_WORKERS

log = logging.getLogger("Storage")
_thread_local = threading.local()


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = requests.Session()
    return _thread_local.session


def get_utc_dates(days: int) -> list[str]:
    """Возвращает список последних `days` полных завершённых UTC-дней (от старых к новым)."""
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    return [(yesterday - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def get_cache_path(exchange: str, symbol: str, date_str: str) -> Path:
    return CACHE_DIR / exchange / symbol / f"{date_str}.parquet"


def _download_with_retries(url: str, retries: int = 3) -> bytes | None:
    session = _get_session()
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=90)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            if attempt == retries:
                raise
            wait = 2 * attempt
            log.warning(f"Ошибка сети ({exc}), повтор {attempt}/{retries} через {wait}с: {url}")
            time.sleep(wait)
    return None


def _download_and_cache_binance_day(symbol: str, date_str: str, cache_path: Path) -> tuple[str, str, int, float]:
    t0 = time.perf_counter()
    url = (
        f"https://data.binance.vision/data/spot/daily/aggTrades/"
        f"{symbol}/{symbol}-aggTrades-{date_str}.zip"
    )
    content = _download_with_retries(url)
    if content is None:
        return date_str, "MISSING", 0, time.perf_counter() - t0

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_name = zf.namelist()[0]
        with zf.open(csv_name) as f:
            df = pd.read_csv(
                f,
                header=None,
                usecols=[1, 2, 5, 6],
                names=["price", "qty", "time", "is_buyer_maker"],
                dtype={
                    "price": np.float64,
                    "qty": np.float64,
                    "time": np.int64,
                    "is_buyer_maker": bool,
                },
            )

    if len(df) == 0:
        return date_str, "EMPTY", 0, time.perf_counter() - t0

    # Нормализуем время к миллисекундам (с 2025 Binance Spot хранит микросекунды)
    ts_ms = (df["time"] // 1000) if df["time"].iloc[0] > 10**14 else df["time"]
    out = pd.DataFrame({
        "ts_ms": ts_ms.astype(np.int64),
        "price": df["price"].astype(np.float64),
        "qty": df["qty"].astype(np.float64),
        "is_ask": (~df["is_buyer_maker"]).astype(bool),
    })

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".parquet.tmp")
    out.to_parquet(tmp_path, index=False, compression="snappy")
    tmp_path.replace(cache_path)

    return date_str, "DOWNLOADED", len(out), time.perf_counter() - t0


def _download_and_cache_bybit_day(symbol: str, date_str: str, cache_path: Path) -> tuple[str, str, int, float]:
    t0 = time.perf_counter()
    url = f"https://public.bybit.com/trading/{symbol}/{symbol}{date_str}.csv.gz"
    content = _download_with_retries(url)
    if content is None:
        return date_str, "MISSING", 0, time.perf_counter() - t0

    with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
        df = pd.read_csv(
            gz,
            usecols=["timestamp", "side", "size", "price"],
            dtype={
                "timestamp": np.float64,
                "side": str,
                "size": np.float64,
                "price": np.float64,
            },
        )

    if len(df) == 0:
        return date_str, "EMPTY", 0, time.perf_counter() - t0

    # ByBit public trading CSV хранит timestamp в секундах (float)
    ts_raw = df["timestamp"].values
    ts_ms = (ts_raw * 1000.0).astype(np.int64) if ts_raw[0] < 10**11 else ts_raw.astype(np.int64)

    out = pd.DataFrame({
        "ts_ms": ts_ms,
        "price": df["price"].astype(np.float64),
        "qty": df["size"].astype(np.float64),
        "is_ask": (df["side"].str.lower() == "buy").astype(bool),
    })

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".parquet.tmp")
    out.to_parquet(tmp_path, index=False, compression="snappy")
    tmp_path.replace(cache_path)

    return date_str, "DOWNLOADED", len(out), time.perf_counter() - t0


def ensure_days_cached(exchange: str, symbol: str, dates: list[str]) -> list[Path]:
    """
    Проверяет локальный кэш для списка дат и скачивает только недостающие дни.
    Возвращает упорядоченный список существующих путей к .parquet файлам.
    """
    missing: list[tuple[str, Path]] = []
    cached_count = 0

    for d in dates:
        p = get_cache_path(exchange, symbol, d)
        if p.exists() and p.stat().st_size > 0:
            cached_count += 1
        else:
            missing.append((d, p))

    log.info(
        f"Кэш {exchange:13s} ({symbol}) | Всего дней: {len(dates)} | "
        f"В кэше: {cached_count} | Нужно скачать: {len(missing)}"
    )

    if missing:
        t_start = time.perf_counter()
        worker_fn = (
            _download_and_cache_binance_day
            if exchange == "binance_spot"
            else _download_and_cache_bybit_day
        )

        done = 0
        total_missing = len(missing)
        total_trades = 0

        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
            futures = {
                executor.submit(worker_fn, symbol, d, p): d
                for d, p in missing
            }
            for fut in as_completed(futures):
                date_str, status, n_trades, dt = fut.result()
                done += 1
                total_trades += n_trades
                elapsed = time.perf_counter() - t_start
                eta = (elapsed / done) * (total_missing - done)

                if status == "DOWNLOADED":
                    speed = n_trades / dt if dt > 0 else 0
                    log.info(
                        f"[{exchange}] [{done:02d}/{total_missing:02d}] {date_str} | "
                        f"скачан: {n_trades:9,d} сд. за {dt:5.2f}с ({speed:,.0f} сд/с) | ETA: {eta:.0f}с"
                    )
                else:
                    log.warning(
                        f"[{exchange}] [{done:02d}/{total_missing:02d}] {date_str} | статус: {status}"
                    )

        log.info(
            f"[{exchange}] Загрузка завершена за {time.perf_counter() - t_start:.2f}с | "
            f"Новых сделок сохранено: {total_trades:,d}"
        )

    existing_paths = [
        get_cache_path(exchange, symbol, d)
        for d in dates
        if get_cache_path(exchange, symbol, d).exists()
    ]
    return existing_paths