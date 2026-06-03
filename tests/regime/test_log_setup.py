"""Tests for §15.3 log directory setup."""
from pathlib import Path

from bot.regime.log_setup import (
    setup_regime_logging,
    ensure_log_dirs,
    LOG_SUBDIRS,
    RETENTION_DAYS,
)


def test_ensure_log_dirs_creates_all(tmp_path):
    dirs = ensure_log_dirs(str(tmp_path))
    assert len(dirs) == len(LOG_SUBDIRS)
    for d in dirs:
        assert Path(d).is_dir()


def test_ensure_log_dirs_idempotent(tmp_path):
    ensure_log_dirs(str(tmp_path))
    ensure_log_dirs(str(tmp_path))
    logs_dir = tmp_path / "logs"
    subdirs = [d.name for d in logs_dir.iterdir() if d.is_dir()]
    assert set(subdirs) == set(LOG_SUBDIRS)


def test_setup_regime_logging_returns_loggers(tmp_path):
    loggers = setup_regime_logging(str(tmp_path))
    assert len(loggers) == len(LOG_SUBDIRS)
    for name in LOG_SUBDIRS:
        assert name in loggers


def test_log_files_created(tmp_path):
    setup_regime_logging(str(tmp_path))
    for subdir in LOG_SUBDIRS:
        log_file = tmp_path / "logs" / subdir / f"{subdir}.log"
        assert log_file.parent.is_dir()


def test_retention_days():
    assert RETENTION_DAYS == 90
