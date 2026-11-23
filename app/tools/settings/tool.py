"""Инструмент визуального редактирования config.json."""

from typing import Any, cast

from PySide6.QtWidgets import QWidget

from app.core.tools import BaseTool, ToolContext, register_tool
from app.tools.settings.page import SettingsPage


@register_tool
class SettingsTool(BaseTool):
    """Инструмент настроек config.json."""

    tool_id = "settings"
    title = "Настройки (config.json)"
    nav_label = "⚙  Настройки"
    nav_group = "СИСТЕМА"
    order = 900

    @property
    def page(self) -> SettingsPage:
        return cast(SettingsPage, self.widget)

    def create_widget(self) -> QWidget:
        page = SettingsPage(self.context.config)
        page.saved.connect(self._on_saved)
        page.toast_requested.connect(self.context.host.show_toast)
        return page

    def _on_saved(self, cfg: dict[str, Any], recalculate: bool) -> None:
        self.context.host.set_symbol(str(cfg.get("SYMBOL", self.context.config.current.symbol)))
        if recalculate:
            self.context.host.show_toast("✓ Настройки сохранены — запускаю пересчёт...")
            self.context.host.switch_tool("calibrator")
            self.context.commands.invoke("calibration.start", reload_cfg=True)
        else:
            self.context.host.show_toast("✓ Настройки сохранены в config.json")
