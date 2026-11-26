"""Применение настроек прокси к QtNetwork / QWebEngine."""

from __future__ import annotations

import logging

from PySide6.QtNetwork import QNetworkProxy

from app.core.proxy import ProxyEntry, get_proxy_manager

log = logging.getLogger(__name__)

_current_warning: str | None = None


def _set_no_proxy() -> None:
    QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))


def _qt_proxy_type(entry: ProxyEntry) -> QNetworkProxy.ProxyType | None:
    if entry.protocol in ("http", "https"):
        return QNetworkProxy.ProxyType.HttpProxy
    if entry.protocol == "socks5":
        return QNetworkProxy.ProxyType.Socks5Proxy
    # В Qt 6 нет Socks4Proxy — SOCKS4 остаётся только в requests.
    return None


def apply_qt_proxy_from_manager() -> str | None:
    """Синхронизирует QNetworkProxy с ProxyManager.

    Возвращает текст предупреждения для UI или ``None``, если всё применено.
    Никогда не логирует и не показывает пароль.
    """

    global _current_warning

    manager = get_proxy_manager()
    config = manager.snapshot()

    if not config.enabled or not config.apply_to_web:
        _set_no_proxy()
        _current_warning = None
        return None

    entry = manager.get_active_proxy()
    if entry is None:
        _set_no_proxy()
        _current_warning = (
            "Прокси включён, но активный прокси не выбран — Web-вкладки идут напрямую."
        )
        return _current_warning

    if entry.protocol == "socks4":
        _set_no_proxy()
        _current_warning = (
            "Qt 6 / QWebEngine не поддерживает SOCKS4 — для Web-вкладок прокси не применён."
        )
        return _current_warning

    if entry.protocol == "socks5" and (entry.username or entry.password):
        # Chromium не умеет авторизоваться в SOCKS5 без диалога/расширения.
        # Не применяем прокси, чтобы не сломать загрузку вкладок.
        _set_no_proxy()
        _current_warning = (
            "Chromium / QWebEngine не поддерживает SOCKS5 с авторизацией — "
            "для Web-вкладок прокси не применён (HTTP-запросы приложения идут через прокси)."
        )
        return _current_warning

    proxy_type = _qt_proxy_type(entry)
    if proxy_type is None:
        _set_no_proxy()
        _current_warning = "Этот тип прокси не поддерживается Qt/WebEngine."
        return _current_warning

    proxy = QNetworkProxy(
        proxy_type,
        entry.host,
        entry.port,
        entry.username,
        entry.password,
    )
    QNetworkProxy.setApplicationProxy(proxy)
    _current_warning = None
    log.info(
        "Qt-прокси применён: %s",
        entry.masked_url(config.remote_dns),
    )
    return None


def web_proxy_warning() -> str | None:
    return _current_warning
