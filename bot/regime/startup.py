"""
Startup logging — §6.4 of CLAUDE_STRATEGY_SPEC_v3.

Logs full flag state to stdout, file log, and Telegram at startup.
"""
import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("regime.startup")

SPEC_VERSION = "v3"


def _config_hash(config_path: Optional[str]) -> str:
    if not config_path:
        return "n/a"
    try:
        data = Path(config_path).read_bytes()
        return hashlib.sha256(data).hexdigest()[:12]
    except (FileNotFoundError, PermissionError):
        return "unreadable"


def build_startup_message(flags, config_path: Optional[str] = None) -> str:
    lines = [
        f"[STARTUP] CogniflowAI Trading Bot",
        f"Spec version: {SPEC_VERSION}",
        flags.startup_summary(),
    ]
    if config_path:
        lines.append(f"Config file: {config_path} (sha256: {_config_hash(config_path)})")
    return "\n".join(lines)


def log_startup(flags, config_path: Optional[str] = None,
                telegram_alerts=None) -> str:
    """Log flag state to stdout, file logger, and optionally Telegram.

    Returns the startup message for testing.
    """
    msg = build_startup_message(flags, config_path)

    print(msg)
    logger.info(msg)

    if telegram_alerts is not None:
        telegram_alerts.send(f"🚀 <b>Bot Starting</b>\n<pre>{msg}</pre>")

    return msg
