"""Ядро менеджера прокси: модели, парсеры, хранилище и потокобезопасный singleton.

Модуль не зависит от PySide6 и может использоваться фоновыми потоками и CLI.
Креды прокси никогда не должны попадать в логи: для этого есть ``masked_url``
и :func:`mask_credentials`.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from app.core.paths import PROXIES_PATH

log = logging.getLogger(__name__)

ALLOWED_PROTOCOLS = ("http", "https", "socks5", "socks4")

_SCHEME_ALIASES = {
    "http": "http",
    "https": "https",
    "socks": "socks5",
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks4": "socks4",
    "socks4a": "socks4",
}

_SCHEME_RE = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://")
_CREDENTIALS_RE = re.compile(
    r"(?P<prefix>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@"
)
_PLAIN_CREDENTIALS_RE = re.compile(r"(?<![\w./:-])(?P<user>[^:@*\s/]+):(?P<pwd>[^@\s/]+)@")


class ProxyValidationError(ValueError):
    """Некорректные данные прокси (хост, порт, протокол и т.п.)."""


class ProxyParseError(ValueError):
    """Не удалось разобрать строку с прокси."""


def mask_credentials(text: object) -> str:
    """Маскирует логин/пароль в произвольном тексте ошибки или URL."""

    value = str(text)
    value = _CREDENTIALS_RE.sub(lambda m: f"{m.group('prefix')}***:***@", value)
    value = _PLAIN_CREDENTIALS_RE.sub("***:***@", value)
    return value


def sanitize_error(exc: BaseException, limit: int = 320) -> str:
    """Возвращает короткий безопасный текст исключения без кредов."""

    text = mask_credentials(str(exc) or exc.__class__.__name__)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def normalize_protocol(value: object) -> str:
    """Приводит схему к одному из поддерживаемых протоколов."""

    clean = str(value or "").strip().lower()
    protocol = _SCHEME_ALIASES.get(clean)
    if protocol is None:
        raise ProxyValidationError(
            f"Неподдерживаемый протокол {clean!r}. Допустимо: {', '.join(ALLOWED_PROTOCOLS)}"
        )
    return protocol


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    return default


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class ProxyEntry:
    """Одна запись прокси."""

    id: str
    protocol: str
    host: str
    port: int
    username: str = ""
    password: str = field(default="", repr=False)
    label: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip() or _new_id()
        self.protocol = normalize_protocol(self.protocol)
        self.host = str(self.host or "").strip()
        self.username = str(self.username or "").strip()
        self.password = str(self.password or "")
        self.label = str(self.label or "").strip()
        self.enabled = _as_bool(self.enabled, True)

        try:
            self.port = int(self.port)
        except (TypeError, ValueError) as exc:
            raise ProxyValidationError(f"Порт должен быть числом: {self.port!r}") from exc

        if not self.host:
            raise ProxyValidationError("Хост не может быть пустым")
        if any(ch.isspace() for ch in self.host):
            raise ProxyValidationError("Хост не должен содержать пробелы")
        if not (1 <= self.port <= 65535):
            raise ProxyValidationError(
                f"Порт должен быть в диапазоне 1–65535, получено {self.port}"
            )

    def effective_scheme(self, remote_dns: bool = True) -> str:
        if self.protocol == "socks5" and remote_dns:
            return "socks5h"
        if self.protocol == "socks4" and remote_dns:
            return "socks4a"
        return self.protocol

    def _host_for_url(self) -> str:
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return host

    def url(self, remote_dns: bool = True) -> str:
        """Полный URL прокси для requests (с кредами, только для транспорта)."""

        scheme = self.effective_scheme(remote_dns)
        auth = ""
        if self.username or self.password:
            user = quote(self.username, safe="")
            password = quote(self.password, safe="")
            auth = f"{user}:{password}@"
        return f"{scheme}://{auth}{self._host_for_url()}:{self.port}"

    def masked_url(self, remote_dns: bool = True) -> str:
        """URL без пароля — безопасно для логов, ошибок и UI."""

        scheme = self.effective_scheme(remote_dns)
        auth = ""
        if self.username or self.password:
            user = quote(self.username, safe="") if self.username else "***"
            auth = f"{user}:***@"
        return f"{scheme}://{auth}{self._host_for_url()}:{self.port}"

    def display_name(self) -> str:
        return self.label or f"{self.host}:{self.port}"

    @property
    def has_auth(self) -> bool:
        return bool(self.username or self.password)


    # -------------------- Сериализация --------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "protocol": self.protocol,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "label": self.label,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: object) -> ProxyEntry:
        if not isinstance(data, dict):
            raise ProxyValidationError("Запись прокси должна быть JSON-объектом")
        return cls(
            id=str(data.get("id", "")),
            protocol=str(data.get("protocol", "")),
            host=str(data.get("host", "")),
            port=data.get("port", 0),  # type: ignore[arg-type]
            username=str(data.get("username", "") or ""),
            password=str(data.get("password", "") or ""),
            label=str(data.get("label", "") or ""),
            enabled=_as_bool(data.get("enabled", True), True),
        )

    def copy(self) -> ProxyEntry:
        return ProxyEntry(
            id=self.id,
            protocol=self.protocol,
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            label=self.label,
            enabled=self.enabled,
        )



def _split_host_port(value: str) -> tuple[str, int]:
    """Разбирает host:port с поддержкой IPv6 в квадратных скобках."""

    text = value.strip()
    if not text:
        raise ProxyParseError("не указан адрес host:port")

    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            raise ProxyParseError("незакрытая квадратная скобка в IPv6-адресе")
        host = text[1:end].strip()
        rest = text[end + 1 :]
        if not rest.startswith(":"):
            raise ProxyParseError("после IPv6-адреса ожидается :port")
        port_text = rest[1:]
    else:
        if text.count(":") != 1:
            raise ProxyParseError("ожидается host:port")
        host, port_text = text.rsplit(":", 1)

    host = host.strip()
    port_text = port_text.strip()
    if not host:
        raise ProxyParseError("хост не может быть пустым")
    if any(ch.isspace() for ch in host):
        raise ProxyParseError("хост не должен содержать пробелы")
    if not port_text.isdigit():
        raise ProxyParseError("порт должен быть числом")
    port = int(port_text)
    if not (1 <= port <= 65535):
        raise ProxyParseError("порт должен быть в диапазоне 1–65535")
    return host, port


def _parse_with_scheme(protocol: str, rest: str) -> ProxyEntry:
    rest = rest.strip()
    if not rest:
        raise ProxyParseError("после протокола не указан адрес")

    username = ""
    password = ""
    if "@" in rest:
        userinfo, hostport = rest.rsplit("@", 1)
        if ":" in userinfo:
            username, password = userinfo.split(":", 1)
        else:
            username = userinfo
        username = unquote(username)
        password = unquote(password)
    else:
        hostport = rest
        # host:port:user:pass — только если порт действительно числовой.
        if not rest.startswith("[") and rest.count(":") >= 3:
            parts = rest.split(":", 3)
            if parts[1].strip().isdigit():
                hostport = f"{parts[0]}:{parts[1]}"
                username = parts[2]
                password = parts[3]

    host, port = _split_host_port(hostport)
    return ProxyEntry(
        id=_new_id(),
        protocol=protocol,
        host=host,
        port=port,
        username=username.strip(),
        password=password,
    )


def parse_proxy_string(
    raw: str,
    default_protocol: str = "socks5",
    label: str = "",
) -> ProxyEntry:
    """Разбирает одну строку прокси в любом популярном формате."""

    text = str(raw or "").strip()
    if not text:
        raise ProxyParseError("пустая строка")

    scheme_match = _SCHEME_RE.match(text)
    if scheme_match is not None:
        protocol = normalize_protocol(scheme_match.group("scheme"))
        rest = text[scheme_match.end() :]
    else:
        protocol = normalize_protocol(default_protocol)
        rest = text

    # Допускаем завершающий слэш у скопированного URL.
    if rest.endswith("/") and not rest.endswith("//"):
        rest = rest[:-1]

    entry = _parse_with_scheme(protocol, rest)
    if label:
        entry.label = str(label).strip()
    return entry



def _bulk_lines(text: str) -> Iterable[tuple[int, str]]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for index, raw_line in enumerate(normalized.split("\n"), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        yield index, line


def _append_bulk_line(
    line: str,
    line_no: int,
    default_protocol: str,
    label: str,
    entries: list[ProxyEntry],
    errors: list[str],
) -> None:
    try:
        entries.append(parse_proxy_string(line, default_protocol=default_protocol, label=label))
        return
    except (ProxyParseError, ProxyValidationError):
        pass

    # Списки через запятую: сначала пробуем строку целиком (пароль может
    # содержать запятую), и только при ошибке делим на фрагменты.
    if "," in line:
        parts = [part.strip() for part in line.split(",") if part.strip()]
        for part in parts:
            try:
                entries.append(
                    parse_proxy_string(part, default_protocol=default_protocol, label=label)
                )
            except (ProxyParseError, ProxyValidationError) as exc:
                errors.append(f"Строка {line_no}: {exc}")
        return

    try:
        parse_proxy_string(line, default_protocol=default_protocol)
    except (ProxyParseError, ProxyValidationError) as exc:
        errors.append(f"Строка {line_no}: {exc}")


def parse_proxy_bulk(
    text: str,
    default_protocol: str = "socks5",
    label: str = "",
) -> tuple[list[ProxyEntry], list[str]]:
    """Разбирает многострочный список. Возвращает записи и человекочитаемые ошибки."""

    entries: list[ProxyEntry] = []
    errors: list[str] = []
    for line_no, line in _bulk_lines(text):
        if ";" in line:
            try:
                entries.append(
                    parse_proxy_string(line, default_protocol=default_protocol, label=label)
                )
                continue
            except (ProxyParseError, ProxyValidationError):
                pass
        chunks = [chunk.strip() for chunk in line.split(";") if chunk.strip()]
        for chunk in chunks or [line]:
            _append_bulk_line(chunk, line_no, default_protocol, label, entries, errors)
    return entries, errors



@dataclass
class ProxyConfig:
    """Состояние менеджера прокси."""

    enabled: bool = False
    random_mode: bool = False
    active_proxy_id: str | None = None
    remote_dns: bool = True
    apply_to_web: bool = False
    proxies: list[ProxyEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "random_mode": self.random_mode,
            "active_proxy_id": self.active_proxy_id,
            "remote_dns": self.remote_dns,
            "apply_to_web": self.apply_to_web,
            "proxies": [entry.to_dict() for entry in self.proxies],
        }

    @classmethod
    def from_dict(cls, data: object) -> ProxyConfig:
        if not isinstance(data, dict):
            return cls()

        raw_proxies = data.get("proxies", [])
        proxies: list[ProxyEntry] = []
        if isinstance(raw_proxies, list):
            for item in raw_proxies:
                try:
                    proxies.append(ProxyEntry.from_dict(item))
                except (ProxyValidationError, TypeError, ValueError) as exc:
                    log.warning("Пропущена некорректная запись прокси: %s", exc)

        active_id = data.get("active_proxy_id")
        active = str(active_id).strip() if active_id else None
        if active and not any(entry.id == active for entry in proxies):
            active = None

        return cls(
            enabled=_as_bool(data.get("enabled", False), False),
            random_mode=_as_bool(data.get("random_mode", False), False),
            active_proxy_id=active,
            remote_dns=_as_bool(data.get("remote_dns", True), True),
            apply_to_web=_as_bool(data.get("apply_to_web", False), False),
            proxies=proxies,
        )



class ProxyManager:
    """Потокобезопасный менеджер прокси с атомарным сохранением в JSON."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = Path(storage_path) if storage_path is not None else PROXIES_PATH
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._config = ProxyConfig()
        self._load()

    @property
    def storage_path(self) -> Path:
        return self._storage_path

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._config.enabled

    @property
    def random_mode(self) -> bool:
        with self._lock:
            return self._config.random_mode

    @property
    def remote_dns(self) -> bool:
        with self._lock:
            return self._config.remote_dns

    @property
    def apply_to_web(self) -> bool:
        with self._lock:
            return self._config.apply_to_web

    @property
    def active_proxy_id(self) -> str | None:
        with self._lock:
            return self._config.active_proxy_id

    def snapshot(self) -> ProxyConfig:
        with self._lock:
            return ProxyConfig(
                enabled=self._config.enabled,
                random_mode=self._config.random_mode,
                active_proxy_id=self._config.active_proxy_id,
                remote_dns=self._config.remote_dns,
                apply_to_web=self._config.apply_to_web,
                proxies=[entry.copy() for entry in self._config.proxies],
            )

    def entries(self) -> list[ProxyEntry]:
        with self._lock:
            return [entry.copy() for entry in self._config.proxies]

    def get_entry(self, proxy_id: str) -> ProxyEntry | None:
        with self._lock:
            for entry in self._config.proxies:
                if entry.id == proxy_id:
                    return entry.copy()
        return None

    def get_active_proxy(self) -> ProxyEntry | None:
        with self._lock:
            active = self._config.active_proxy_id
            if active is None:
                return None
            for entry in self._config.proxies:
                if entry.id == active:
                    return entry.copy()
        return None

    def enabled_count(self) -> int:
        with self._lock:
            return sum(1 for entry in self._config.proxies if entry.enabled)

    def pick_proxy(self, exclude_ids: set[str] | None = None) -> ProxyEntry | None:
        """Возвращает активный или случайный включённый прокси для текущего запроса."""

        with self._lock:
            if not self._config.enabled:
                return None

            if self._config.random_mode:
                excluded = exclude_ids or set()
                candidates = [
                    entry
                    for entry in self._config.proxies
                    if entry.enabled and entry.id not in excluded
                ]
                if not candidates:
                    return None
                return random.choice(candidates).copy()

            active = self._config.active_proxy_id
            if active is None:
                return None
            for entry in self._config.proxies:
                if entry.id == active:
                    return entry.copy()
            return None


    # -------------------- Изменение состояния --------------------

    def _commit(self, mutate: Callable[[], None]) -> None:
        with self._lock:
            mutate()
            self._save_locked()
        self._notify()

    def add(self, entry: ProxyEntry) -> ProxyEntry:
        """Добавляет прокси. При конфликте id генерируется новый."""

        added = entry.copy()

        def _apply() -> None:
            if any(existing.id == added.id for existing in self._config.proxies):
                added.id = _new_id()
            self._config.proxies.append(added)
            if self._config.active_proxy_id is None:
                self._config.active_proxy_id = added.id

        self._commit(_apply)
        return added.copy()

    def add_bulk(self, entries: Iterable[ProxyEntry]) -> tuple[int, int]:
        """Добавляет список прокси, пропуская дубли. Возвращает (добавлено, пропущено)."""

        added = 0
        skipped = 0

        def _apply() -> None:
            nonlocal added, skipped
            existing = {
                (entry.protocol, entry.host.lower(), entry.port, entry.username)
                for entry in self._config.proxies
            }
            for entry in entries:
                key = (entry.protocol, entry.host.lower(), entry.port, entry.username)
                if key in existing:
                    skipped += 1
                    continue
                new_entry = entry.copy()
                if any(item.id == new_entry.id for item in self._config.proxies):
                    new_entry.id = _new_id()
                self._config.proxies.append(new_entry)
                existing.add(key)
                added += 1
                if self._config.active_proxy_id is None:
                    self._config.active_proxy_id = new_entry.id

        self._commit(_apply)
        return added, skipped

    def update(self, entry: ProxyEntry) -> bool:
        """Обновляет запись по id. Возвращает False, если запись не найдена."""

        updated = entry.copy()
        changed = False

        def _apply() -> None:
            nonlocal changed
            for index, existing in enumerate(self._config.proxies):
                if existing.id == updated.id:
                    self._config.proxies[index] = updated
                    changed = True
                    return

        self._commit(_apply)
        return changed

    def remove(self, proxy_id: str) -> bool:
        """Удаляет прокси и при необходимости переключает активный."""

        removed = False

        def _apply() -> None:
            nonlocal removed
            before = len(self._config.proxies)
            self._config.proxies = [
                entry for entry in self._config.proxies if entry.id != proxy_id
            ]
            removed = len(self._config.proxies) != before
            if removed and self._config.active_proxy_id == proxy_id:
                self._config.active_proxy_id = (
                    self._config.proxies[0].id if self._config.proxies else None
                )

        self._commit(_apply)
        return removed

    def set_active(self, proxy_id: str | None) -> bool:
        def _apply() -> None:
            self._config.active_proxy_id = proxy_id

        with self._lock:
            if proxy_id is not None and not any(
                entry.id == proxy_id for entry in self._config.proxies
            ):
                return False
        self._commit(_apply)
        return True

    def set_enabled(self, value: bool) -> None:
        def _apply() -> None:
            self._config.enabled = bool(value)

        self._commit(_apply)

    def set_random_mode(self, value: bool) -> None:
        def _apply() -> None:
            self._config.random_mode = bool(value)

        self._commit(_apply)

    def set_remote_dns(self, value: bool) -> None:
        def _apply() -> None:
            self._config.remote_dns = bool(value)

        self._commit(_apply)

    def set_apply_to_web(self, value: bool) -> None:
        def _apply() -> None:
            self._config.apply_to_web = bool(value)

        self._commit(_apply)


    # -------------------- Сохранение / загрузка --------------------

    def _save_locked(self) -> None:
        path = self._storage_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=path.name + ".",
                suffix=".tmp",
                dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        self._config.to_dict(),
                        handle,
                        ensure_ascii=False,
                        indent=2,
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            except Exception:
                with suppress(OSError):
                    os.unlink(temp_name)
                raise
            with suppress(OSError):
                os.chmod(path, 0o600)
        except Exception as exc:  # noqa: BLE001 - сохранение не должно ронять приложение
            log.error("Не удалось сохранить %s: %s", path.name, sanitize_error(exc))

    def _load(self) -> None:
        path = self._storage_path
        if not path.is_file():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - битый файл не должен ронять старт
            log.warning("Не удалось прочитать %s: %s", path.name, sanitize_error(exc))
            backup = path.with_name(path.name + ".bak")
            try:
                os.replace(path, backup)
                log.warning("Повреждённый файл сохранён как %s", backup.name)
            except OSError as backup_exc:
                log.warning("Не удалось создать резервную копию: %s", backup_exc)
            return

        try:
            self._config = ProxyConfig.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Некорректное состояние прокси в %s: %s", path.name, sanitize_error(exc))
            self._config = ProxyConfig()

    # -------------------- Подписки --------------------

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Подписывает на изменения состояния; возвращает функцию отписки."""

        with self._lock:
            self._listeners.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._listeners.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback()
            except Exception:
                log.exception("Ошибка в подписчике ProxyManager")

_MANAGER_LOCK = threading.RLock()
_MANAGER: ProxyManager | None = None


def get_proxy_manager() -> ProxyManager:
    """Возвращает процесс-синглтон менеджера прокси."""

    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ProxyManager()
        return _MANAGER

