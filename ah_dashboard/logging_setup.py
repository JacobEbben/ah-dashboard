"""Logging: console output plus a rotating error log file.

Error details (upstream responses, exceptions, stack traces) go to the log file
only; users get a generic message with a short reference ID that matches the
log entry. Tokens are never logged.
"""

from __future__ import annotations

import logging
import os
import uuid
from logging.handlers import RotatingFileHandler

from flask import Flask

PACKAGE_LOGGER = "ah_dashboard"
_MARK = "_ah_dashboard_handler"  # tags handlers we added, so re-setup replaces them
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure(app: Flask) -> None:
    level = getattr(logging, str(app.config["LOG_LEVEL"]).upper(), logging.INFO)
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _MARK, False):
            logger.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    setattr(console, _MARK, True)
    logger.addHandler(console)

    path = app.config.get("ERROR_LOG_FILE")
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=app.config["ERROR_LOG_MAX_BYTES"],
            backupCount=app.config["ERROR_LOG_BACKUPS"],
            encoding="utf-8",
            delay=True,  # create the file on the first error, not at startup
        )
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        setattr(file_handler, _MARK, True)
        logger.addHandler(file_handler)


def new_reference() -> str:
    """Short ID shown to the user and written next to the logged details."""
    return uuid.uuid4().hex[:10]


def log_error(logger: logging.Logger, message: str, *args, exc_info=False) -> str:
    """Log an error with a fresh reference ID and return that ID."""
    ref = new_reference()
    logger.error("[ref %s] " + message, ref, *args, exc_info=exc_info)
    return ref
