#!/usr/bin/env python3
"""
scripts/reset_trades.py — Delete all contaminated trade data.

Run AFTER deploying fixes, BEFORE restarting the bot.
The 29 existing trades are a mix of phantom losses (exit_price=0),
noise-triggered exits, and GBP pence bugs. Starting fresh.
"""

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
db_path = BASE_DIR / "learning_loop.db"
pos_db = BASE_DIR / "positions.db"


def main():
    # Delete all trades from learning_loop.db
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        count = cursor.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        print(f"Found {count} trades to delete in learning_loop.db")
        cursor.execute("DELETE FROM trades")
        conn.commit()
        conn.close()
        print(f"Deleted {count} trades")
    else:
        print(f"No learning_loop.db found at {db_path}")

    # Clear positions.db
    if pos_db.exists():
        conn2 = sqlite3.connect(str(pos_db))
        try:
            conn2.execute("DELETE FROM open_positions")
            conn2.execute("DELETE FROM watch_positions")
            conn2.commit()
            print("Cleared positions.db (open_positions + watch_positions)")
        except sqlite3.OperationalError as e:
            print(f"positions.db warning: {e}")
        conn2.close()
    else:
        print(f"No positions.db found at {pos_db}")

    print("\nTrade history reset. Bot will start fresh.")


if __name__ == "__main__":
    main()
