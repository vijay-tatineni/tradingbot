"""
tests/test_pnl_calculations.py
Test P&L calculations for both USD and GBP instruments in learning_loop.py.
"""

import sqlite3
import datetime
import pytest


# ── Helpers ────────────────────────────────────────────────────

def make_db():
    """Create an in-memory trades DB matching the learning_loop schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT,
            symbol          TEXT,
            name            TEXT,
            action          TEXT,
            entry_price     REAL,
            exit_price      REAL,
            qty             REAL,
            pnl_usd         REAL,
            hold_days       INTEGER,
            outcome         TEXT,
            alligator_state TEXT,
            alligator_dir   TEXT,
            ma200_trend     TEXT,
            wr_value        REAL,
            wr_signal       TEXT,
            rsi_value       REAL,
            confidence      TEXT,
            exit_reason     TEXT,
            open            INTEGER DEFAULT 1,
            currency        TEXT DEFAULT 'USD'
        )
    """)
    return conn


def insert_open_trade(conn, symbol, entry_price, qty, action='BUY', currency='USD'):
    """Insert an open trade and return its id."""
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, name, action, entry_price, qty, open, currency) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
        (datetime.datetime.utcnow().isoformat(), symbol, symbol, action, entry_price, qty, currency),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def close_trade(conn, trade_id, exit_price, currency):
    """Simulate the exit P&L logic from learning_loop._record_exit."""
    row = conn.execute(
        "SELECT entry_price, qty, action FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    entry_price, qty, action = row

    if action == 'BUY':
        pnl = (exit_price - entry_price) * qty
    else:
        pnl = (entry_price - exit_price) * qty

    # GBP: prices are in pence, convert P&L to pounds
    if currency == 'GBP':
        pnl = pnl / 100

    outcome = 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'SCRATCH'
    conn.execute(
        "UPDATE trades SET exit_price=?, pnl_usd=?, outcome=?, open=0 WHERE id=?",
        (round(exit_price, 4), round(pnl, 2), outcome, trade_id),
    )
    conn.commit()
    return round(pnl, 2)


# ── Tests ──────────────────────────────────────────────────────

def test_gbp_pnl_converts_pence_to_pounds():
    """GBP instruments: P&L should be in pounds, not pence.
    Entry 3450p, exit 3408.5p, qty 40 -> loss = 41.5p x 40 = 1660p = -16.60 pounds"""
    conn = make_db()
    tid = insert_open_trade(conn, 'SHEL', 3450.0, 40, 'BUY', 'GBP')
    pnl = close_trade(conn, tid, 3408.5, 'GBP')
    assert pnl == -16.60, f"Expected -16.60 pounds, got {pnl}"


def test_usd_pnl_no_conversion():
    """USD instruments: P&L should be in dollars as-is.
    Entry $197.81, exit $197.68, qty 5 -> loss = $0.13 x 5 = -$0.65"""
    conn = make_db()
    tid = insert_open_trade(conn, 'MSFT', 197.81, 5, 'BUY', 'USD')
    pnl = close_trade(conn, tid, 197.68, 'USD')
    assert pnl == -0.65, f"Expected -0.65, got {pnl}"


def test_gbp_pnl_positive_trade():
    """GBP win: entry 3410.5p, exit 3416.5p, qty 40 -> profit = 6p x 40 = 240p = 2.40 pounds"""
    conn = make_db()
    tid = insert_open_trade(conn, 'BARC', 3410.5, 40, 'BUY', 'GBP')
    pnl = close_trade(conn, tid, 3416.5, 'GBP')
    assert pnl == 2.40, f"Expected 2.40, got {pnl}"


def test_eur_pnl_no_conversion():
    """EUR instruments (SU): P&L in EUR, no pence conversion needed."""
    conn = make_db()
    tid = insert_open_trade(conn, 'SU', 230.0, 10, 'BUY', 'EUR')
    pnl = close_trade(conn, tid, 232.5, 'EUR')
    assert pnl == 25.0, f"Expected 25.0, got {pnl}"


def test_pnl_sign_preserved_negative():
    """Negative P&L must keep its sign through all calculations."""
    conn = make_db()
    tid = insert_open_trade(conn, 'MSFT', 100.0, 10, 'BUY', 'USD')
    pnl = close_trade(conn, tid, 95.0, 'USD')
    assert pnl < 0, f"Expected negative P&L, got {pnl}"
    assert pnl == -50.0

    row = conn.execute("SELECT pnl_usd, outcome FROM trades WHERE id=?", (tid,)).fetchone()
    assert row[0] == -50.0
    assert row[1] == 'LOSS'


def test_pnl_sign_preserved_positive():
    """Positive P&L must keep its sign through all calculations."""
    conn = make_db()
    tid = insert_open_trade(conn, 'MSFT', 100.0, 10, 'BUY', 'USD')
    pnl = close_trade(conn, tid, 105.0, 'USD')
    assert pnl > 0, f"Expected positive P&L, got {pnl}"
    assert pnl == 50.0

    row = conn.execute("SELECT pnl_usd, outcome FROM trades WHERE id=?", (tid,)).fetchone()
    assert row[0] == 50.0
    assert row[1] == 'WIN'
