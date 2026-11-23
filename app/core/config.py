"""Настройки приложения: дефолты, валидация и сервис доступа к config.json."""

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.paths import CONFIG_PATH

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
    # OI & Delta Regime
    "OI_DEFAULT_TIMEFRAME": "1h",
    "OI_Z_WINDOW": 60,
    "OI_THRESHOLD_OI": 2.0,
    "OI_THRESHOLD_DELTA": 1.5,
    "OI_ABSORPTION_CLOSE_RATIO": 0.35,
    "OI_ABSORPTION_MIN_Z_DELTA": 1.5,
    "OI_SPOT_SYNC_MIN_Z": 1.0,
    "OI_SPOT_DIV_MIN_Z": 0.8,
    "OI_SPOT_DEAD_MAX_Z": 0.3,
    "OI_SPOT_DEAD_EXTREME_OI_Z": 2.5,
    "OI_USE_BYBIT": True,
    "OI_VISIBLE_BARS": 200,
    "OI_REFRESH_SEC": 20,
    # OI & Delta Regime: состояние тулбара (единый источник правды для страницы настроек)
    "OI_ONLY_ABSORPTION": False,
    "OI_HIDE_WEAK_NO_SPOT": False,
    "OI_SHOW_Z_SUBPLOT": True,
    "OI_LIVE_ENABLED": True,
}


def _clamp(value: float, low: float, high: float) -> float:
    """Ограничивает значение диапазоном [low, high]."""
    return max(low, min(high, value))


def _write_config(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _load_or_create_config(config_path: Path) -> dict[str, Any]:
    """Читает config.json рядом с приложением; при отсутствии создаёт файл с дефолтами."""
    if not config_path.exists():
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        _write_config(config_path, defaults)
        print(f"[Config] Создан файл настроек по умолчанию: {config_path}")
        return defaults

    try:
        raw_text = config_path.read_text(encoding="utf-8")
        user_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ошибка синтаксиса в {config_path} (строка {exc.lineno}, столбец {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(user_data, dict):
        raise RuntimeError(f"Файл {config_path} должен содержать JSON-объект {{...}}")

    # Подмешиваем недостающие ключи из дефолтов (защита при обновлениях или случайном удалении поля)
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(user_data)

    missing_keys = [k for k in DEFAULT_CONFIG if k not in user_data]
    if missing_keys:
        _write_config(config_path, merged)
        print(f"[Config] В {config_path.name} добавлены недостающие параметры: {', '.join(missing_keys)}")

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

    oi_timeframe = str(merged["OI_DEFAULT_TIMEFRAME"]).strip().lower()
    if oi_timeframe not in ("5m", "15m", "1h", "4h"):
        raise ValueError("OI_DEFAULT_TIMEFRAME должен быть одним из: 5m, 15m, 1h, 4h")

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
        "OI_DEFAULT_TIMEFRAME": oi_timeframe,
        "OI_Z_WINDOW": int(_clamp(int(merged["OI_Z_WINDOW"]), 15, 200)),
        "OI_THRESHOLD_OI": float(_clamp(float(merged["OI_THRESHOLD_OI"]), 0.5, 6.0)),
        "OI_THRESHOLD_DELTA": float(_clamp(float(merged["OI_THRESHOLD_DELTA"]), 0.5, 6.0)),
        "OI_ABSORPTION_CLOSE_RATIO": float(
            _clamp(float(merged["OI_ABSORPTION_CLOSE_RATIO"]), 0.10, 0.49)
        ),
        "OI_ABSORPTION_MIN_Z_DELTA": float(
            _clamp(float(merged["OI_ABSORPTION_MIN_Z_DELTA"]), 0.5, 5.0)
        ),
        "OI_SPOT_SYNC_MIN_Z": float(_clamp(float(merged["OI_SPOT_SYNC_MIN_Z"]), 0.3, 4.0)),
        "OI_SPOT_DIV_MIN_Z": float(_clamp(float(merged["OI_SPOT_DIV_MIN_Z"]), 0.2, 4.0)),
        "OI_SPOT_DEAD_MAX_Z": float(_clamp(float(merged["OI_SPOT_DEAD_MAX_Z"]), 0.05, 1.5)),
        "OI_SPOT_DEAD_EXTREME_OI_Z": float(
            _clamp(float(merged["OI_SPOT_DEAD_EXTREME_OI_Z"]), 1.0, 6.0)
        ),
        "OI_USE_BYBIT": bool(merged["OI_USE_BYBIT"]),
        "OI_VISIBLE_BARS": int(_clamp(int(merged["OI_VISIBLE_BARS"]), 60, 400)),
        "OI_REFRESH_SEC": int(_clamp(int(merged["OI_REFRESH_SEC"]), 0, 120)),
        "OI_ONLY_ABSORPTION": bool(merged["OI_ONLY_ABSORPTION"]),
        "OI_HIDE_WEAK_NO_SPOT": bool(merged["OI_HIDE_WEAK_NO_SPOT"]),
        "OI_SHOW_Z_SUBPLOT": bool(merged["OI_SHOW_Z_SUBPLOT"]),
        "OI_LIVE_ENABLED": bool(merged["OI_LIVE_ENABLED"]),
    }


@dataclass(frozen=True)
class AppSettings:
    """Иммутабельный снимок настроек, используемый расчётными модулями."""

    config_path: Path
    symbol: str
    spot_clusters: int
    futures_clusters: int
    days_binance_1h: int
    days_binance_15m: int
    days_bybit_15m: int
    days_bybit_5m: int
    cluster_mode: str
    atr_lookback_days: dict[int, int]
    atr_winsor_pct: float
    scale_rounding_rules: tuple[tuple[float, int], ...]
    half_life_days: dict[int, float]
    outlier_day_max_ratio: float
    outlier_day_min_weight: float
    download_workers: int
    tz_offset_hours: int
    console_top_rows: int
    save_sorted_csv: bool
    oi_default_timeframe: str
    oi_z_window: int
    oi_threshold_oi: float
    oi_threshold_delta: float
    oi_absorption_close_ratio: float
    oi_absorption_min_z_delta: float
    oi_spot_sync_min_z: float
    oi_spot_div_min_z: float
    oi_spot_dead_max_z: float
    oi_spot_dead_extreme_oi_z: float
    oi_use_bybit: bool
    oi_visible_bars: int
    oi_refresh_sec: int
    oi_only_absorption: bool
    oi_hide_weak_no_spot: bool
    oi_show_z_subplot: bool
    oi_live_enabled: bool
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, cfg: dict[str, Any], config_path: Path) -> "AppSettings":
        """Собирает снимок настроек из «сырого» словаря config.json."""
        merged = copy.deepcopy(DEFAULT_CONFIG)
        merged.update(cfg)
        return cls(
            config_path=config_path,
            symbol=str(merged["SYMBOL"]).strip().upper(),
            spot_clusters=int(merged["SPOT_CLUSTERS"]),
            futures_clusters=int(merged["FUTURES_CLUSTERS"]),
            days_binance_1h=int(merged["DAYS_BINANCE_1H"]),
            days_binance_15m=int(merged["DAYS_BINANCE_15M"]),
            days_bybit_15m=int(merged["DAYS_BYBIT_15M"]),
            days_bybit_5m=int(merged["DAYS_BYBIT_5M"]),
            cluster_mode=str(merged["CLUSTER_MODE"]).strip().lower(),
            atr_lookback_days=_parse_int_map(merged["ATR_LOOKBACK_DAYS"]),
            atr_winsor_pct=float(merged["ATR_WINSOR_PCT"]),
            scale_rounding_rules=tuple(_parse_scale_rules(merged["SCALE_ROUNDING_RULES"])),
            half_life_days=_parse_float_map(merged["HALF_LIFE_DAYS"]),
            outlier_day_max_ratio=float(merged["OUTLIER_DAY_MAX_RATIO"]),
            outlier_day_min_weight=float(merged["OUTLIER_DAY_MIN_WEIGHT"]),
            download_workers=int(merged["DOWNLOAD_WORKERS"]),
            tz_offset_hours=int(merged["TZ_OFFSET_HOURS"]),
            console_top_rows=int(merged["CONSOLE_TOP_ROWS"]),
            save_sorted_csv=bool(merged["SAVE_SORTED_CSV"]),
            oi_default_timeframe=str(merged["OI_DEFAULT_TIMEFRAME"]).strip().lower(),
            oi_z_window=int(merged["OI_Z_WINDOW"]),
            oi_threshold_oi=float(merged["OI_THRESHOLD_OI"]),
            oi_threshold_delta=float(merged["OI_THRESHOLD_DELTA"]),
            oi_absorption_close_ratio=float(merged["OI_ABSORPTION_CLOSE_RATIO"]),
            oi_absorption_min_z_delta=float(merged["OI_ABSORPTION_MIN_Z_DELTA"]),
            oi_spot_sync_min_z=float(merged["OI_SPOT_SYNC_MIN_Z"]),
            oi_spot_div_min_z=float(merged["OI_SPOT_DIV_MIN_Z"]),
            oi_spot_dead_max_z=float(merged["OI_SPOT_DEAD_MAX_Z"]),
            oi_spot_dead_extreme_oi_z=float(merged["OI_SPOT_DEAD_EXTREME_OI_Z"]),
            oi_use_bybit=bool(merged["OI_USE_BYBIT"]),
            oi_visible_bars=int(merged["OI_VISIBLE_BARS"]),
            oi_refresh_sec=int(merged["OI_REFRESH_SEC"]),
            oi_only_absorption=bool(merged["OI_ONLY_ABSORPTION"]),
            oi_hide_weak_no_spot=bool(merged["OI_HIDE_WEAK_NO_SPOT"]),
            oi_show_z_subplot=bool(merged["OI_SHOW_Z_SUBPLOT"]),
            oi_live_enabled=bool(merged["OI_LIVE_ENABLED"]),
            raw=merged,
        )


class ConfigService:
    """Единая точка чтения и записи config.json."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path is not None else CONFIG_PATH
        self._current: AppSettings = self.reload()

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def current(self) -> AppSettings:
        return self._current

    def reload(self) -> AppSettings:
        """Перечитывает config.json и обновляет текущий снимок настроек."""
        raw = self.load_raw()
        self._current = AppSettings.from_raw(raw, self._config_path)
        return self._current

    def load_raw(self) -> dict[str, Any]:
        """Возвращает актуальный словарь настроек из config.json (для редактора в UI)."""
        return _load_or_create_config(self._config_path)

    def save_raw(self, data: dict[str, Any]) -> dict[str, Any]:
        """Валидирует, сохраняет config.json и обновляет текущий снимок настроек."""
        clean = normalize_config(data)
        _write_config(self._config_path, clean)
        self._current = AppSettings.from_raw(clean, self._config_path)
        return clean

    def patch_raw(self, updates: dict[str, Any]) -> AppSettings:
        """Частично обновляет config.json: меняет только переданные ключи.

        Остальные секции (SYMBOL, настройки Calibrator и т.д.) сохраняются как есть,
        пересчёт Calibrator не запускается.
        """
        raw = self.load_raw()
        raw.update({str(key): value for key, value in updates.items()})
        clean = normalize_config(raw)
        _write_config(self._config_path, clean)
        self._current = AppSettings.from_raw(clean, self._config_path)
        return self._current

    def reset_defaults(self) -> dict[str, Any]:
        """Сбрасывает config.json к значениям по умолчанию."""
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        _write_config(self._config_path, defaults)
        self._current = AppSettings.from_raw(defaults, self._config_path)
        return defaults


_default_service: ConfigService | None = None


def get_config_service() -> ConfigService:
    """Возвращает процесс-синглтон сервиса настроек."""
    global _default_service
    if _default_service is None:
        _default_service = ConfigService()
    return _default_service
