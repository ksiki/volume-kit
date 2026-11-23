"""Модели данных инструмента OI & Delta Regime (без зависимостей от Qt)."""

from dataclasses import dataclass, field

REGIME_META: dict[str, tuple[str, str, str]] = {
    "LONG_BUILD": ("Покупатель набирает позицию", "▲ L+", "#10B981"),
    "LONG_CLOSE": ("Покупатель закрывает позицию", "✕ L-", "#F97316"),
    "SHORT_CLOSE": ("Продавец закрывает позицию", "✕ S-", "#06B6D4"),
    "SHORT_BUILD": ("Продавец набирает позицию", "▼ S+", "#E11D48"),
}

SPOT_STATUS_SYNC = "SYNC"
SPOT_STATUS_DIV = "DIV"
SPOT_STATUS_NEUTRAL = "NEUTRAL"
SPOT_STATUS_UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RegimeParams:
    """Параметры расчёта и отображения матрицы аномалий."""

    timeframe: str = "1h"
    z_window: int = 60
    threshold_oi: float = 2.0
    threshold_delta: float = 1.5
    absorption_close_ratio: float = 0.35
    absorption_min_z_delta: float = 1.5
    spot_sync_min_z: float = 1.0
    spot_div_min_z: float = 0.8
    spot_dead_max_z: float = 0.3
    spot_dead_extreme_oi_z: float = 2.5
    use_bybit: bool = True
    visible_bars: int = 200
    tz_offset_hours: int = 0


@dataclass
class CandleBar:
    """Одна свеча с рассчитанными дельтами, OI, Z-score и контекстными тегами."""

    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume_base: float
    taker_buy_base: float
    fut_delta: float
    spot_available: bool
    spot_delta: float | None
    oi_binance_start: float | None
    oi_binance_end: float | None
    oi_bybit_start: float | None
    oi_bybit_end: float | None
    delta_oi_binance: float | None
    delta_oi_bybit: float | None
    delta_oi_total: float | None
    is_live: bool
    live_start_estimated: bool
    is_warmup: bool
    has_oi: bool
    z_doi: float | None
    z_fut_delta: float | None
    z_spot_delta: float | None
    regime_key: str | None
    regime_label: str | None
    marker: str | None
    color: str | None
    is_anomaly: bool
    is_absorption: bool
    absorption_side: str | None
    absorption_reason: str | None
    spot_status: str
    is_squeeze: bool = False
    liq_volume_base: float | None = None


@dataclass
class AnomalyEvent:
    """Событие аномалии для правой ленты и навигации по графику."""

    bar_index: int
    open_time_ms: int
    timeframe: str
    regime_key: str
    regime_label: str
    marker: str
    color: str
    z_doi: float
    z_fut_delta: float
    z_spot_delta: float | None
    fut_delta: float
    spot_delta: float | None
    delta_oi_total: float | None
    delta_oi_binance: float | None
    delta_oi_bybit: float | None
    spot_status: str
    is_absorption: bool
    absorption_reason: str | None
    is_live: bool
    is_squeeze: bool = False
    liq_volume_base: float | None = None

    @property
    def strength(self) -> float:
        """Сила события для сортировки и отображения."""
        return max(abs(self.z_doi), abs(self.z_fut_delta))


@dataclass
class RegimeBundle:
    """Полный результат загрузки и расчёта, готовый для отрисовки."""

    symbol: str
    base_asset: str
    timeframe: str
    params: RegimeParams
    bars: list[CandleBar] = field(default_factory=list)
    events: list[AnomalyEvent] = field(default_factory=list)
    anomaly_count: int = 0
    warmup_rows: int = 0
    spot_available: bool = False
    bybit_available: bool = False
    sources_label: str = "Binance only"
    warnings: list[str] = field(default_factory=list)
    updated_at: str = ""
    updated_at_ms: int = 0
    step_ms: int = 3_600_000
    live_open_time_ms: int | None = None

    @property
    def last_price(self) -> float | None:
        """Цена последней свечи (для статуса/подсказок)."""
        return self.bars[-1].close if self.bars else None
