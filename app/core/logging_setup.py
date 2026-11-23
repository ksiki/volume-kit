"""Единая настройка логирования приложения: консоль и JSONL-файлы по дням."""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.paths import LOGS_DIR

LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)-10s | %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"

LOG_RETENTION_DAYS = 7
LOG_FILE_SUFFIX = ".jsonl"

SESSION_ID = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid():05d}"

_SERVICE_LOGGER_PREFIXES = (
    "app.services.calibration",
    "app.services.analytics",
    "app.services.market_data",
    "app.services.storage",
)

_UI_LOGGER_PREFIXES = ("app.ui.workers.calibration_worker",)


def resolve_tool(logger_name: str, explicit: str | None = None) -> str:
    """Определяет инструмент-источник записи по extra={"tool": ...} или имени логгера."""
    if explicit:
        return str(explicit)

    name = logger_name or ""
    if name.startswith(_SERVICE_LOGGER_PREFIXES) or name.startswith(_UI_LOGGER_PREFIXES):
        return "Calibrator"

    if name.startswith("app.tools."):
        package = name[len("app.tools."):].split(".", 1)[0]
        if package:
            return "".join(part.capitalize() for part in package.split("_"))

    return "System"


class JsonlFileHandler(logging.Handler):
    """Пишет записи в app/logs/YYYY-MM-DD.jsonl (по строке JSON на запись)."""

    def __init__(self, logs_dir: Path = LOGS_DIR) -> None:
        super().__init__()
        self._logs_dir = Path(logs_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = ""
        self._stream = None  # type: ignore[assignment]

    def _open_for_date(self, date_str: str) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        self._stream = (self._logs_dir / f"{date_str}{LOG_FILE_SUFFIX}").open("a", encoding="utf-8")
        self._current_date = date_str

    def emit(self, record: logging.LogRecord) -> None:
        try:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            if self._stream is None or date_str != self._current_date:
                self._open_for_date(date_str)

            entry = {
                "ts": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "date": date_str,
                "level": record.levelname,
                "tool": resolve_tool(record.name, getattr(record, "tool", None)),
                "session_id": getattr(record, "session_id", SESSION_ID),
                "event": getattr(record, "event", "log"),
                "message": self.format(record),
            }
            self._stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
        finally:
            self._stream = None
            super().close()


def _log_files(logs_dir: Path, keep_days: int) -> list[Path]:
    directory = Path(logs_dir)
    if not directory.is_dir():
        return []

    cutoff = datetime.now().date() - timedelta(days=keep_days)
    files: list[Path] = []
    for path in sorted(directory.glob(f"*{LOG_FILE_SUFFIX}")):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= cutoff:
            files.append(path)
    return files


def prune_old_logs(logs_dir: Path = LOGS_DIR, keep_days: int = LOG_RETENTION_DAYS) -> list[Path]:
    """Удаляет .jsonl-файлы старше keep_days дней и возвращает список удалённых."""
    removed: list[Path] = []
    directory = Path(logs_dir)
    if not directory.is_dir():
        return removed

    cutoff = datetime.now().date() - timedelta(days=keep_days)
    for path in directory.glob(f"*{LOG_FILE_SUFFIX}"):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= cutoff:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


def read_recent_logs(
    logs_dir: Path = LOGS_DIR,
    keep_days: int = LOG_RETENTION_DAYS,
) -> list[dict[str, Any]]:
    """Читает записи из .jsonl-файлов за последние keep_days дней."""
    entries: list[dict[str, Any]] = []
    for path in _log_files(logs_dir, keep_days):
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        entries.append(record)
        except OSError:
            continue
    return entries


def available_log_dates(
    logs_dir: Path = LOGS_DIR,
    keep_days: int = LOG_RETENTION_DAYS,
) -> list[str]:
    """Возвращает дни, за которые есть файлы логов (новые сначала)."""
    dates: list[str] = []
    for path in _log_files(logs_dir, keep_days):
        dates.append(path.stem)
    return sorted(set(dates), reverse=True)


def log_event(
    event: str,
    message: str,
    *,
    tool: str | None = None,
    level: int = logging.INFO,
    logger_name: str = "app.services.calibration",
) -> None:
    """Пишет служебную запись лога (например, маркер запуска event="run_start")."""
    logging.getLogger(logger_name).log(level, message, extra={"event": event, "tool": tool})


def configure_logging(console: bool = True) -> None:
    """Настраивает корневой логгер: консоль (опционально) и JSONL-файлы с ротацией."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if console:
        logging.basicConfig(
            level=logging.INFO,
            format=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT,
        )

    prune_old_logs()

    if not any(isinstance(handler, JsonlFileHandler) for handler in root.handlers):
        file_handler = JsonlFileHandler()
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(file_handler)
