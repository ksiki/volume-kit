"""Единая настройка логирования приложения."""

import logging

LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)-10s | %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"


def configure_logging(console: bool = True) -> None:
    """Настраивает корневой логгер приложения (консольный обработчик по умолчанию)."""
    if console:
        logging.basicConfig(
            level=logging.INFO,
            format=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT,
        )
        return

    root = logging.getLogger()
    root.setLevel(logging.INFO)
