"""Проверка и наполнение локального Parquet-кэша сделок."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import AppSettings
from app.core.paths import CACHE_DIR
from app.services.storage.downloaders import (
    _download_and_cache_binance_day,
    _download_and_cache_bybit_day,
)

log = logging.getLogger("Storage")


def get_utc_dates(days: int) -> list[str]:
    """Возвращает список последних `days` полных завершённых UTC-дней (от старых к новым)."""
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    return [(yesterday - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def get_cache_path(exchange: str, symbol: str, date_str: str) -> Path:
    return CACHE_DIR / exchange / symbol / f"{date_str}.parquet"


def ensure_days_cached(
    exchange: str,
    symbol: str,
    dates: list[str],
    settings: AppSettings,
) -> list[Path]:
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

        with ThreadPoolExecutor(max_workers=settings.download_workers) as executor:
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
