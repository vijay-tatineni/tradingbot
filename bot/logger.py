"""
bot/logger.py
Centralised logging — writes to stdout and ~/trading/portfolio_bot.log
"""

import datetime
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_FILE = str(BASE_DIR / 'portfolio_bot.log')

def log(msg: str, level: str = "INFO") -> None:
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] [{level}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass  # never let logging crash the bot

def separator(title: str = "") -> None:
    line = f"{'─'*20} {title} {'─'*20}" if title else "─" * 60
    log(line)

def banner(lines: list) -> None:
    log("=" * 60)
    for line in lines:
        log(f"  {line}")
    log("=" * 60)
