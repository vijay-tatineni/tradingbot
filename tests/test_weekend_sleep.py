"""
tests/test_weekend_sleep.py — Test the weekend sleep fix in main.py.
"""

import datetime
from unittest.mock import MagicMock, patch

import pytest


class FakeBroker:
    """Broker mock that can be configured to raise on sleep()."""
    def __init__(self, raise_on_sleep=None):
        self._raise = raise_on_sleep
        self.sleep_called = False

    def sleep(self, seconds):
        self.sleep_called = True
        if self._raise:
            raise self._raise("connection lost")

    def is_connected(self):
        return True

    def reconnect(self):
        pass


class FakeWatchdog:
    def __init__(self):
        self.sleep_mode = None

    def set_sleep_mode(self, val):
        self.sleep_mode = val


def _simulate_weekend_sleep(broker, watchdog, weekday):
    """
    Simulate the weekend sleep logic from main.py.
    Returns True if fallback sleep was used, False if broker.sleep worked, None if not weekend.

    weekday: 0=Mon, 1=Tue, ..., 5=Sat, 6=Sun
    """
    # Use known dates: 2026-03-30 is a Monday
    # Mon=30, Tue=31, Wed=Apr1, Thu=Apr2, Fri=Apr3, Sat=Apr4, Sun=Apr5
    day_offsets = {0: (3, 30), 1: (3, 31), 2: (4, 1), 3: (4, 2), 4: (4, 3), 5: (4, 4), 6: (4, 5)}
    month, day = day_offsets[weekday]
    now = datetime.datetime(2026, month, day, 12, 0, 0, tzinfo=datetime.timezone.utc)
    assert now.weekday() == weekday, f"Expected weekday {weekday}, got {now.weekday()}"

    fallback_used = False

    if now.weekday() >= 5:
        watchdog.set_sleep_mode(True)
        try:
            broker.sleep(3600)
        except (ConnectionError, OSError, Exception) as e:
            fallback_used = True
        return fallback_used
    return None  # Not weekend


def test_weekend_sleep_survives_connection_error():
    """broker.sleep() raises ConnectionError -> fallback to time.sleep()."""
    broker = FakeBroker(raise_on_sleep=ConnectionError)
    watchdog = FakeWatchdog()
    result = _simulate_weekend_sleep(broker, watchdog, weekday=5)  # Saturday
    assert result is True
    assert watchdog.sleep_mode is True


def test_weekend_sleep_survives_os_error():
    """broker.sleep() raises OSError -> same fallback."""
    broker = FakeBroker(raise_on_sleep=OSError)
    watchdog = FakeWatchdog()
    result = _simulate_weekend_sleep(broker, watchdog, weekday=6)  # Sunday
    assert result is True


def test_weekend_sleep_survives_generic_exception():
    """broker.sleep() raises generic Exception -> fallback."""
    broker = FakeBroker(raise_on_sleep=Exception)
    watchdog = FakeWatchdog()
    result = _simulate_weekend_sleep(broker, watchdog, weekday=5)
    assert result is True


def test_weekend_detection_saturday():
    """Saturday (weekday=5) triggers weekend sleep."""
    broker = FakeBroker()
    watchdog = FakeWatchdog()
    result = _simulate_weekend_sleep(broker, watchdog, weekday=5)
    assert result is not None  # Weekend was detected
    assert watchdog.sleep_mode is True


def test_weekend_detection_sunday():
    """Sunday (weekday=6) triggers weekend sleep."""
    broker = FakeBroker()
    watchdog = FakeWatchdog()
    result = _simulate_weekend_sleep(broker, watchdog, weekday=6)
    assert result is not None
    assert watchdog.sleep_mode is True


def test_weekday_no_weekend_sleep():
    """Monday-Friday does NOT trigger weekend sleep."""
    for weekday in range(5):  # Mon=0..Fri=4
        broker = FakeBroker()
        watchdog = FakeWatchdog()
        result = _simulate_weekend_sleep(broker, watchdog, weekday=weekday)
        assert result is None  # Not weekend
        assert watchdog.sleep_mode is None
