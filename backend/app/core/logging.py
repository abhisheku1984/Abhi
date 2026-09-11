from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    """Structured logs for developers; users only ever see friendly messages (§37)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir = settings.data_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
    )
    file_handler = logging.FileHandler(log_dir / "studio.log", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(file_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.DEBUG else logging.INFO
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str = "studio"):
    configure_logging()
    return structlog.get_logger(name)


log = get_logger("studio")
