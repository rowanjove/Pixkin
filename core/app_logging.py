import logging
import sys
from logging.handlers import RotatingFileHandler

from core.paths import logs_dir
from core.privacy import SecretRedactionFilter


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("desktop_pet")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"
    )
    file_handler = RotatingFileHandler(
        logs_dir() / "pixkin.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SecretRedactionFilter())
    logger.addHandler(file_handler)

    if not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(SecretRedactionFilter())
        logger.addHandler(console)
    return logger
