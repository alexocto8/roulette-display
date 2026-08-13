"""Rotating file logging + a global exception hook.

The service runs unattended 24/7 under systemd (Restart=always). If something unexpected blows
up, we want the traceback on disk (not just in the ephemeral journal buffer) before the process
exits and systemd restarts it.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Config

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(config: Config) -> logging.Logger:
    logs_dir = config.resolve(config.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("roulette")
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    logger.handlers.clear()

    file_handler = RotatingFileHandler(
        logs_dir / "app.log",
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(stream_handler)

    def handle_uncaught(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Unhandled exception - process will exit", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_uncaught

    return logger
