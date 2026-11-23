"""Встроенная страница стороннего веб-инструмента с выделением нужного div."""

from app.core.paths import WEB_PROFILE_DIR
from app.tools.web.isolation_js import build_isolation_js
from app.ui.widgets import repolish


from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False


_SHARED_PROFILE: "QWebEngineProfile | None" = None


def _get_shared_profile() -> "QWebEngineProfile":
    """Общий профиль Chromium с сохранением кэша и cookies рядом с приложением."""
    global _SHARED_PROFILE
    if _SHARED_PROFILE is None:
        profile_dir = WEB_PROFILE_DIR
        profile_dir.mkdir(parents=True, exist_ok=True)

        profile = QWebEngineProfile("VolumeKitWebProfile")
        profile.setPersistentStoragePath(str(profile_dir / "storage"))
        profile.setCachePath(str(profile_dir / "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        _SHARED_PROFILE = profile
    return _SHARED_PROFILE


class WebToolPage(QWidget):
    """Встроенная страница стороннего веб-инструмента с выделением нужного div."""

    def __init__(
        self,
        title: str,
        url: str,
        target_class: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._url = url
        self._target_class = target_class
        self._loaded = False
        self._isolated = True
        self._zoom = 1.0

        self._view: "QWebEngineView | None" = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Компактный тулбар инструмента
        toolbar = QFrame()
        toolbar.setObjectName("WebToolbar")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(16, 8, 16, 8)
        tb_lay.setSpacing(8)

        lbl_url = QLabel(self._url)
        lbl_url.setObjectName("ScaleMeta")
        lbl_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._status_lbl = QLabel("Ожидание открытия...")
        self._status_lbl.setObjectName("SettingsHint")

        self._isolate_btn = QPushButton("Только график: ВКЛ")
        self._isolate_btn.setObjectName("FilterChip")
        self._isolate_btn.setProperty("active", True)
        self._isolate_btn.setToolTip(
            f"Изолировать div.{self._target_class} и скрыть остальную страницу"
        )
        self._isolate_btn.clicked.connect(self._toggle_isolation)
        repolish(self._isolate_btn)

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setObjectName("SecondaryBtn")
        btn_zoom_out.setFixedWidth(32)
        btn_zoom_out.setToolTip("Уменьшить масштаб")
        btn_zoom_out.clicked.connect(lambda: self._change_zoom(-0.1))

        self._zoom_lbl = QPushButton("100%")
        self._zoom_lbl.setObjectName("SecondaryBtn")
        self._zoom_lbl.setFixedWidth(56)
        self._zoom_lbl.setToolTip("Сбросить масштаб к 100%")
        self._zoom_lbl.clicked.connect(self._reset_zoom)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setObjectName("SecondaryBtn")
        btn_zoom_in.setFixedWidth(32)
        btn_zoom_in.setToolTip("Увеличить масштаб")
        btn_zoom_in.clicked.connect(lambda: self._change_zoom(0.1))

        btn_reload = QPushButton("↻ Обновить")
        btn_reload.setObjectName("SecondaryBtn")
        btn_reload.clicked.connect(self.reload_page)

        btn_ext = QPushButton("Открыть в браузере ↗")
        btn_ext.setObjectName("SecondaryBtn")
        btn_ext.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._url)))

        tb_lay.addWidget(lbl_url)
        tb_lay.addSpacing(8)
        tb_lay.addWidget(self._status_lbl)
        tb_lay.addStretch(1)
        tb_lay.addWidget(self._isolate_btn)
        tb_lay.addWidget(btn_zoom_out)
        tb_lay.addWidget(self._zoom_lbl)
        tb_lay.addWidget(btn_zoom_in)
        tb_lay.addWidget(btn_reload)
        tb_lay.addWidget(btn_ext)

        root.addWidget(toolbar)

        # Тонкая полоса загрузки страницы
        self._progress = QProgressBar()
        self._progress.setObjectName("ThinProgress")
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        self._progress.hide()
        root.addWidget(self._progress)

        if not WEBENGINE_AVAILABLE:
            self._build_missing_webengine_placeholder(root)
            return

        # Встроенный Chromium (QWebEngineView)
        self._view = QWebEngineView(self)
        page = QWebEnginePage(_get_shared_profile(), self._view)
        self._view.setPage(page)

        settings = page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)

        self._view.loadStarted.connect(self._on_load_started)
        self._view.loadProgress.connect(self._on_load_progress)
        self._view.loadFinished.connect(self._on_load_finished)

        root.addWidget(self._view, 1)

    def _build_missing_webengine_placeholder(self, root: QVBoxLayout) -> None:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(12)

        card = QFrame()
        card.setObjectName("Card")
        card.setMaximumWidth(560)
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(24, 22, 24, 22)
        c_lay.setSpacing(10)

        t = QLabel("Не установлен модуль QtWebEngine")
        t.setObjectName("CardTitle")
        msg = QLabel(
            "Для отображения встроенных страниц Coinglass установите полный пакет PySide6:\n\n"
            "pip install --upgrade PySide6 PySide6-Addons"
        )
        msg.setWordWrap(True)
        msg.setObjectName("SettingsHint")

        c_lay.addWidget(t)
        c_lay.addWidget(msg)
        lay.addWidget(card)
        root.addWidget(wrap, 1)

    def ensure_loaded(self) -> None:
        """Загружает сайт при первом переходе на вкладку инструмента."""
        if self._loaded or self._view is None:
            return
        self._loaded = True
        self._view.setUrl(QUrl(self._url))

    def reload_page(self) -> None:
        if self._view is None:
            return
        if not self._loaded:
            self.ensure_loaded()
        else:
            self._view.reload()

    def _toggle_isolation(self) -> None:
        self._isolated = not self._isolated
        self._isolate_btn.setText(
            "Только график: ВКЛ" if self._isolated else "Только график: ВЫКЛ"
        )
        self._isolate_btn.setProperty("active", self._isolated)
        repolish(self._isolate_btn)
        self._inject_isolation()

    def _change_zoom(self, delta: float) -> None:
        if self._view is None:
            return
        self._zoom = max(0.5, min(2.0, round(self._zoom + delta, 2)))
        self._view.setZoomFactor(self._zoom)
        self._zoom_lbl.setText(f"{int(round(self._zoom * 100))}%")

    def _reset_zoom(self) -> None:
        if self._view is None:
            return
        self._zoom = 1.0
        self._view.setZoomFactor(1.0)
        self._zoom_lbl.setText("100%")

    def _on_load_started(self) -> None:
        self._progress.setValue(5)
        self._progress.show()
        self._status_lbl.setText("Загрузка страницы...")

    def _on_load_progress(self, value: int) -> None:
        self._progress.setValue(value)
        if value >= 40 and self._isolated:
            self._inject_isolation()

    def _on_load_finished(self, ok: bool) -> None:
        self._progress.hide()
        if ok:
            self._status_lbl.setText("Готово")
            self._inject_isolation()
            # Повторная инъекция после догрузки SPA-скриптов Coinglass
            QTimer.singleShot(800, self._inject_isolation)
            QTimer.singleShot(2200, self._inject_isolation)
        else:
            self._status_lbl.setText("Ошибка загрузки страницы")

    def _inject_isolation(self) -> None:
        if self._view is None or self._view.page() is None:
            return
        js = build_isolation_js(self._target_class, self._isolated)
        self._view.page().runJavaScript(js)
