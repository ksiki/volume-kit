"""Мост между стандартным logging и Qt-интерфейсом."""

import logging
import sys
from datetime import datetime

from PySide6.QtCore import QObject, Signal


class QtLogBridge(QObject):
    """Мост между стандартным logging и Qt-интерфейсом."""

    log_received = Signal(dict)


class _QtLogHandler(logging.Handler):
    def __init__(self, bridge: QtLogBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
            self._bridge.log_received.emit(entry)
        except Exception:
            pass


def install_qt_logging(bridge: QtLogBridge) -> logging.Handler:
    """Подключает перехват всех логов Python к QtLogBridge без дублирования."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for handler in list(root.handlers):
        if isinstance(handler, _QtLogHandler):
            root.removeHandler(handler)
        elif getattr(sys, "frozen", False):
            root.removeHandler(handler)

    qt_handler = _QtLogHandler(bridge)
    qt_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(qt_handler)
    return qt_handler
