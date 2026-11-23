import json
from pathlib import Path

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

from ..config import BASE_DIR
from .widgets import repolish

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
        profile_dir = Path(BASE_DIR) / "web_profile"
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


def _build_isolation_js(target_class: str, enabled: bool) -> str:
    """JS для изоляции целевого div без разрушения React-дерева, flex-вёрстки и выпадающих меню."""
    class_json = json.dumps(target_class)
    # Префикс до хэша CSS-модуля (на случай если Coinglass обновит хэш)
    prefix = target_class.split("__")[0] + "__" if "__" in target_class else target_class
    prefix_json = json.dumps(prefix)
    enabled_js = "true" if enabled else "false"

    return f"""
(function() {{
  const TARGET_CLASS = {class_json};
  const TARGET_PREFIX = {prefix_json};
  const ENABLED = {enabled_js};
  const STYLE_ID = "Volume Kit-isolation-style";

  function stopWatchers() {{
    if (window.__thObserver) {{
      try {{ window.__thObserver.disconnect(); }} catch (e) {{}}
      window.__thObserver = null;
    }}
    if (window.__thInterval) {{
      clearInterval(window.__thInterval);
      window.__thInterval = null;
    }}
  }}

  function clearIsolation() {{
    stopWatchers();
    const st = document.getElementById(STYLE_ID);
    if (st) st.remove();
    if (document.body) {{
      document.body.classList.remove("th-isolated");
    }}
    document.querySelectorAll(
      "[data-th-keep],[data-th-target],[data-th-portal],[data-th-portal-ancestor]"
    ).forEach((el) => {{
      el.removeAttribute("data-th-keep");
      el.removeAttribute("data-th-target");
      el.removeAttribute("data-th-portal");
      el.removeAttribute("data-th-portal-ancestor");
    }});
    window.dispatchEvent(new Event("resize"));
  }}

  if (!ENABLED) {{
    clearIsolation();
    return;
  }}

  function ensureStyle() {{
    let st = document.getElementById(STYLE_ID);
    if (!st) {{
      st = document.createElement("style");
      st.id = STYLE_ID;
      (document.head || document.documentElement).appendChild(st);
    }}
    st.textContent = `
      html, body {{
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        height: 100% !important;
        min-height: 100vh !important;
        overflow-x: hidden !important;
      }}
      body.th-isolated *:not([data-th-keep]):not([data-th-target]):not([data-th-target] *):not([data-th-portal]):not([data-th-portal] *):not([data-th-portal-ancestor]) {{
        display: none !important;
      }}
      body.th-isolated [data-th-keep="1"] {{
        display: block !important;
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        height: 100% !important;
        min-height: 100% !important;
        border: none !important;
        box-shadow: none !important;
        position: static !important;
        transform: none !important;
      }}
      body.th-isolated [data-th-target="1"] {{
        display: flex !important;
        flex-direction: column !important;
        visibility: visible !important;
        opacity: 1 !important;
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        height: 100vh !important;
        min-height: 100vh !important;
        margin: 0 !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        box-sizing: border-box !important;
      }}
      body.th-isolated [data-th-target="1"] > [class*="ChartCard_toolbar__"],
      body.th-isolated [data-th-target="1"] > [class*="ChartCard_subbar__"],
      body.th-isolated [data-th-target="1"] > [class*="Toolbar_toolbar__"] {{
        flex: 0 0 auto !important;
      }}
      body.th-isolated [data-th-target="1"] > [class*="ChartCard_plot__"],
      body.th-isolated [data-th-target="1"] > [class*="ChartArea_single__"] {{
        flex: 1 1 0% !important;
        min-height: 360px !important;
        width: 100% !important;
      }}
      body.th-isolated [data-th-target="1"] [class*="ChartArea_stage__"] {{
        flex: 1 1 auto !important;
        min-height: 320px !important;
      }}
    `;
  }}

  function isIgnoredBodyChild(el) {{
    if (!el || el.nodeType !== 1) return true;
    const tag = el.tagName;
    if (
      tag === "SCRIPT" ||
      tag === "STYLE" ||
      tag === "LINK" ||
      tag === "NOSCRIPT" ||
      tag === "META"
    ) {{
      return true;
    }}
    if (el.id === "__next") return true;
    const cls = typeof el.className === "string" ? el.className : "";
    const idCls = ((el.id || "") + " " + cls).toLowerCase();
    if (
      idCls.includes("termly") ||
      idCls.includes("cookie") ||
      idCls.includes("consent")
    ) {{
      return true;
    }}
    return false;
  }}

  function markPortals() {{
    // 1. Все порталы (меню настроек, списки, диалоги скриншота), смонтированные прямо в body вне #__next
    if (document.body) {{
      Array.from(document.body.children).forEach((child) => {{
        if (!isIgnoredBodyChild(child) && child.getAttribute("data-th-portal") !== "1") {{
          child.setAttribute("data-th-portal", "1");
        }}
      }});
    }}

    // 2. Всплывающие меню/подсказки MUI, AntD и графика, если они смонтированы внутри #__next
    const portalSelectors = [
      ".MuiPopover-root",
      ".MuiPopper-root",
      ".MuiModal-root",
      ".MuiTooltip-popper",
      ".ant-dropdown",
      ".ant-select-dropdown",
      ".ant-tooltip",
      ".ant-popover",
      ".ant-modal-root",
      "[data-floating-ui-portal]",
      "[data-radix-popper-content-wrapper]",
      "[role='listbox']",
      "[role='tooltip']",
      "[role='dialog']",
      "[role='menu']",
      "[aria-modal='true']"
    ];
    for (const sel of portalSelectors) {{
      document.querySelectorAll(sel).forEach((el) => {{
        if (el.closest("header, footer, aside")) return;
        if (el.getAttribute("data-th-portal") !== "1") {{
          el.setAttribute("data-th-portal", "1");
        }}
        let cur = el.parentElement;
        while (cur && cur !== document.body && cur !== document.documentElement) {{
          if (
            !cur.hasAttribute("data-th-keep") &&
            !cur.hasAttribute("data-th-target") &&
            cur.getAttribute("data-th-portal-ancestor") !== "1"
          ) {{
            cur.setAttribute("data-th-portal-ancestor", "1");
          }}
          cur = cur.parentElement;
        }}
      }});
    }}
  }}

  function findTarget() {{
    let el = document.querySelector("div." + CSS.escape(TARGET_CLASS));
    if (!el) {{
      el = document.querySelector("." + CSS.escape(TARGET_CLASS));
    }}
    if (!el && TARGET_PREFIX) {{
      el = document.querySelector("div[class*='" + TARGET_PREFIX.replace(/'/g, "") + "']");
    }}
    return el;
  }}

  function applyIsolation() {{
    if (!document.body) return false;
    const target = findTarget();
    if (!target) return false;

    ensureStyle();

    const alreadySame = target.getAttribute("data-th-target") === "1"
      && document.body.classList.contains("th-isolated");

    if (!alreadySame) {{
      document.querySelectorAll("[data-th-keep],[data-th-target]").forEach((el) => {{
        el.removeAttribute("data-th-keep");
        el.removeAttribute("data-th-target");
      }});

      target.setAttribute("data-th-target", "1");
      let cur = target.parentElement;
      while (cur && cur !== document.documentElement) {{
        cur.setAttribute("data-th-keep", "1");
        cur = cur.parentElement;
      }}
      document.body.classList.add("th-isolated");

      setTimeout(() => window.dispatchEvent(new Event("resize")), 80);
      setTimeout(() => window.dispatchEvent(new Event("resize")), 350);
      setTimeout(() => window.dispatchEvent(new Event("resize")), 900);
    }}

    markPortals();
    return true;
  }}

  stopWatchers();
  applyIsolation();

  let debounceTimer = null;
  window.__thObserver = new MutationObserver(() => {{
    markPortals();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => applyIsolation(), 120);
  }});
  if (document.documentElement) {{
    window.__thObserver.observe(document.documentElement, {{
      childList: true,
      subtree: true
    }});
  }}

  let ticks = 0;
  window.__thInterval = setInterval(() => {{
    ticks += 1;
    applyIsolation();
    if (ticks >= 40) {{
      clearInterval(window.__thInterval);
      window.__thInterval = null;
    }}
  }}, 500);
}})();
"""


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
        js = _build_isolation_js(self._target_class, self._isolated)
        self._view.page().runJavaScript(js)