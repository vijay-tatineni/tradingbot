"""
§15.3: Log file setup for regime subsystems.

Creates daily-rotated log directories. 90-day retention.
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_SUBDIRS = [
    "regime",
    "routing",
    "shadow",
    "degradation",
    "pauses",
    "calendar_edits",
]

RETENTION_DAYS = 90


def setup_regime_logging(base_dir: str) -> dict[str, logging.Logger]:
    logs_dir = Path(base_dir) / "logs"
    loggers = {}

    for subdir in LOG_SUBDIRS:
        log_dir = logs_dir / subdir
        log_dir.mkdir(parents=True, exist_ok=True)

        logger_name = f"regime.{subdir}" if subdir != "regime" else "regime"
        log = logging.getLogger(logger_name)

        if not log.handlers:
            handler = TimedRotatingFileHandler(
                filename=str(log_dir / f"{subdir}.log"),
                when="midnight",
                backupCount=RETENTION_DAYS,
                utc=True,
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            ))
            log.addHandler(handler)
            log.setLevel(logging.INFO)

        loggers[subdir] = log

    return loggers


def ensure_log_dirs(base_dir: str) -> list[str]:
    logs_dir = Path(base_dir) / "logs"
    created = []
    for subdir in LOG_SUBDIRS:
        log_dir = logs_dir / subdir
        log_dir.mkdir(parents=True, exist_ok=True)
        created.append(str(log_dir))
    return created
