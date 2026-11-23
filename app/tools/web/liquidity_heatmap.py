"""Инструмент Liquidity Heatmap (Coinglass)."""

from typing import cast

from PySide6.QtWidgets import QWidget

from app.core.tools import BaseTool, register_tool
from app.tools.web.web_tool_page import WebToolPage

COINGLASS_OB_URL = "https://www.coinglass.com/LiquidityHeatmap/BTC"
COINGLASS_OB_CLASS = "ChartCard_card__wWOwA"


@register_tool
class LiquidityHeatmapTool(BaseTool):
    """Встроенная страница Coinglass Liquidity Heatmap."""

    tool_id = "ob_heatmap"
    title = "Liquidity Heatmap"
    nav_label = "▤  Liquidity Heatmap"
    nav_group = "ИНСТРУМЕНТЫ"
    order = 30

    @property
    def page(self) -> WebToolPage:
        return cast(WebToolPage, self.widget)

    def create_widget(self) -> QWidget:
        return WebToolPage(
            title="Liquidity Heatmap (BTC)",
            url=COINGLASS_OB_URL,
            target_class=COINGLASS_OB_CLASS,
        )

    def on_shown(self) -> None:
        # Ленивая загрузка веб-инструмента при первом открытии
        self.page.ensure_loaded()
