"""Короткие читаемые подписи режимов для графика и правой ленты."""

from __future__ import annotations

REGIME_SHORT: dict[str, str] = {
    "LONG_BUILD": "+Лонг",
    "LONG_CLOSE": "-Лонг",
    "SHORT_BUILD": "+Шорт",
    "SHORT_CLOSE": "-Шорт",
}

# Компактные коды для плотных кластеров на графике.
REGIME_COMPACT: dict[str, str] = {
    "LONG_BUILD": "L+",
    "LONG_CLOSE": "L−",
    "SHORT_BUILD": "S+",
    "SHORT_CLOSE": "S−",
}

REGIME_ICON: dict[str, str] = {
    "LONG_BUILD": "▲",
    "LONG_CLOSE": "✕",
    "SHORT_BUILD": "▼",
    "SHORT_CLOSE": "✕",
}

ABSORPTION_MARK = "⚠"

SPOT_SHORT: dict[str, str] = {
    "SYNC": "✓ Спот",
    "DIV": "⚡ Диверг.",
    "PASSIVE": "○ Слабый спот",
    "NEUTRAL": "· Нейтр.",
    "UNAVAILABLE": "— Спот",
}

# Понятные названия цельных многобарных зон.
MULTI_ZONE_TITLE: dict[str, str] = {
    "LONG_BUILD": "Набор лонгов",
    "LONG_CLOSE": "Сброс лонгов",
    "SHORT_BUILD": "Набор шортов",
    "SHORT_CLOSE": "Сброс шортов",
}

MULTI_ZONE_SHORT: dict[str, str] = {
    "LONG_BUILD": "Набор Л",
    "LONG_CLOSE": "Сброс Л",
    "SHORT_BUILD": "Набор Ш",
    "SHORT_CLOSE": "Сброс Ш",
}


def short_regime_label(regime_key: str | None, fallback: str | None = "") -> str:
    """Короткая подпись режима по ключу (fallback — исходный текст)."""
    if regime_key:
        label = REGIME_SHORT.get(regime_key)
        if label:
            return label
    return (fallback or "").strip()


def regime_badge_text(
    regime_key: str | None,
    regime_label: str | None,
    is_absorption: bool = False,
) -> str:
    """Текст бейджа: короткая подпись режима + значок поглощения."""
    text = short_regime_label(regime_key, regime_label)
    if is_absorption and text:
        return f"{ABSORPTION_MARK} {text}"
    return text


def regime_compact_text(
    regime_key: str | None,
    is_absorption: bool = False,
) -> str:
    """Компактный код режима для плотных кластеров (L+/S−)."""
    code = REGIME_COMPACT.get(regime_key or "", "")
    if not code:
        return ""
    if is_absorption:
        return f"⚠{code}"
    return code


def multi_zone_title(regime_key: str | None) -> str:
    """Понятное название многобарной зоны: «Набор лонгов» и т.п."""
    if not regime_key:
        return "Мульти-бар"
    return MULTI_ZONE_TITLE.get(regime_key, "Мульти-бар")


def multi_zone_short(regime_key: str | None) -> str:
    """Компактное название зоны для верхней дорожки: «Набор Л» и т.п."""
    if not regime_key:
        return "Мульти-бар"
    return MULTI_ZONE_SHORT.get(regime_key, "Мульти-бар")


def spot_short_label(status: str) -> str:
    """Короткая подпись статуса спота."""
    return SPOT_SHORT.get(status, "· Нейтр.")
