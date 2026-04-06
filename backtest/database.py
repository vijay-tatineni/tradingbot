"""
backtest/database.py — SQLite schema and helpers for backtest.db.

Stores historical OHLCV data and walk-forward results.
Database lives at ~/trading/backtest.db (single file, shared across profiles).
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "backtest.db"


def get_connection() -> sqlite3.Connection:
    """Open (or create) backtest.db and ensure schema exists."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    _create_schema(conn)
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            datetime TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER DEFAULT 0,
            downloaded_at TEXT NOT NULL,
            PRIMARY KEY (symbol, timeframe, datetime)
        );

        CREATE TABLE IF NOT EXISTS wf_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT,
            is_pnl REAL,
            is_profit_factor REAL,
            is_win_rate REAL,
            is_trade_count INTEGER,
            oos_pnl REAL,
            oos_profit_factor REAL,
            oos_win_rate REAL,
            oos_trade_count INTEGER,
            wf_efficiency REAL,
            best_stop_pct REAL,
            best_tp_pct REAL,
            param_stability TEXT,
            verdict TEXT,
            train_months INTEGER,
            test_months INTEGER
        );

        CREATE TABLE IF NOT EXISTS optimise_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            best_stop_pct REAL,
            best_tp_pct REAL,
            best_rsi_period INTEGER,
            best_rsi_oversold INTEGER,
            best_rsi_overbought INTEGER,
            best_wr_period INTEGER,
            best_adx_threshold INTEGER,
            best_ma_period INTEGER,
            wf_efficiency REAL,
            oos_pnl REAL,
            oos_profit_factor REAL,
            oos_win_rate REAL,
            oos_trade_count INTEGER,
            current_oos_pnl REAL,
            improvement_pct REAL,
            combos_tested INTEGER,
            duration_seconds REAL
        );

        CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol
            ON ohlcv(symbol, timeframe);
        CREATE INDEX IF NOT EXISTS idx_wf_results
            ON wf_results(run_date, symbol);
        CREATE INDEX IF NOT EXISTS idx_optimise_results
            ON optimise_results(run_date, symbol);
    """)


def store_bars(conn: sqlite3.Connection, symbol: str, timeframe: str,
               bars: list[dict]) -> int:
    """
    Insert or replace OHLCV bars into the database.
    Returns number of rows written.
    """
    now = datetime.utcnow().isoformat()
    rows = [
        (symbol, timeframe, b["datetime"], b["open"], b["high"],
         b["low"], b["close"], b.get("volume", 0), now)
        for b in bars
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv "
        "(symbol, timeframe, datetime, open, high, low, close, volume, downloaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def load_bars(conn: sqlite3.Connection, symbol: str,
              timeframe: str) -> pd.DataFrame:
    """Load OHLCV bars for a symbol+timeframe into a DataFrame."""
    df = pd.read_sql_query(
        "SELECT datetime, open, high, low, close, volume "
        "FROM ohlcv WHERE symbol = ? AND timeframe = ? "
        "ORDER BY datetime",
        conn,
        params=(symbol, timeframe),
    )
    if not df.empty:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df


def data_age_hours(conn: sqlite3.Connection, symbol: str,
                   timeframe: str) -> float | None:
    """
    How many hours since the most recent download for this symbol+timeframe.
    Returns None if no data exists.
    """
    row = conn.execute(
        "SELECT MAX(downloaded_at) FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    downloaded = datetime.fromisoformat(row[0])
    return (datetime.utcnow() - downloaded).total_seconds() / 3600


def store_optimise_result(conn: sqlite3.Connection, result: dict) -> None:
    """Persist a single optimise result row."""
    conn.execute(
        "INSERT INTO optimise_results "
        "(run_date, symbol, best_stop_pct, best_tp_pct, "
        "best_rsi_period, best_rsi_oversold, best_rsi_overbought, "
        "best_wr_period, best_adx_threshold, best_ma_period, "
        "wf_efficiency, oos_pnl, oos_profit_factor, oos_win_rate, "
        "oos_trade_count, current_oos_pnl, improvement_pct, "
        "combos_tested, duration_seconds) "
        "VALUES (:run_date, :symbol, :best_stop_pct, :best_tp_pct, "
        ":best_rsi_period, :best_rsi_oversold, :best_rsi_overbought, "
        ":best_wr_period, :best_adx_threshold, :best_ma_period, "
        ":wf_efficiency, :oos_pnl, :oos_profit_factor, :oos_win_rate, "
        ":oos_trade_count, :current_oos_pnl, :improvement_pct, "
        ":combos_tested, :duration_seconds)",
        result,
    )
    conn.commit()


def store_wf_result(conn: sqlite3.Connection, result: dict) -> None:
    """Persist a single walk-forward result row."""
    conn.execute(
        "INSERT INTO wf_results "
        "(run_date, symbol, timeframe, is_pnl, is_profit_factor, is_win_rate, "
        "is_trade_count, oos_pnl, oos_profit_factor, oos_win_rate, "
        "oos_trade_count, wf_efficiency, best_stop_pct, best_tp_pct, "
        "param_stability, verdict, train_months, test_months) "
        "VALUES (:run_date, :symbol, :timeframe, :is_pnl, :is_profit_factor, "
        ":is_win_rate, :is_trade_count, :oos_pnl, :oos_profit_factor, "
        ":oos_win_rate, :oos_trade_count, :wf_efficiency, :best_stop_pct, "
        ":best_tp_pct, :param_stability, :verdict, :train_months, :test_months)",
        result,
    )
    conn.commit()
