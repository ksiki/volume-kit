"""Явная регистрация встроенных инструментов Volume Kit."""

from app.core.tools import ToolRegistry, get_default_registry


def register_builtin_tools() -> ToolRegistry:
    """Импортирует пакеты инструментов, чтобы сработали декораторы @register_tool.

    Явные импорты (вместо автообнаружения через pkgutil) гарантируют регистрацию
    и в обычном режиме, и в сборке PyInstaller / frozen.
    """
    from app.tools.calibrator import CalibratorTool  # noqa: F401
    from app.tools.settings import SettingsTool  # noqa: F401
    from app.tools.web import LiquidationHeatmapTool, LiquidityHeatmapTool  # noqa: F401

    return get_default_registry()
