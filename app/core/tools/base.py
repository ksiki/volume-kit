"""Базовые абстракции инструментов: контекст, шина команд и протокол оболочки."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Protocol

from app.core.config import ConfigService

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

CommandHandler = Callable[..., Any]
LogCallback = Callable[[dict[str, Any]], None]


class CommandBus:
    """Именованные команды, доступные инструментам приложения."""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler) -> None:
        if not name:
            raise ValueError("Имя команды не может быть пустым")
        if name in self._handlers:
            raise ValueError(f"Команда уже зарегистрирована: {name}")
        self._handlers[name] = handler

    def unregister(self, name: str) -> None:
        self._handlers.pop(name, None)

    def is_registered(self, name: str) -> bool:
        return name in self._handlers

    def invoke(self, name: str, *args: Any, **kwargs: Any) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            raise KeyError(f"Команда не зарегистрирована: {name}")
        return handler(*args, **kwargs)


class ToolHost(Protocol):
    """Интерфейс главного окна, который видят инструменты."""

    @property
    def current_symbol(self) -> str: ...

    def show_toast(self, message: str) -> None: ...

    def open_logs(self) -> None: ...

    def set_symbol(self, symbol: str) -> None: ...

    def show_reference_price(self, value: Any) -> None: ...

    def switch_tool(self, tool_id: str) -> None: ...

    def subscribe_logs(self, callback: LogCallback) -> Callable[[], None]: ...


@dataclass
class ToolContext:
    """Зависимости, которые оболочка передаёт каждому инструменту."""

    config: ConfigService
    host: ToolHost
    commands: CommandBus


class BaseTool(ABC):
    """Базовый класс инструмента Volume Kit."""

    tool_id: ClassVar[str]
    title: ClassVar[str]
    nav_label: ClassVar[str]
    nav_group: ClassVar[str] = "ИНСТРУМЕНТЫ"
    order: ClassVar[int] = 100

    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._widget: "QWidget | None" = None

    @property
    def context(self) -> ToolContext:
        return self._context

    @property
    def is_widget_created(self) -> bool:
        return self._widget is not None

    @property
    def widget(self) -> "QWidget":
        """Лениво создаёт и возвращает виджет инструмента."""
        if self._widget is None:
            self._widget = self.create_widget()
            self.on_widget_created()
        return self._widget

    @abstractmethod
    def create_widget(self) -> "QWidget":
        """Создаёт корневой виджет инструмента (вызывается один раз, по требованию)."""

    def on_widget_created(self) -> None:
        """Хук после создания виджета (подписки, сигналы)."""

    def on_first_shown(self) -> None:
        """Хук первого показа вкладки инструмента."""

    def on_shown(self) -> None:
        """Хук каждого показа вкладки инструмента."""

    def shutdown(self) -> None:
        """Освобождает ресурсы инструмента при закрытии окна."""
