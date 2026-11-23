"""Короткие читаемые подписи режимов для графика и правой ленты."""

from __future__ import annotations

REGIME_SHORT: dict[str, str] = {
    "LONG_BUILD": "+Лонг",
    "LONG_CLOSE": "-Лонг",
    "SHORT_BUILD": "+Шорт",
    "SHORT_CLOSE": "-Шорт",
}

ABSORPTION_MARK = "⚠"


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
