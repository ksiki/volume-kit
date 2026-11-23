"""Реестр инструментов и декоратор явной регистрации."""

from typing import Iterator, TypeVar

from app.core.tools.base import BaseTool

ToolT = TypeVar("ToolT", bound=type[BaseTool])


class ToolRegistry:
    """Упорядоченный реестр классов инструментов."""

    def __init__(self) -> None:
        self._classes: dict[str, type[BaseTool]] = {}
        self._registration_order: list[str] = []

    def register(self, tool_cls: type[BaseTool], *, override: bool = False) -> None:
        tool_id = getattr(tool_cls, "tool_id", "")
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise ValueError(f"У инструмента {tool_cls!r} не задан tool_id")
        if tool_id in self._classes and not override:
            raise ValueError(f"Инструмент уже зарегистрирован: {tool_id}")
        if tool_id not in self._classes:
            self._registration_order.append(tool_id)
        self._classes[tool_id] = tool_cls

    def unregister(self, tool_id: str) -> None:
        self._classes.pop(tool_id, None)
        if tool_id in self._registration_order:
            self._registration_order.remove(tool_id)

    def get(self, tool_id: str) -> type[BaseTool]:
        return self._classes[tool_id]

    def classes(self) -> list[type[BaseTool]]:
        """Возвращает инструменты, отсортированные по order и порядку регистрации."""
        indexed = {tool_id: index for index, tool_id in enumerate(self._registration_order)}
        return sorted(
            self._classes.values(),
            key=lambda cls: (cls.order, indexed.get(cls.tool_id, 0)),
        )

    def __contains__(self, tool_id: object) -> bool:
        return tool_id in self._classes

    def __len__(self) -> int:
        return len(self._classes)

    def __iter__(self) -> Iterator[type[BaseTool]]:
        return iter(self.classes())


_default_registry = ToolRegistry()


def register_tool(tool_cls: ToolT) -> ToolT:
    """Декоратор явной регистрации класса инструмента в реестре по умолчанию."""
    _default_registry.register(tool_cls)
    return tool_cls


def get_default_registry() -> ToolRegistry:
    return _default_registry
