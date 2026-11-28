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
SPOT_STATUS_PASSIVE = "PASSIVE"
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
    absorption_effort_range_ratio: float = 0.75
    spot_sync_min_z: float = 1.0
    spot_div_min_z: float = 0.8
    spot_dead_max_z: float = 0.3
    spot_dead_extreme_oi_z: float = 2.5
    multi_bar_window: int = 4
    multi_bar_threshold_oi: float = 2.2
    multi_bar_threshold_delta: float = 1.8
    trap_level_min_z: float = 2.5
    use_bybit: bool = True
    use_okx: bool = True
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

    # ---- расширенный контекст (Этап 1.2) ----
    delta_oi_pct: float | None = None
    delta_oi_by_exchange: dict[str, float] = field(default_factory=dict)
    oi_start_by_exchange: dict[str, float] = field(default_factory=dict)
    oi_end_by_exchange: dict[str, float] = field(default_factory=dict)
    dominant_exchange: str | None = None
    dominant_share: float | None = None
    is_single_exchange_led: bool = False
    live_progress: float = 1.0
    range_ratio: float | None = None
    is_standalone_absorption: bool = False
    basis_pct: float | None = None
    funding_rate: float | None = None
    multi_regime_key: str | None = None
    z_multi_doi: float | None = None
    z_multi_delta: float | None = None
    # ---- волатильность: Wilder ATR(14) по закрытым свечам ----
    atr14: float | None = None
    atr14_pct: float | None = None
    range_atr: float | None = None


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
    spot_status: str
    is_absorption: bool
    absorption_reason: str | None
    is_live: bool

    # ---- контекст события (Этап 1.2) ----
    delta_oi_pct: float | None = None
    delta_oi_by_exchange: dict[str, float] = field(default_factory=dict)
    dominant_exchange: str | None = None
    dominant_share: float | None = None
    is_single_exchange_led: bool = False
    live_progress: float = 1.0
    basis_pct: float | None = None
    funding_rate: float | None = None
    is_standalone_absorption: bool = False
    atr14: float | None = None
    range_atr: float | None = None

    @property
    def strength(self) -> float:
        """Сила события для сортировки и отображения."""
        return max(abs(self.z_doi), abs(self.z_fut_delta))


@dataclass
class TrapLevel:
    """Проекция уровня «застрявших» участников по сильной свече."""

    start_index: int
    end_index: int
    open_time_ms: int
    price_low: float
    price_high: float
    price_mid: float
    side: str  # "SUPPORT" | "RESISTANCE"
    regime_key: str
    strength: float
    is_mitigated: bool = False


@dataclass(frozen=True)
class MultiBarZone:
    """Непрерывная зона реального многобарного накопления/разгрузки."""

    start_index: int
    end_index: int
    start_time_ms: int
    end_time_ms: int
    regime_key: str
    regime_label: str
    color: str
    bar_count: int
    sum_delta_oi: float
    sum_delta_oi_pct: float | None
    sum_fut_delta: float
    peak_z_doi: float
    peak_z_delta: float
    price_low: float
    price_high: float


@dataclass(frozen=True)
class OutcomeHorizonRow:
    """Медианная отработка события на одном горизонте закрытых баров."""

    horizon: int
    n: int
    mfe_pct: float | None = None
    mae_pct: float | None = None
    close_pct: float | None = None


@dataclass
class RegimeOutcomeRow:
    """Статистика отработки для одной группы событий (режим/поглощение)."""

    regime_key: str
    label: str
    count: int
    horizons: list[OutcomeHorizonRow] = field(default_factory=list)


@dataclass
class OutcomeStats:
    """Сводная статистика отработки по текущим параметрам расчёта."""

    rows: list[RegimeOutcomeRow] = field(default_factory=list)
    generated_bars: int = 0


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
    active_exchanges: list[str] = field(default_factory=lambda: ["binance"])
    sources_label: str = "Binance only"
    current_atr14: float | None = None
    current_atr14_pct: float | None = None
    warnings: list[str] = field(default_factory=list)
    updated_at: str = ""
    updated_at_ms: int = 0
    step_ms: int = 3_600_000
    live_open_time_ms: int | None = None
    trap_levels: list[TrapLevel] = field(default_factory=list)
    multi_zones: list[MultiBarZone] = field(default_factory=list)
    outcome_stats: OutcomeStats = field(default_factory=OutcomeStats)
    oi_unavailable_before_index: int | None = None

    @property
    def last_price(self) -> float | None:
        """Цена последней свечи (для статуса/подсказок)."""
        return self.bars[-1].close if self.bars else None
