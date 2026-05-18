"""
Recovery CLI for overlay hard-failures — §13.6 of CLAUDE_STRATEGY_SPEC_v3.

Usage:
  python -m bot.degradation.recover_overlay EARNINGS_LOCKOUT
  python -m bot.degradation.recover_overlay --list

Runs overlay self-test, clears pauses on success, logs cleared_by.
No auto-recovery anywhere — this is the manual path.
"""
import argparse
import logging
import sys

from bot.degradation.instrument_pause_registry import InstrumentPauseRegistry
from bot.degradation.events import DegradationEventStore
from bot.overlays.registry import get_overlay

logger = logging.getLogger("degradation.recover_overlay")

DEFAULT_DB_PATH = "data/trading.db"


def recover(overlay_name: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    try:
        overlay = get_overlay(overlay_name)
    except ValueError:
        print(f"Unknown overlay: {overlay_name}")
        return False

    registry = InstrumentPauseRegistry(db_path)
    event_store = DegradationEventStore(db_path)

    print(f"Running self-test on {overlay_name}...")
    try:
        passed = overlay.self_test()
    except Exception as e:
        print(f"Self-test raised exception: {e}")
        passed = False

    if not passed:
        print(f"Self-test FAILED for {overlay_name}. Pauses NOT cleared.")
        return False

    print("Self-test passed.")

    cleared = registry.clear_by_overlay(overlay_name, cleared_by=f"recover-overlay CLI")
    if cleared:
        print(f"Cleared {len(cleared)} instrument pauses: {', '.join(cleared)}")
    else:
        print("No active pauses found for this overlay.")

    event_store.record(
        component=f"overlay:{overlay_name}",
        severity="recovery",
        trigger_reason=f"Manual recovery via CLI",
        action_taken=f"Self-test passed, cleared {len(cleared)} pauses",
        instruments_paused=cleared,
        recovery_instructions=None,
    )
    print("Audit row written.")
    return True


def list_paused(db_path: str = DEFAULT_DB_PATH) -> None:
    registry = InstrumentPauseRegistry(db_path)
    paused = registry.list_paused()
    if not paused:
        print("No instruments currently paused.")
        return
    print(f"{len(paused)} instrument(s) paused:")
    for instrument, overlay, reason in paused:
        print(f"  {instrument} — by {overlay}: {reason}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recover from overlay hard-failure",
        prog="bot recover-overlay",
    )
    parser.add_argument("overlay_name", nargs="?",
                        help="Name of overlay to recover (e.g. EARNINGS_LOCKOUT)")
    parser.add_argument("--list", action="store_true",
                        help="List all currently paused instruments")
    parser.add_argument("--db", default=DEFAULT_DB_PATH,
                        help="Path to trading database")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.list:
        list_paused(args.db)
        sys.exit(0)

    if not args.overlay_name:
        parser.print_help()
        sys.exit(1)

    success = recover(args.overlay_name, args.db)
    sys.exit(0 if success else 1)
