import os
import sys
from pathlib import Path

# Глушим лишние служебные сообщения Chromium в консоли
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--log-level=3")

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

# Импорт QtWebEngineWidgets до создания QApplication гарантирует корректную инициализацию контекста
try:
    import PySide6.QtWebEngineWidgets  # noqa: F401
except ImportError:
    pass

from .ui.main_window import MainWindow
from .ui.theme import APP_STYLESHEET, FONT_FAMILY_UI


def _get_icon_path() -> Path | None:
    """Находит icon.png в сборке PyInstaller (_MEIPASS / рядом с .exe) или в корне проекта."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "icon.png")
        candidates.append(Path(sys.executable).resolve().parent / "icon.png")
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "icon.png")
        candidates.append(Path(__file__).resolve().parent / "icon.png")

    for path in candidates:
        if path.is_file():
            return path
    return None


def start_gui() -> None:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("Volume Kit")
    app.setApplicationDisplayName("Volume Kit")
    app.setOrganizationName("Volume Kit")
    app.setStyle("Fusion")

    app_icon = QIcon()
    icon_path = _get_icon_path()
    if icon_path is not None:
        app_icon = QIcon(str(icon_path))
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

    base_font = QFont(FONT_FAMILY_UI, 10)
    base_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(base_font)

    app.setStyleSheet(APP_STYLESHEET)

    window = MainWindow()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.showMaximized()

    sys.exit(app.exec())