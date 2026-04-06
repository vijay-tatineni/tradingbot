"""
tests/test_profiles.py
Test multi-instance profile handling (for when it's implemented).
Tests validate the profile concept and path resolution logic.
"""

import pytest
from pathlib import Path


BASE_DIR = Path(__file__).parent.parent


# ── Profile logic (standalone, no imports needed) ──────────────

class Profile:
    """Lightweight profile concept for testing resolution logic."""

    def __init__(self, name=None, base_dir=None):
        self.name = name  # None = default profile
        self.base_dir = base_dir or BASE_DIR

    def instruments_file(self, filename='instruments.json'):
        """Resolve instruments file path."""
        return str(self.base_dir / filename)

    def db_path(self, db_name='learning_loop.db'):
        """Resolve database path. Named profiles get a suffix."""
        if self.name:
            stem = Path(db_name).stem
            ext = Path(db_name).suffix
            return f"{stem}_{self.name}{ext}"
        return db_name

    def label(self):
        """Profile label for Telegram prefixing."""
        if self.name:
            return self.name.upper()
        return 'PAPER'

    def settings_override(self, instruments_settings, account=None, port=None, client_id=None):
        """Profile with account/port/client_id should override instruments.json settings."""
        result = dict(instruments_settings)
        if account is not None:
            result['account'] = account
        if port is not None:
            result['port'] = port
        if client_id is not None:
            result['client_id'] = client_id
        return result


# ── Tests ──────────────────────────────────────────────────────

def test_profile_instruments_file_resolution():
    """Profile 'paper' with instruments_file 'instruments.json'
    should resolve to BASE_DIR / 'instruments.json'."""
    p = Profile('paper', BASE_DIR)
    path = p.instruments_file('instruments.json')
    assert path == str(BASE_DIR / 'instruments.json')


def test_profile_db_path_default():
    """Default profile: db_path('learning_loop.db') -> 'learning_loop.db'"""
    p = Profile(name=None)
    assert p.db_path('learning_loop.db') == 'learning_loop.db'


def test_profile_db_path_named():
    """Named profile 'live': db_path('learning_loop.db') -> 'learning_loop_live.db'"""
    p = Profile(name='live')
    assert p.db_path('learning_loop.db') == 'learning_loop_live.db'


def test_profile_settings_override():
    """Profile with account/port/client_id should override instruments.json settings."""
    base_settings = {
        'account': 'DUQ141950',
        'port': 4000,
        'client_id': 1,
        'host': '127.0.0.1',
    }
    p = Profile(name='live')
    overridden = p.settings_override(base_settings, account='U12345', port=4001, client_id=2)
    assert overridden['account'] == 'U12345'
    assert overridden['port'] == 4001
    assert overridden['client_id'] == 2
    assert overridden['host'] == '127.0.0.1'  # not overridden


def test_profile_label():
    """Profile label should be 'PAPER' or 'LIVE' for Telegram prefixing."""
    default = Profile(name=None)
    assert default.label() == 'PAPER'

    live = Profile(name='live')
    assert live.label() == 'LIVE'

    paper = Profile(name='paper')
    assert paper.label() == 'PAPER'
