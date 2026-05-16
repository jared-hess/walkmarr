"""Logging setup for Walkmarr runtime sessions."""

from __future__ import annotations

import logging
import os
from pathlib import Path


LOGGER_NAME = "walkmarr"


def default_log_path() -> Path:
    """Return default persistent log file path."""
    return Path("~/.local/state/walkmarr/walkmarr.log").expanduser()


def configure_file_logging(*, verbose: bool) -> Path:
    """Configure or update Walkmarr file logger and return log path."""
    configured_path_raw = os.environ.get("WALKMARR_LOG_PATH")
    log_path = Path(configured_path_raw).expanduser() if configured_path_raw else default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    active_handler: logging.Handler | None = None
    target_path = str(log_path.resolve(strict=False))

    for handler in list(logger.handlers):
        marker = getattr(handler, "_walkmarr_log_path", None)
        if marker == target_path:
            active_handler = handler
            continue
        logger.removeHandler(handler)
        handler.close()

    if active_handler is None:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        setattr(file_handler, "_walkmarr_log_path", target_path)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)

    logger.info("Logging initialized at %s", log_path)
    return log_path
