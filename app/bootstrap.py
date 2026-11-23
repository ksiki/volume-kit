"""Единая точка запуска GUI Volume Kit."""

import os
import sys

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

from app.core.config import get_config_service
from app.core.logging_setup import configure_logging
from app.core.paths import icon_path
from app.tools import register_builtin_tools
from app.ui.main_window import MainWindow
from app.ui.theme import APP_STYLESHEET, FONT_FAMILY_UI


def start_gui() -> None:
    configure_logging(console=True)

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("Volume Kit")
    app.setApplicationDisplayName("Volume Kit")
    app.setOrganizationName("Volume Kit")
    app.setStyle("Fusion")

    app_icon = QIcon()
    path = icon_path()
    if path is not None:
        app_icon = QIcon(str(path))
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

    base_font = QFont(FONT_FAMILY_UI, 10)
    base_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(base_font)

    app.setStyleSheet(APP_STYLESHEET)

    registry = register_builtin_tools()
    window = MainWindow(registry, get_config_service())
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.showMaximized()

    sys.exit(app.exec())
