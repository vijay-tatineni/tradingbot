"""Session-wide database isolation for the test suite.

Several modules resolve their SQLite path once, at import time, from a
module-level constant anchored to the repo root::

    bot/position_tracker.py:30    DB_FILE = BASE_DIR / 'positions.db'
    bot/plugins/learning_loop.py  DB_FILE = BASE_DIR / 'learning_loop.db'
    backtest/database.py:15       DB_PATH = ...     / 'backtest.db'

Because ``sqlite3.connect`` *creates* a missing database rather than failing,
running the suite in a working tree populated those files as empty schemas.
That is how a decommissioned tree came back looking populated while holding no
data -- strictly worse than being obviously empty, because it reads as intact.

Two layers, deliberately:

1. **Redirect the known constants** to a per-session tmp directory. This is the
   primary mechanism, and it is explicit: anyone reading this file can see
   which paths move where.
2. **A safety net on** ``sqlite3.connect`` that reroutes any *other* attempt to
   open a ``.db`` sitting directly in the repo root. Layer 1 only covers the
   constants known today; the net keeps the guarantee true for paths reached by
   import-time side effects or added later, which is what the requirement --
   the full suite creates zero files in a live tree -- actually demands.

Both are test-only. No production module is changed: every constant is restored
at session end.
"""

import importlib
import os
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# (module path, attribute, filename to use inside the tmp dir)
_DB_CONSTANTS = [
    ("bot.position_tracker", "DB_FILE", "positions.db"),
    ("bot.dashboard", "_PNL_DB", "positions.db"),
    ("bot.plugins.learning_loop", "DB_FILE", "learning_loop.db"),
    ("bot.layer3_silver", "DB_FILE", "layer3_silver.db"),
    ("bot.llm.news_collector", "NEWS_DB", "news.db"),
    ("backtest.database", "DB_PATH", "backtest.db"),
]

# Names the safety net had to reroute — exposed so a test can assert on it.
redirected_connects: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def isolate_databases(tmp_path_factory):
    """Point every repo-root database at a throwaway directory."""
    data_dir = tmp_path_factory.mktemp("dbs")

    restore = []
    for module_path, attribute, filename in _DB_CONSTANTS:
        try:
            module = importlib.import_module(module_path)
        except Exception:
            continue                    # optional dependency; nothing to patch
        if not hasattr(module, attribute):
            continue
        previous = getattr(module, attribute)
        restore.append((module, attribute, previous))
        # Preserve the original type: some modules hold str, others Path.
        replacement = data_dir / filename
        setattr(module, attribute,
                replacement if isinstance(previous, Path) else str(replacement))

    original_connect = sqlite3.connect

    def guarded_connect(target, *args, **kwargs):
        """Reroute a repo-root .db into the session tmp directory."""
        try:
            as_text = str(target)
            if as_text != ":memory:" and not as_text.startswith("file:"):
                absolute = Path(os.path.abspath(as_text))
                if absolute.parent == REPO_ROOT and absolute.suffix == ".db":
                    redirected_connects.append(absolute.name)
                    target = str(data_dir / absolute.name)
        except Exception:
            pass                        # never break a connect over this
        return original_connect(target, *args, **kwargs)

    sqlite3.connect = guarded_connect
    try:
        yield data_dir
    finally:
        sqlite3.connect = original_connect
        for module, attribute, previous in restore:
            setattr(module, attribute, previous)
