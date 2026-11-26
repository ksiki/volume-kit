"""Единый прокси-осведомлённый HTTP-клиент Volume Kit.

``ProxyAwareSession`` на каждом запросе опрашивает :class:`ProxyManager`,
поэтому переключение прокси, включение/выключение и случайная ротация
применяются мгновенно во всех уже запущенных фоновых потоках.
"""

from __future__ import annotations

import logging

import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
)
from requests.exceptions import (
    ConnectTimeout,
    ProxyError,
    SSLError,
)

from app.core.proxy import ProxyEntry, get_proxy_manager, mask_credentials

log = logging.getLogger(__name__)

_FALLBACK_ERRORS = (ProxyError, ConnectTimeout, RequestsConnectionError, SSLError)


class ProxyNotConfiguredError(ProxyError):
    """Прокси включён, но активный прокси не выбран или нет включённых записей."""


def _mask_exception_credentials(exc: BaseException) -> None:
    """Прячет креды в тексте исключения, чтобы они не попали в логи/UI."""

    for index, arg in enumerate(exc.args):
        if isinstance(arg, str):
            masked = mask_credentials(arg)
            if masked != arg:
                try:
                    exc.args = (*exc.args[:index], masked, *exc.args[index + 1 :])
                except Exception:  # noqa: BLE001 - не мешаем исходному исключению
                    log.debug("Не удалось замаскировать креды в тексте исключения")
        elif isinstance(arg, BaseException):
            _mask_exception_credentials(arg)


class ProxyAwareSession(requests.Session):
    """``requests.Session``, динамически использующая прокси из ProxyManager."""

    def __init__(self, proxy_override: ProxyEntry | None = None) -> None:
        super().__init__()
        self._proxy_override = proxy_override
        # Приложение само решает, идти через прокси или напрямую.
        # trust_env меняется на каждом запросе (см. request).
        self.trust_env = False
        self.headers.setdefault("User-Agent", "VolumeKit/1.0")

    def _proxies_for(self, entry: ProxyEntry) -> dict[str, str]:
        manager = get_proxy_manager()
        url = entry.url(manager.remote_dns)
        return {"http": url, "https": url}

    def _send(
        self,
        method: str,
        url: str,
        *,
        proxies: dict[str, str] | None,
        **kwargs,
    ) -> requests.Response:
        kwargs.pop("proxies", None)
        kwargs["proxies"] = proxies
        return super().request(method, url, **kwargs)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:  # type: ignore[override]
        try:
            return self._request_with_proxy(method, url, **kwargs)
        except requests.RequestException as exc:
            _mask_exception_credentials(exc)
            raise

    def _request_with_proxy(self, method: str, url: str, **kwargs) -> requests.Response:
        override = self._proxy_override
        if override is not None:
            self.trust_env = False
            return self._send(
                method,
                url,
                proxies=self._proxies_for(override),
                **kwargs,
            )

        manager = get_proxy_manager()
        if not manager.enabled:
            self.trust_env = True
            return self._send(method, url, proxies=None, **kwargs)

        self.trust_env = False
        proxy = manager.pick_proxy()
        if proxy is None:
            raise ProxyNotConfiguredError(
                "Прокси включён, но активный прокси не выбран "
                "(или нет включённых записей в списке)"
            )

        try:
            return self._send(
                method,
                url,
                proxies=self._proxies_for(proxy),
                **kwargs,
            )
        except _FALLBACK_ERRORS as exc:
            if not manager.random_mode:
                raise

            fallback = manager.pick_proxy(exclude_ids={proxy.id})
            if fallback is None:
                raise

            log.warning(
                "Прокси %s недоступен (%s) — пробуем резервный %s",
                proxy.masked_url(manager.remote_dns),
                mask_credentials(str(exc)),
                fallback.masked_url(manager.remote_dns),
            )
            return self._send(
                method,
                url,
                proxies=self._proxies_for(fallback),
                **kwargs,
            )

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._proxy_override = None


def create_http_session(proxy_override: ProxyEntry | None = None) -> ProxyAwareSession:
    """Создаёт прокси-осведомлённую сессию для одного потока."""

    return ProxyAwareSession(proxy_override=proxy_override)


def http_request(method: str, url: str, **kwargs) -> requests.Response:
    """Одноразовый запрос через общий прокси-менеджер."""

    with create_http_session() as session:
        return session.request(method, url, **kwargs)
