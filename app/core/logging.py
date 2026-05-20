"""
app/core/logging.py
────────────────────
Structured logging configuration.
Call setup_logging() once at application startup (in main.py).
"""

import logging
import sys
from typing import Any

from app.core.config import get_settings


def setup_logging() -> None:
    """Configure root logger for the application."""
    settings = get_settings()

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Simple format for development; JSON format preferred in production
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=log_level,
        format=fmt,
        datefmt=datefmt,
        stream=sys.stdout,
        force=True,
    )

    # Quiet noisy third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger.

    Usage:
        logger = get_logger(__name__)
        logger.info("Alert received", extra={"alert_id": alert_id})
    """
    return logging.getLogger(name)


class ContextLogger:
    """
    Thin wrapper that attaches fixed context fields to every log call.
    Useful for adding alert_id / case_id to all logs within a handler.

    Usage:
        log = ContextLogger(__name__, alert_id="abc-123")
        log.info("Processing started")
        # logs: "Processing started | alert_id=abc-123"
    """

    def __init__(self, name: str, **context: Any) -> None:
        self._logger = logging.getLogger(name)
        self._context = context

    def _fmt(self, msg: str) -> str:
        if not self._context:
            return msg
        ctx_str = " | ".join(f"{k}={v}" for k, v in self._context.items())
        return f"{msg} | {ctx_str}"

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._logger.debug(self._fmt(msg), **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._logger.info(self._fmt(msg), **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._logger.warning(self._fmt(msg), **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._logger.error(self._fmt(msg), **kwargs)
