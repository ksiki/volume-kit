"""Команды разработки Volume Kit: запуск и сборка (аналог Makefile для Windows)."""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "VolumeKit"
RUN_SCRIPT = ROOT / "run.py"
SPEC_FILE = ROOT / "VolumeKit.spec"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
ICON_PNG = ROOT / "assets" / "icon.png"
ICON_ICO = ROOT / "assets" / "icon.ico"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _venv_python() -> Path | None:
    """Возвращает интерпретатор локального .venv или None с понятным сообщением."""
    if os.name == "nt":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"

    if candidate.is_file():
        return candidate

    print("[dev] Не найден локальный .venv.", file=sys.stderr)
    print("[dev] Создайте окружение: python -m venv .venv", file=sys.stderr)
    return None


def _ensure_icon() -> bool:
    """Генерирует assets/icon.ico из assets/icon.png (PNG-сжатый ICO, без Pillow)."""
    if not ICON_PNG.is_file():
        print(f"[dev] Не найден файл иконки: {ICON_PNG}", file=sys.stderr)
        return False

    if ICON_ICO.is_file() and ICON_ICO.stat().st_mtime >= ICON_PNG.stat().st_mtime:
        return True

    payload = ICON_PNG.read_bytes()
    if not payload.startswith(_PNG_SIGNATURE) or len(payload) < 24:
        print(f"[dev] Файл {ICON_PNG.name} не является корректным PNG.", file=sys.stderr)
        return False

    width, height = struct.unpack(">II", payload[16:24])
    if width <= 0 or height <= 0 or width > 256 or height > 256:
        print(
            f"[dev] Неподдерживаемый размер {ICON_PNG.name}: {width}x{height} (нужно до 256x256).",
            file=sys.stderr,
        )
        return False

    size_byte = 0 if width >= 256 else width
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        size_byte,
        size_byte,
        0,
        0,
        1,
        32,
        len(payload),
        len(header) + 16,
    )
    ICON_ICO.write_bytes(header + entry + payload)
    print(f"[dev] Создана иконка: {ICON_ICO.relative_to(ROOT)} ({width}x{height})")
    return True


def _pyinstaller_available(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-c", "import PyInstaller"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _clean_artifacts() -> None:
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            print(f"[dev] Очищено: {path.relative_to(ROOT)}")


def cmd_run(extra_args: list[str]) -> int:
    """Запускает приложение через интерпретатор .venv."""
    python = _venv_python()
    if python is None:
        return 1

    if not RUN_SCRIPT.is_file():
        print(f"[dev] Не найден {RUN_SCRIPT.name}.", file=sys.stderr)
        return 1

    return subprocess.run([str(python), str(RUN_SCRIPT), *extra_args], cwd=ROOT).returncode


def cmd_build() -> int:
    """Собирает onedir-сборку dist/VolumeKit/VolumeKit.exe по VolumeKit.spec."""
    python = _venv_python()
    if python is None:
        return 1

    if not _pyinstaller_available(python):
        print("[dev] PyInstaller не найден в .venv.", file=sys.stderr)
        print(
            "[dev] Установите: .venv\\Scripts\\python.exe -m pip install pyinstaller",
            file=sys.stderr,
        )
        return 1

    if not SPEC_FILE.is_file():
        print(f"[dev] Не найден файл конфигурации сборки: {SPEC_FILE.name}", file=sys.stderr)
        return 1

    if not _ensure_icon():
        return 1

    _clean_artifacts()
    print("[dev] Сборка VolumeKit (onedir, windowed)...")

    result = subprocess.run(
        [str(python), "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_FILE)],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print(f"[dev] Сборка завершилась с ошибкой (код {result.returncode}).", file=sys.stderr)
        return result.returncode

    exe_path = DIST_DIR / APP_NAME / f"{APP_NAME}.exe"
    if not exe_path.is_file():
        print(f"[dev] Ожидаемый файл не найден: {exe_path}", file=sys.stderr)
        return 1

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print("[dev] Сборка завершена.")
    print(f"[dev] Готовое приложение: {exe_path} ({size_mb:.1f} МБ)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev",
        description="Команды разработки Volume Kit (использует интерпретатор из .venv).",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Запустить приложение (run.py)")
    run_parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Дополнительные аргументы run.py, например --cli",
    )

    subparsers.add_parser("build", help="Собрать dist/VolumeKit/VolumeKit.exe")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return cmd_run(list(args.args))
    if args.command == "build":
        return cmd_build()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
