"""Единая точка запуска GUI Volume Kit."""

import logging
import os
import sys

# Глушим лишние служебные сообщения Chromium в консоли
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--log-level=3")

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
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
from app.ui.proxy_web import apply_qt_proxy_from_manager
from app.ui.theme import APP_STYLESHEET, FONT_FAMILY_UI


def _apply_light_appearance(app: QApplication) -> None:
    """Фиксирует светлую схему Qt, чтобы тёмная тема Windows не ломала попапы и меню."""
    style_hints = app.styleHints()
    if hasattr(style_hints, "setColorScheme"):
        style_hints.setColorScheme(Qt.ColorScheme.Light)

    text = QColor("#0F172A")
    muted = QColor("#94A3B8")
    white = QColor("#FFFFFF")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#F4F6F9"))
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, white)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#F1F5F9"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, white)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, white)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, white)
    palette.setColor(QPalette.ColorRole.Link, QColor("#2563EB"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563EB"))
    palette.setColor(QPalette.ColorRole.HighlightedText, white)
    palette.setColor(QPalette.ColorRole.PlaceholderText, muted)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)

    app.setPalette(palette)


def start_gui() -> None:
    configure_logging(console=True)

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("Volume Kit")
    app.setApplicationDisplayName("Volume Kit")
    app.setOrganizationName("Volume Kit")
    app.setStyle("Fusion")
    _apply_light_appearance(app)

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

    proxy_warning = apply_qt_proxy_from_manager()
    if proxy_warning:
        logging.getLogger(__name__).warning("Прокси для Web-вкладок: %s", proxy_warning)

    registry = register_builtin_tools()
    window = MainWindow(registry, get_config_service())
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.showMaximized()

    sys.exit(app.exec())
