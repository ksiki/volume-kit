import copy
import json
import sys
from pathlib import Path
from typing import Any

# ===================== ПУТИ (СОВМЕСТИМО С .EXE) =====================
# В собранном .exe (PyInstaller / Nuitka) файлы и папки лежат рядом с самим .exe,
# а в обычном режиме — в папке app/ (чтобы не потерять текущий data_cache)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.json"
CACHE_DIR = BASE_DIR / "data_cache"
OUTPUT_DIR = BASE_DIR / "output"

# ===================== ДЕФОЛТНЫЕ НАСТРОЙКИ =====================
# В JSON ключи словарей всегда строковые, а вместо бесконечности (inf) используется null
DEFAULT_CONFIG: dict[str, Any] = {
    "SYMBOL": "BTCUSDT",
    "SPOT_CLUSTERS": 15,
    "FUTURES_CLUSTERS": 30,
    "DAYS_BINANCE_1H": 60,
    "DAYS_BINANCE_15M": 30,
    "DAYS_BYBIT_15M": 30,
    "DAYS_BYBIT_5M": 14,
    "CLUSTER_MODE": "round",
    "ATR_LOOKBACK_DAYS": {
        "5": 7,
        "15": 14,
        "60": 21,
        "240": 45,
        "1440": 90,
    },
    "ATR_WINSOR_PCT": 95.0,
    "SCALE_ROUNDING_RULES": [
        [20, 1],
        [50, 2],
        [100, 5],
        [250, 10],
        [500, 25],
        [1000, 50],
        [2500, 100],
        [5000, 250],
        [None, 500],
    ],
    "HALF_LIFE_DAYS": {
        "5": 4.0,
        "15": 7.0,
        "60": 14.0,
        "240": 30.0,
        "1440": 60.0,
    },
    "OUTLIER_DAY_MAX_RATIO": 2.5,
    "OUTLIER_DAY_MIN_WEIGHT": 0.15,
    "DOWNLOAD_WORKERS": 4,
    "TZ_OFFSET_HOURS": 0,
    "CONSOLE_TOP_ROWS": 0,
    "SAVE_SORTED_CSV": False,
}


def _save_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _load_or_create_config() -> dict[str, Any]:
    """Читает config.json рядом с приложением; при отсутствии создаёт файл с дефолтами."""
    if not CONFIG_PATH.exists():
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        _save_config(defaults)
        print(f"[Config] Создан файл настроек по умолчанию: {CONFIG_PATH}")
        return defaults

    try:
        raw_text = CONFIG_PATH.read_text(encoding="utf-8")
        user_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ошибка синтаксиса в {CONFIG_PATH} (строка {exc.lineno}, столбец {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(user_data, dict):
        raise RuntimeError(f"Файл {CONFIG_PATH} должен содержать JSON-объект {{...}}")

    # Подмешиваем недостающие ключи из дефолтов (защита при обновлениях или случайном удалении поля)
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(user_data)

    missing_keys = [k for k in DEFAULT_CONFIG if k not in user_data]
    if missing_keys:
        _save_config(merged)
        print(f"[Config] В {CONFIG_PATH.name} добавлены недостающие параметры: {', '.join(missing_keys)}")

    return merged


def _parse_int_map(raw: dict[str, Any]) -> dict[int, int]:
    return {int(k): int(v) for k, v in raw.items()}


def _parse_float_map(raw: dict[str, Any]) -> dict[int, float]:
    return {int(k): float(v) for k, v in raw.items()}


def _parse_scale_rules(raw: list[list[Any]]) -> list[tuple[float, int]]:
    rules: list[tuple[float, int]] = []
    for limit, step in raw:
        limit_val = float("inf") if limit is None or str(limit).lower() == "inf" else float(limit)
        rules.append((limit_val, int(step)))
    return rules


def normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    """Валидирует и приводит словарь настроек к каноническому виду для сохранения в JSON."""
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(data)

    symbol = str(merged["SYMBOL"]).strip().upper()
    if not symbol:
        raise ValueError("Поле SYMBOL не может быть пустым")

    cluster_mode = str(merged["CLUSTER_MODE"]).strip().lower()
    if cluster_mode not in ("round", "floor"):
        raise ValueError("CLUSTER_MODE должен быть 'round' или 'floor'")

    atr_map_raw = merged["ATR_LOOKBACK_DAYS"]
    if not isinstance(atr_map_raw, dict) or not atr_map_raw:
        raise ValueError("ATR_LOOKBACK_DAYS должен быть непустым словарём")
    atr_map = {str(int(k)): int(v) for k, v in atr_map_raw.items()}

    hl_map_raw = merged["HALF_LIFE_DAYS"]
    if not isinstance(hl_map_raw, dict) or not hl_map_raw:
        raise ValueError("HALF_LIFE_DAYS должен быть непустым словарём")
    hl_map = {str(int(k)): float(v) for k, v in hl_map_raw.items()}

    rules_raw = merged["SCALE_ROUNDING_RULES"]
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ValueError("SCALE_ROUNDING_RULES должен быть непустым списком правил")
    clean_rules: list[list[Any]] = []
    for item in rules_raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Каждое правило в SCALE_ROUNDING_RULES должно содержать [порог, шаг]")
        limit, step = item
        if limit is None or str(limit).strip().lower() in ("", "none", "null", "inf", "∞"):
            limit_clean: int | float | None = None
        else:
            num = float(limit)
            limit_clean = int(num) if num.is_integer() else num
        step_clean = int(step)
        if step_clean <= 0:
            raise ValueError("Шаг округления в SCALE_ROUNDING_RULES должен быть > 0")
        clean_rules.append([limit_clean, step_clean])

    return {
        "SYMBOL": symbol,
        "SPOT_CLUSTERS": max(1, int(merged["SPOT_CLUSTERS"])),
        "FUTURES_CLUSTERS": max(1, int(merged["FUTURES_CLUSTERS"])),
        "DAYS_BINANCE_1H": max(1, int(merged["DAYS_BINANCE_1H"])),
        "DAYS_BINANCE_15M": max(1, int(merged["DAYS_BINANCE_15M"])),
        "DAYS_BYBIT_15M": max(1, int(merged["DAYS_BYBIT_15M"])),
        "DAYS_BYBIT_5M": max(1, int(merged["DAYS_BYBIT_5M"])),
        "CLUSTER_MODE": cluster_mode,
        "ATR_LOOKBACK_DAYS": atr_map,
        "ATR_WINSOR_PCT": float(merged["ATR_WINSOR_PCT"]),
        "SCALE_ROUNDING_RULES": clean_rules,
        "HALF_LIFE_DAYS": hl_map,
        "OUTLIER_DAY_MAX_RATIO": float(merged["OUTLIER_DAY_MAX_RATIO"]),
        "OUTLIER_DAY_MIN_WEIGHT": float(merged["OUTLIER_DAY_MIN_WEIGHT"]),
        "DOWNLOAD_WORKERS": max(1, int(merged["DOWNLOAD_WORKERS"])),
        "TZ_OFFSET_HOURS": int(merged["TZ_OFFSET_HOURS"]),
        "CONSOLE_TOP_ROWS": max(0, int(merged["CONSOLE_TOP_ROWS"])),
        "SAVE_SORTED_CSV": bool(merged["SAVE_SORTED_CSV"]),
    }


def load_raw_config() -> dict[str, Any]:
    """Возвращает актуальный словарь настроек из config.json (для редактора в UI)."""
    return _load_or_create_config()


def save_raw_config(data: dict[str, Any]) -> dict[str, Any]:
    """Валидирует, сохраняет config.json и обновляет глобальные переменные модуля."""
    clean = normalize_config(data)
    _save_config(clean)
    _apply_globals(clean)
    return clean


def reset_to_defaults() -> dict[str, Any]:
    """Сбрасывает config.json к значениям по умолчанию."""
    defaults = copy.deepcopy(DEFAULT_CONFIG)
    _save_config(defaults)
    _apply_globals(defaults)
    return defaults


def _apply_globals(cfg: dict[str, Any]) -> None:
    global _CFG
    global SYMBOL, SPOT_CLUSTERS, FUTURES_CLUSTERS
    global DAYS_BINANCE_1H, DAYS_BINANCE_15M, DAYS_BYBIT_15M, DAYS_BYBIT_5M
    global CLUSTER_MODE, ATR_LOOKBACK_DAYS, ATR_WINSOR_PCT, SCALE_ROUNDING_RULES
    global HALF_LIFE_DAYS, OUTLIER_DAY_MAX_RATIO, OUTLIER_DAY_MIN_WEIGHT
    global DOWNLOAD_WORKERS, TZ_OFFSET_HOURS, CONSOLE_TOP_ROWS, SAVE_SORTED_CSV

    _CFG = cfg
    SYMBOL = str(cfg["SYMBOL"]).strip().upper()
    SPOT_CLUSTERS = int(cfg["SPOT_CLUSTERS"])
    FUTURES_CLUSTERS = int(cfg["FUTURES_CLUSTERS"])

    DAYS_BINANCE_1H = int(cfg["DAYS_BINANCE_1H"])
    DAYS_BINANCE_15M = int(cfg["DAYS_BINANCE_15M"])
    DAYS_BYBIT_15M = int(cfg["DAYS_BYBIT_15M"])
    DAYS_BYBIT_5M = int(cfg["DAYS_BYBIT_5M"])

    CLUSTER_MODE = str(cfg["CLUSTER_MODE"]).strip().lower()

    ATR_LOOKBACK_DAYS = _parse_int_map(cfg["ATR_LOOKBACK_DAYS"])
    ATR_WINSOR_PCT = float(cfg["ATR_WINSOR_PCT"])
    SCALE_ROUNDING_RULES = _parse_scale_rules(cfg["SCALE_ROUNDING_RULES"])

    HALF_LIFE_DAYS = _parse_float_map(cfg["HALF_LIFE_DAYS"])
    OUTLIER_DAY_MAX_RATIO = float(cfg["OUTLIER_DAY_MAX_RATIO"])
    OUTLIER_DAY_MIN_WEIGHT = float(cfg["OUTLIER_DAY_MIN_WEIGHT"])

    DOWNLOAD_WORKERS = int(cfg["DOWNLOAD_WORKERS"])
    TZ_OFFSET_HOURS = int(cfg["TZ_OFFSET_HOURS"])
    CONSOLE_TOP_ROWS = int(cfg["CONSOLE_TOP_ROWS"])
    SAVE_SORTED_CSV = bool(cfg["SAVE_SORTED_CSV"])


# ===================== ЗАГРУЗКА ПАРАМЕТРОВ =====================
_CFG: dict[str, Any] = {}
SYMBOL: str = "BTCUSDT"
SPOT_CLUSTERS: int = 15
FUTURES_CLUSTERS: int = 30
DAYS_BINANCE_1H: int = 60
DAYS_BINANCE_15M: int = 30
DAYS_BYBIT_15M: int = 30
DAYS_BYBIT_5M: int = 14
CLUSTER_MODE: str = "round"
ATR_LOOKBACK_DAYS: dict[int, int] = {}
ATR_WINSOR_PCT: float = 95.0
SCALE_ROUNDING_RULES: list[tuple[float, int]] = []
HALF_LIFE_DAYS: dict[int, float] = {}
OUTLIER_DAY_MAX_RATIO: float = 2.5
OUTLIER_DAY_MIN_WEIGHT: float = 0.15
DOWNLOAD_WORKERS: int = 4
TZ_OFFSET_HOURS: int = 0
CONSOLE_TOP_ROWS: int = 0
SAVE_SORTED_CSV: bool = False

_apply_globals(_load_or_create_config())