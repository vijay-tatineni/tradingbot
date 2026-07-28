"""bot/reconciliation.py — broker vs. tracker position reconciliation.

Answers one question every cycle: **does the broker agree with positions.db?**

The 2026-07 safety audit found four positions open at the broker that the
tracker knew nothing about. A position the tracker cannot see gets no synthetic
stop, so it is unprotected in exactly the way the bot assumes is impossible.
The pre-existing ``layer1._reconcile_with_broker`` already repairs some of that
drift, but it does so *silently* (a WARN line, no alert) and it only inspects
symbols in ``active_instruments`` — so a broker position outside the configured
universe is invisible to it. This module closes both gaps: every divergence is
reported, and a divergent symbol is barred from new entries.

Design constraints:

* **Read-only.** Nothing here places, modifies or cancels an order. The only
  broker call is ``get_all_positions()``.
* **Alert on transition, not on state.** A standing divergence alerts once when
  it appears, not once per cycle -- a pager that fires every cycle is a pager
  that gets ignored.
* **A failed broker read is not evidence of agreement.** If the read raises, no
  block is cleared and no divergence is inferred. Phase 1 established that an
  empty position read can mean "data has not arrived yet" rather than "nothing
  is there"; that ambiguity must never silently unblock new risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from bot.logger import log

# Divergence kinds.
UNTRACKED = "untracked"          # broker holds it; positions.db does not
PHANTOM = "phantom"              # positions.db holds it; broker does not
QTY_MISMATCH = "qty_mismatch"    # both hold it, at different signed sizes

# Quantities are floats (fractional shares/CFDs), so compare with a tolerance
# rather than ==. Well below any tradeable increment.
QTY_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Divergence:
    """One symbol on which the broker and the tracker disagree."""

    symbol: str
    kind: str
    broker_qty: Optional[float]
    tracked_qty: Optional[float]

    def describe(self) -> str:
        """Human-readable detail — used verbatim in the Telegram alert."""
        if self.kind == UNTRACKED:
            return (f"{self.symbol}: broker {self.broker_qty:+g}, "
                    f"not tracked in positions.db (no synthetic stop)")
        if self.kind == PHANTOM:
            return (f"{self.symbol}: tracked {self.tracked_qty:+g}, "
                    f"absent at broker")
        return (f"{self.symbol}: broker {self.broker_qty:+g} vs "
                f"tracked {self.tracked_qty:+g}")


def signed_tracked_qty(state) -> float:
    """Signed size of a tracker position.

    The tracker is inconsistent about sign: ``init_existing`` stores the
    broker's already-signed quantity, while positions opened by the bot pass a
    magnitude alongside ``side``. Deriving the sign from ``side`` and the
    magnitude from ``abs(qty)`` is correct under both conventions.
    """
    magnitude = abs(float(getattr(state, "qty", 0.0) or 0.0))
    side = str(getattr(state, "side", "LONG") or "LONG").upper()
    return -magnitude if side == "SHORT" else magnitude


def diff_positions(broker_positions: Iterable,
                   tracked: Mapping[str, object],
                   *,
                   unmanaged: Iterable[str] = (),
                   tolerance: float = QTY_TOLERANCE) -> list[Divergence]:
    """Compare broker positions against tracker state. Pure; no I/O.

    ``broker_positions`` are ``BrokerPosition``-like (``.symbol``, ``.qty``,
    signed). ``tracked`` maps symbol -> ``PositionState``-like.

    Deliberately *not* filtered to ``active_instruments``: a broker position
    outside the configured universe is precisely the blind spot this exists to
    surface. Symbols in ``unmanaged`` are excluded, matching the semantics of
    ``layer1._reconcile_with_broker`` and ``layer1._close_all``.
    """
    skip = {s for s in unmanaged}

    broker_by_symbol: dict[str, float] = {}
    for position in broker_positions:
        symbol = position.symbol
        qty = float(position.qty or 0.0)
        if symbol in skip or abs(qty) <= tolerance:
            continue
        # Same symbol twice (multiple contracts) — aggregate rather than
        # letting the last one silently win.
        broker_by_symbol[symbol] = broker_by_symbol.get(symbol, 0.0) + qty

    tracked_by_symbol: dict[str, float] = {}
    for symbol, state in tracked.items():
        if symbol in skip:
            continue
        tracked_by_symbol[symbol] = signed_tracked_qty(state)

    divergences: list[Divergence] = []
    for symbol in sorted(set(broker_by_symbol) | set(tracked_by_symbol)):
        broker_qty = broker_by_symbol.get(symbol)
        tracked_qty = tracked_by_symbol.get(symbol)

        if broker_qty is None:
            divergences.append(Divergence(symbol, PHANTOM, None, tracked_qty))
        elif tracked_qty is None:
            divergences.append(Divergence(symbol, UNTRACKED, broker_qty, None))
        elif abs(broker_qty - tracked_qty) > tolerance:
            divergences.append(
                Divergence(symbol, QTY_MISMATCH, broker_qty, tracked_qty))

    return divergences


class ReconciliationGate:
    """Tracks which symbols are barred from new entries, and why.

    ``auto_clear=False`` (the default) is the conservative policy: a block
    survives until something explicitly clears it, so a transient broker read
    cannot quietly re-admit risk. With ``auto_clear=True`` a symbol unblocks as
    soon as a *successful* read shows agreement.

    State is in-memory, so a bot restart clears all blocks. That is a real
    limitation: it means "restart" is an implicit clear path.
    """

    def __init__(self, *, auto_clear: bool = False) -> None:
        self.auto_clear = auto_clear
        self._blocked: dict[str, Divergence] = {}

    @property
    def blocked(self) -> dict[str, Divergence]:
        return dict(self._blocked)

    def is_blocked(self, symbol: str) -> bool:
        return symbol in self._blocked

    def clear(self, symbol: str) -> bool:
        """Explicitly release a symbol. Returns True if it had been blocked."""
        return self._blocked.pop(symbol, None) is not None

    def apply(self, divergences: Sequence[Divergence]
              ) -> tuple[list[Divergence], list[str]]:
        """Fold a fresh divergence set into the gate.

        Returns ``(newly_divergent, cleared)`` — the callers alert on the
        first and log the second. A symbol already blocked is *not* re-reported
        even if the detail changed shape, so a standing problem stays quiet.
        """
        current = {d.symbol: d for d in divergences}

        newly_divergent = [d for symbol, d in current.items()
                           if symbol not in self._blocked]
        for symbol, divergence in current.items():
            self._blocked[symbol] = divergence

        cleared: list[str] = []
        if self.auto_clear:
            for symbol in list(self._blocked):
                if symbol not in current:
                    self._blocked.pop(symbol, None)
                    cleared.append(symbol)

        return newly_divergent, sorted(cleared)


class PositionReconciler:
    """Wires the diff and the gate to the live broker, tracker and alerts."""

    def __init__(self, cfg, broker, tracker, alerts=None,
                 *, auto_clear: bool = False) -> None:
        self.cfg = cfg
        self.broker = broker
        self.tracker = tracker
        self.alerts = alerts
        self.gate = ReconciliationGate(auto_clear=auto_clear)
        # Stop-protection issues already reported, so a standing problem does
        # not re-alert every cycle (same discipline as the divergence gate).
        self._reported_unprotected: set[str] = set()
        self._reported_orphans: set[str] = set()

    def is_blocked(self, symbol: str) -> bool:
        return self.gate.is_blocked(symbol)

    def _unmanaged(self) -> set[str]:
        return set(getattr(self.cfg, "unmanaged_positions", []) or [])

    def run(self, context: str = "cycle") -> list[Divergence]:
        """Reconcile once. Returns the current divergence list ([] if none).

        A broker read failure returns [] *without* touching the gate: existing
        blocks stand and no new divergence is inferred from an unreadable book.
        """
        try:
            broker_positions = list(self.broker.get_all_positions())
        except Exception as exc:  # broker/network failure — not agreement
            log(f"[Reconcile] broker read failed ({context}): {exc} — "
                f"existing blocks retained, no divergence inferred", "WARN")
            self._alert(f"⚠️ Position reconciliation could not read the broker "
                        f"({context}): {exc}. Existing entry blocks retained.")
            return []

        divergences = diff_positions(
            broker_positions, self.tracker.open, unmanaged=self._unmanaged())

        newly_divergent, cleared = self.gate.apply(divergences)

        for symbol in cleared:
            log(f"[Reconcile] {symbol} agrees again — entry block released")

        if newly_divergent:
            for divergence in newly_divergent:
                log(f"[Reconcile] DIVERGENCE ({context}): "
                    f"{divergence.describe()} — new entries blocked", "WARN")
            self._alert(self._format_alert(newly_divergent, context))

        # Protective-stop audit rides the same pass: it needs the same broker
        # read and owns the same alerting path.
        self.check_protective_stops(context)

        return divergences

    def check_protective_stops(self, context: str = "startup") -> dict:
        """Report positions without a broker-held stop, and orphaned stops.

        Two distinct failures, both invisible before PR B:

        * **unprotected** — a tracked position with no working stop at the
          broker. If the process dies, that position has no protection at all.
        * **orphaned** — a working stop with no matching broker position. The
          stop is never ratcheted and never expires, so left alone it can OPEN
          a brand-new position in the opposite direction the next time price
          touches it. That is how a flatten becomes a reversal.

        Returns ``{'unprotected': [...], 'orphaned': [...], 'verified': bool}``.
        """
        result = {"unprotected": [], "orphaned": [], "verified": False}

        if not getattr(self.broker, "supports_stop_introspection", lambda: False)():
            log(f"[Stops] broker cannot list working orders ({context}) — "
                f"stop protection is UNVERIFIED, not confirmed absent", "WARN")
            return result

        try:
            stop_symbols = set(self.broker.working_stop_symbols())
            broker_symbols = {p.symbol for p in self.broker.get_all_positions()
                              if float(p.qty or 0.0) != 0.0}
        except Exception as exc:
            log(f"[Stops] could not read working stops ({context}): {exc} — "
                f"treating as unverified", "WARN")
            return result

        result["verified"] = True
        skip = self._unmanaged()
        tracked = {s for s in self.tracker.open if s not in skip}

        result["unprotected"] = sorted(tracked - stop_symbols)
        result["orphaned"] = sorted(stop_symbols - broker_symbols - skip)

        new_unprotected = [s for s in result["unprotected"]
                           if s not in self._reported_unprotected]
        new_orphans = [s for s in result["orphaned"]
                       if s not in self._reported_orphans]
        self._reported_unprotected = set(result["unprotected"])
        self._reported_orphans = set(result["orphaned"])

        if new_unprotected or new_orphans:
            lines = [f"⚠️ <b>Protective stop check</b> ({context})", ""]
            for symbol in new_unprotected:
                log(f"[Stops] {symbol} has NO broker-held stop", "WARN")
                lines.append(f"• {symbol}: tracked position with no "
                             f"broker-held stop")
            for symbol in new_orphans:
                log(f"[Stops] orphaned stop for {symbol} — no open position",
                    "WARN")
                lines.append(f"• {symbol}: orphaned stop order, no open "
                             f"position (could open a new one)")
            self._alert("\n".join(lines))

        return result

    def _format_alert(self, divergences: Sequence[Divergence],
                      context: str) -> str:
        lines = [f"⚠️ <b>Position divergence</b> ({context}) — "
                 f"broker vs positions.db",
                 ""]
        lines += [f"• {d.describe()}" for d in divergences]
        lines += ["", "New entries are blocked for "
                       f"{'these symbols' if len(divergences) > 1 else 'this symbol'} "
                       "until resolved."]
        return "\n".join(lines)

    def _alert(self, message: str) -> None:
        if not self.alerts:
            return
        try:
            self.alerts.send(message)
        except Exception as exc:  # alerting must never break the trading loop
            log(f"[Reconcile] alert send failed: {exc}", "WARN")
