"""Инструмент Liquidation Heatmap (Coinglass)."""

from typing import cast

from PySide6.QtWidgets import QWidget

from app.core.tools import BaseTool, register_tool
from app.tools.web.web_tool_page import WebToolPage

COINGLASS_LIQ_URL = "https://www.coinglass.com/ru/LiquidationHeatMap/BTC?pair=Binance_BTCUSDT"
COINGLASS_LIQ_CLASS = "liq-heatmap_chartPanel__5Sby3"


@register_tool
class LiquidationHeatmapTool(BaseTool):
    """Встроенная страница Coinglass Liquidation Heatmap."""

    tool_id = "liq_heatmap"
    title = "Liquidation Heatmap"
    nav_label = "◔  Liquidation Heatmap"
    nav_group = "ИНСТРУМЕНТЫ"
    order = 20

    @property
    def page(self) -> WebToolPage:
        return cast(WebToolPage, self.widget)

    def create_widget(self) -> QWidget:
        return WebToolPage(
            title="Liquidation Heatmap (Binance BTCUSDT)",
            url=COINGLASS_LIQ_URL,
            target_class=COINGLASS_LIQ_CLASS,
        )

    def on_shown(self) -> None:
        # Ленивая загрузка веб-инструмента при первом открытии
        self.page.ensure_loaded()
