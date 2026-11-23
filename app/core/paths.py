"""Централизованное разрешение путей к данным и ресурсам приложения."""

import sys
from pathlib import Path

_IS_FROZEN = bool(getattr(sys, "frozen", False))

# В собранном .exe (PyInstaller / Nuitka) файлы и папки лежат рядом с самим .exe,
# а в обычном режиме — в папке app/ (чтобы не потерять текущий data_cache)
if _IS_FROZEN:
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parents[1]

CONFIG_PATH = BASE_DIR / "config.json"
CACHE_DIR = BASE_DIR / "data_cache"
OUTPUT_DIR = BASE_DIR / "output"
WEB_PROFILE_DIR = BASE_DIR / "web_profile"
LOGS_DIR = BASE_DIR / "logs"


def _assets_candidates() -> list[Path]:
    """Возвращает каталоги-кандидаты для ресурсов (assets) в порядке приоритета."""
    candidates: list[Path] = []
    if _IS_FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "assets")
        candidates.append(Path(sys.executable).resolve().parent / "assets")
    else:
        candidates.append(Path(__file__).resolve().parents[2] / "assets")
    return candidates


def assets_dir() -> Path:
    """Возвращает первый существующий каталог ресурсов, иначе первый кандидат."""
    candidates = _assets_candidates()
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[0]


def icon_path() -> Path | None:
    """Находит icon.png в assets/ (dev / PyInstaller) или в устаревших местах."""
    candidates: list[Path] = [directory / "icon.png" for directory in _assets_candidates()]
    if _IS_FROZEN:
        candidates.append(BASE_DIR / "icon.png")
    else:
        legacy_root = Path(__file__).resolve().parents[2]
        candidates.append(legacy_root / "icon.png")
        candidates.append(legacy_root / "app" / "icon.png")

    for path in candidates:
        if path.is_file():
            return path
    return None


def logo_path() -> Path | None:
    """Находит logo.svg в assets/ (dev / PyInstaller)."""
    for directory in _assets_candidates():
        path = directory / "logo.svg"
        if path.is_file():
            return path
    return None
