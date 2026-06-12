"""Dynamic Universe v1 — daily shadow evaluator (additive, feature-flagged).

Gated by ``enable_dynamic_universe_shadow`` (default false). When the flag is false
``maybe_run`` is a pure no-op: no scheduler work, no DB write, no state change.

When explicitly enabled in a NON-production test/shadow environment it computes
structural eligibility, state transitions, candidate TTL expiry, HYPOTHETICAL
breakout candidates, slot/sector/heat contention, hypothetical IBKR routing, and
hypothetical risk/quantity — writing shadow records to universe.db.

It NEVER: submits/amends/cancels orders, calls a broker (IBKR/IG) or data provider
(EODHD), reads live positions.db, changes Layer 1 / exit / sizing behaviour, or
rewrites any config. Bars + per-instrument features arrive via an INJECTED provider
callable — there is deliberately no broker dependency in this module.

Eligibility note (P2-2): the USD-denominated price/ADV20 thresholds are evaluated
against USD-NORMALISED values. Each instrument's LOCAL price/ADV20 is converted via an
injected, broker-free FX rate (bot.universe.fx); any FX problem fails closed with a
deterministic reason code (never a local-vs-USD comparison). The FX provider is optional
and is only consulted for non-USD instruments — USD is assumed 1.0.

Sizing note: hypothetical qty/risk use the frozen breakout constants
(bot/universe/params, mirroring backtest/breakout_sim) and the instrument's LOCAL
price/ATR — a single-currency approximation for hypothetical RISK SIZING only (real
FX-normalised sizing is deferred, see docs/dynamic_universe_pre_enable_blockers.md).
These are hypothetical figures only — never orders.
"""
import hashlib
import json
import logging
from typing import Callable, Optional

from datetime import date

from backtest.breakout_strategy import compute_indicators
from bot.universe import params
from bot.universe.eligibility import structural_eligibility
from bot.universe.fx import normalize_to_usd
from bot.universe.models import (
    ELIGIBILITY_MODE_SHADOW, EXIT_SIGNAL_STATUSES, HypotheticalOrder, PositionSnapshot,
    PositionStatus, Reason, State, StateOutcome,
)
from bot.universe.registry import Registry
from bot.universe.state_machine import transition

logger = logging.getLogger("universe.evaluator")

FLAG = "enable_dynamic_universe_shadow"


def _snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, default=str).encode()
    ).hexdigest()


def _pid_hash(position_id) -> Optional[str]:
    """Non-sensitive, stable hash of a position_id (never store the raw id). None → None."""
    if position_id is None:
        return None
    return hashlib.sha256(str(position_id).encode()).hexdigest()[:16]


def _event_id(pid_hash, closed_trading_date) -> str:
    """Durable, idempotent identifier for a single position-close event (P3-9). Keyed on
    the (hashed) position identity and the close trading date, so a replayed observation of
    the SAME close yields the SAME id and never restarts cooldown; a genuinely later close
    (new date / new position) yields a new id and a fresh cooldown."""
    return hashlib.sha256(
        f"{pid_hash or '?'}|{_date_iso(closed_trading_date)}".encode()
    ).hexdigest()[:24]


def _date_iso(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _last_bar_valid(row) -> bool:
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    vals = [o, h, l, c]
    if any(v is None for v in vals):
        return False
    if any(float(v) <= 0 for v in vals):
        return False
    # Cast to native bool — numpy comparisons return numpy.bool_, which is not
    # JSON-serialisable when the snapshot is persisted.
    return bool(h >= l and h >= max(o, c) and l <= min(o, c))


class ShadowEvaluator:
    def __init__(self, registry: Registry,
                 bars_provider: Callable[[dict], Optional[dict]],
                 flags,
                 equity: float = 100_000.0,
                 evaluator_version: Optional[str] = None,
                 position_provider=None,
                 fx_provider=None):
        from bot.universe import EVALUATOR_VERSION
        self.registry = registry
        self.bars_provider = bars_provider   # callable(canonical_rec)->dict|None ; NO broker
        self.flags = flags                   # object/dict supporting .get(name)
        self.equity = float(equity)
        self.evaluator_version = evaluator_version or EVALUATOR_VERSION
        # Optional broker-free, INJECTED FxRateProvider (bot.universe.fx). Used ONLY to
        # USD-normalise non-USD price/ADV20 for eligibility (P2-2); USD instruments never
        # touch it. None is safe: USD instruments still normalise, non-USD fail closed
        # with fx_conversion_unavailable. Queried only inside _evaluate_one (never on the
        # flag-off no-op path).
        self.fx_provider = fx_provider
        # Optional broker-free, read-only PositionSnapshotProvider (task §3). When
        # injected it drives POSITION_OPEN/EXIT_ONLY/COOLDOWN organically; when None
        # the evaluator falls back to the legacy prior-state + trend-break derivation.
        # It is queried ONLY inside _evaluate_one, which the flag-off no-op never reaches.
        self.position_provider = position_provider

    def is_enabled(self) -> bool:
        try:
            return bool(self.flags.get(FLAG))
        except Exception:
            return False

    # ── public entry point ────────────────────────────────────────────
    def maybe_run(self, trading_date: str, only_ids=None) -> dict:
        if not self.is_enabled():
            # ZERO runtime effect: no DB write, no state change.
            return {"ran": False, "reason": "flag_off", "evaluated": 0}

        only = set(only_ids) if only_ids is not None else None
        expired = self.registry.expire_candidates(trading_date)
        outcomes = []
        for rec in self.registry.all_canonical():
            cid = rec["canonical_instrument_id"]
            if only is not None and cid not in only:
                continue
            if self.registry.has_history(cid, trading_date, self.evaluator_version):
                outcomes.append({"canonical_instrument_id": cid,
                                 "skipped": "idempotent"})
                continue
            outcomes.append(self._evaluate_one(rec, trading_date))

        contention = self._apply_contention(outcomes)
        logger.info("dynamic-universe shadow run %s: evaluated=%d expired_candidates=%d "
                    "selected=%d", trading_date,
                    sum(1 for o in outcomes if "skipped" not in o), expired,
                    len(contention["selected"]))
        self._log_shadow_outputs(trading_date, contention)  # §9 store/log hypothetical outputs
        return {
            "ran": True, "trading_date": trading_date,
            "evaluated": sum(1 for o in outcomes if "skipped" not in o),
            "expired_candidates": expired,
            "outcomes": outcomes,
            "selected": contention["selected"],
            "rejected": contention["rejected"],
        }

    # ── per-instrument evaluation ─────────────────────────────────────
    def _evaluate_one(self, rec: dict, trading_date: str) -> dict:
        # Evaluation sequence (task §5): (1) load current record → (2) completed-bar
        # availability → (3) authoritative position snapshot → (4) structural eligibility
        # → (5) position lifecycle → (6) cooldown transition → (7) one transition object
        # → (8) atomic state+history+lifecycle-marker persist. No partial state is written
        # before the complete transition is known.
        cid = rec["canonical_instrument_id"]
        td_date = (date.fromisoformat(trading_date)
                   if isinstance(trading_date, str) else trading_date)
        # ── 1. load current universe record ───────────────────────────
        prior = self.registry.get_state(cid)
        prior_state = prior["current_state"] if prior else None
        prior_passes = prior["consecutive_passes"] if prior else 0
        prior_failures = prior["consecutive_failures"] if prior else 0
        # P3-2: cooldown count comes from the session-based field; the legacy
        # `cooldown_until` is NOT read here. An ambiguous legacy row fails safe (blocked).
        prior_cooldown, cooldown_ambiguous = self._resolve_prior_cooldown(prior)
        prior_last_observed = prior.get("last_observed_position_status") if prior else None
        prior_last_pid_hash = prior.get("last_observed_position_id_hash") if prior else None
        prior_last_event_id = prior.get("last_processed_position_event_id") if prior else None
        prior_last_counted = prior.get("cooldown_last_counted_trading_date") if prior else None
        prior_started_date = prior.get("cooldown_started_trading_date") if prior else None
        prior_close_date = prior.get("last_position_close_trading_date") if prior else None

        # ── 2. completed-bar availability ─────────────────────────────
        src = self.bars_provider(rec) or {}
        bars = src.get("bars")
        ibkr_map = self.registry.get_gateway_ibkr(cid)
        ibkr_ok = ibkr_map is not None and rec.get("primary_gateway") == "IBKR"

        snap = {
            "corp_action_status": src.get("corp_action_status", "unavailable"),
            "sector": src.get("sector") or rec.get("sector"),
            "research_mapping_ok": bool(rec.get("research_symbol")),
            "ibkr_mapping_ok": ibkr_ok,
            "cooldown_remaining": prior_cooldown,
            "fresh_bar": bool(src.get("fresh_bar", True)) if bars is not None else False,
        }
        entry_signal = trend_break = False
        atr = price = sma50 = None
        if bars is not None and len(bars) > 0:
            ind = compute_indicators(bars)
            last = ind.iloc[-1]
            snap["bar_count"] = len(ind)
            snap["ohlc_valid"] = _last_bar_valid(last)
            cols = ["sma50", "sma200", "atr14", "adx14", "high20_excl"]
            snap["indicators_available"] = bool(ind.iloc[-1][cols].notna().all())
            n = min(20, len(ind))
            price_local = float(last["close"])
            adv20_local = float((ind["close"] * ind["volume"]).tail(n).mean())
            # ── USD currency normalisation for ELIGIBILITY thresholds (P2-2) ──
            # The price/ADV20 thresholds are USD; convert each instrument's LOCAL value
            # via the injected, broker-free FX rate. Any FX problem fails closed.
            norm = normalize_to_usd(
                price_local=price_local, adv20_local=adv20_local,
                currency=rec.get("currency"),
                price_unit=src.get("price_unit") or rec.get("price_unit"),
                trading_date=td_date, fx_provider=self.fx_provider)
            snap["currency"] = norm.currency or rec.get("currency")
            snap["price_unit"] = norm.price_unit or (src.get("price_unit") or rec.get("price_unit"))
            snap["price_local"] = price_local
            snap["adv20_local"] = adv20_local
            snap["fx_to_usd"] = norm.fx_to_usd
            snap["fx_effective_date"] = (norm.fx_effective_date.isoformat()
                                         if norm.fx_effective_date else None)
            snap["price"] = norm.price_usd       # USD-normalised → eligibility threshold input
            snap["price_usd"] = norm.price_usd
            snap["adv20_usd"] = norm.adv20_usd   # USD-normalised
            snap["fx_reason"] = norm.reason      # None when ok; a fail-closed code otherwise
            entry_signal = bool(last["entry_signal"])
            trend_break = bool(last["trend_break"])
            atr = None if last["atr14"] != last["atr14"] else float(last["atr14"])
            # Hypothetical SIZING uses the LOCAL price/ATR (single-currency approximation,
            # explicitly deferred); only ELIGIBILITY is USD-normalised above.
            price = price_local
            sma50 = None if last["sma50"] != last["sma50"] else float(last["sma50"])
        else:
            snap.update({"bar_count": 0, "price": None, "price_usd": None,
                         "adv20_usd": None, "price_local": None, "adv20_local": None,
                         "currency": rec.get("currency"), "price_unit": None,
                         "fx_to_usd": None, "fx_effective_date": None, "fx_reason": None,
                         "ohlc_valid": False, "indicators_available": False})

        # Shadow eligibility policy: unknown corporate-action data WARNS (it does not
        # block); the paper/live hard-block policy is a separate, un-wired code path.
        elig = structural_eligibility(snap, mode=ELIGIBILITY_MODE_SHADOW)

        # ── 3. authoritative position snapshot (task §2 / P3-8) ───────
        # No provider / error / malformed → UNKNOWN (fail-safe). A stale prior state is
        # NEVER used as proof of an open position.
        pos_snap = self._position_snapshot(cid, td_date)

        # ── 5. position lifecycle: durable, exactly-once exit (P3-9) ──
        session_complete = (bars is not None and len(bars) > 0
                            and bool(src.get("fresh_bar", True)))
        exit_info = self._derive_exit(pos_snap, prior_last_observed,
                                      prior_last_pid_hash, prior_last_event_id, td_date)
        # ── 6. cooldown transition inputs (P3-2 counting rules) ───────
        # A session counts at most once and only when a completed bar exists (weekends/
        # holidays/missing bars → not a completed session → never decrement); a duplicate
        # same-date run (trading_date not strictly after the last counted date) does not
        # count twice.
        countable = bool(session_complete
                         and (prior_last_counted is None
                              or str(trading_date) > str(prior_last_counted)))
        ctx = {
            "hard_disabled": bool(rec.get("hard_disabled")),
            "admin_active": bool(rec.get("administratively_active", 1)),
            "admin_paused": bool(src.get("admin_paused", False)),
            "has_open_position": pos_snap.status == PositionStatus.POSITION_OPEN,
            "position_unknown": pos_snap.status == PositionStatus.UNKNOWN,
            "exit_detected": exit_info["exit_detected"],
            "cooldown_session_countable": countable,
        }

        # ── 7. one transition object ──────────────────────────────────
        if cooldown_ambiguous:
            # P3-2: an ambiguous legacy cooldown is NOT inferred — fail safe into a
            # blocked/manual-review COOLDOWN; the session count stays NULL until a human
            # re-establishes it. (HARD_DISABLED still dominates.)
            outcome = self._ambiguous_cooldown_outcome(rec, elig, prior_passes,
                                                        prior_failures)
        else:
            outcome = transition(prior_state, prior_passes, prior_failures,
                                 prior_cooldown, elig, ctx)

        feature_snapshot = {
            **{k: snap.get(k) for k in (
                "bar_count", "price", "price_usd", "price_local", "adv20_usd",
                "adv20_local", "currency", "price_unit", "fx_to_usd",
                "fx_effective_date", "fx_reason", "indicators_available",
                "ohlc_valid", "corp_action_status", "sector", "fresh_bar")},
            "entry_signal": entry_signal, "trend_break": trend_break,
            "atr14": atr, "sma50": sma50,
            "primary_gateway": rec.get("primary_gateway"),
            "ibkr_mapping_ok": ibkr_ok,
            # non-sensitive operational status (never account ids / quantities / prices)
            "position_status": pos_snap.status.value,
        }
        fhash = _snapshot_hash(feature_snapshot)

        # ── persisted cooldown + exit-event bookkeeping ───────────────
        cooldown_remaining = (None if cooldown_ambiguous else int(outcome.cooldown_remaining))
        started_date = trading_date if outcome.cooldown_started else prior_started_date
        last_counted = trading_date if outcome.cooldown_counted else prior_last_counted
        if exit_info["exit_detected"]:
            last_event_id = exit_info["event_id"]
            close_date = (exit_info["closed_trading_date"].isoformat()
                          if exit_info["closed_trading_date"] else None)
        else:
            last_event_id = prior_last_event_id
            close_date = prior_close_date
        cur_pid_hash = _pid_hash(pos_snap.position_id) or prior_last_pid_hash

        state_rec = {
            "canonical_instrument_id": cid,
            "current_state": outcome.new_state.value,
            "previous_state": prior_state,
            "reason_codes": outcome.reason_codes,
            "consecutive_passes": outcome.consecutive_passes,
            "consecutive_failures": outcome.consecutive_failures,
            # legacy compatibility metadata ONLY (deprecated; not read by runtime logic):
            "cooldown_until": (None if cooldown_remaining is None else str(cooldown_remaining)),
            "evaluated_trading_date": trading_date,
            "feature_snapshot_hash": fhash,
            "evaluator_version": self.evaluator_version,
            # P3-2 canonical session-based cooldown:
            "cooldown_started_trading_date": started_date,
            "cooldown_sessions_remaining": cooldown_remaining,
            "cooldown_last_counted_trading_date": last_counted,
            "cooldown_release_estimate": None,   # never authoritative without a calendar
            # P3-9 durable exit markers:
            "last_observed_position_status": self._post_observed_status(pos_snap),
            "last_observed_position_id_hash": cur_pid_hash,
            "last_processed_position_event_id": last_event_id,
            "last_position_close_trading_date": close_date,
        }
        history_rec = {
            "canonical_instrument_id": cid, "trading_date": trading_date,
            "prior_state": prior_state, "new_state": outcome.new_state.value,
            "reason_codes": outcome.reason_codes,
            "feature_snapshot": feature_snapshot, "feature_snapshot_hash": fhash,
            "evaluator_version": self.evaluator_version,
        }
        # ── 8. atomic state + history + lifecycle-marker persist (P3-3) ─
        self.registry.persist_transition_atomic(state_rec, history_rec)

        return {
            "canonical_instrument_id": cid,
            "new_state": outcome.new_state.value,
            "prior_state": prior_state,
            "reason_codes": outcome.reason_codes,
            "entry_signal": entry_signal, "trend_break": trend_break,
            "price": price, "atr14": atr, "adv20": snap.get("adv20_usd"),
            "sector": snap.get("sector"),
            "spread": src.get("spread"),
            "primary_gateway": rec.get("primary_gateway"),
        }

    # ── cooldown legacy resolution (P3-2) ─────────────────────────────
    @staticmethod
    def _resolve_prior_cooldown(prior) -> tuple:
        """Return (remaining_sessions:int, ambiguous:bool) from the prior state row.

        Reads ONLY the session-based `cooldown_sessions_remaining` (P3-2). If that column
        is NULL but the deprecated legacy `cooldown_until` holds a non-zero value, the
        count is AMBIGUOUS — it is NOT inferred; the caller fails safe into a blocked
        manual-review state.
        """
        if prior is None:
            return 0, False
        sr = prior.get("cooldown_sessions_remaining")
        if sr is not None:
            return int(sr), False
        legacy = prior.get("cooldown_until")
        if legacy is None or str(legacy).strip() in ("", "0"):
            return 0, False          # unambiguous: not in cooldown
        return 0, True               # legacy non-zero count, no v2 field → ambiguous

    def _ambiguous_cooldown_outcome(self, rec, elig, prior_passes, prior_failures):
        """Fail-safe COOLDOWN outcome for an ambiguous legacy cooldown row (P3-2):
        blocked/manual-review, no inferred session count. HARD_DISABLED still dominates."""
        reasons = list(elig.reason_codes)
        if bool(rec.get("hard_disabled")):
            if Reason.HARD_DISABLED not in reasons:
                reasons.append(Reason.HARD_DISABLED)
            return StateOutcome(State.HARD_DISABLED, 0, 0, reasons, 0)
        for r in (Reason.IN_COOLDOWN, Reason.COOLDOWN_LEGACY_AMBIGUOUS):
            if r not in reasons:
                reasons.append(r)
        # cooldown_remaining is left semantically NULL by the caller; the 0 here is unused.
        return StateOutcome(State.COOLDOWN, prior_passes, prior_failures, reasons, 0)

    # ── broker-free position snapshot (task §2 / P3-8) ────────────────
    def _position_snapshot(self, cid: str, td_date) -> PositionSnapshot:
        """Resolve an authoritative PositionSnapshot from the INJECTED provider.

        No provider configured → UNKNOWN (never a stale prior-state assumption, P3-8).
        Any provider exception / timeout / malformed / unrecognised value → UNKNOWN
        (fail-safe). The provider is broker-free by contract; this makes no broker access.
        """
        if self.position_provider is None:
            return PositionSnapshot(status=PositionStatus.UNKNOWN)
        try:
            raw = self.position_provider.get_position_status(cid, td_date)
        except Exception:
            logger.warning("position provider error for %s on %s; treating as UNKNOWN",
                           cid, td_date)
            return PositionSnapshot(status=PositionStatus.UNKNOWN)
        return self._coerce_snapshot(raw)

    @staticmethod
    def _coerce_snapshot(raw) -> PositionSnapshot:
        """Coerce a provider return (PositionSnapshot | PositionStatus | str) into a
        PositionSnapshot; anything unrecognised → UNKNOWN (fail-safe)."""
        if isinstance(raw, PositionSnapshot):
            if isinstance(raw.status, PositionStatus):
                return raw
            return PositionSnapshot(status=PositionStatus.UNKNOWN)
        if isinstance(raw, PositionStatus):
            return PositionSnapshot(status=raw)
        try:
            return PositionSnapshot(status=PositionStatus(raw))
        except (ValueError, TypeError):
            return PositionSnapshot(status=PositionStatus.UNKNOWN)

    @staticmethod
    def _post_observed_status(pos_snap: PositionSnapshot) -> str:
        """The status to PERSIST as `last_observed_position_status` for the NEXT run's
        open→flat detection. An exit signal is recorded as NO_POSITION (the position is
        now flat) so the following flat session is not mistaken for a fresh open→flat;
        UNKNOWN is recorded as-is so OPEN→UNKNOWN→NO_POSITION cannot false-trigger."""
        if pos_snap.status in EXIT_SIGNAL_STATUSES:
            return PositionStatus.NO_POSITION.value
        return pos_snap.status.value

    def _derive_exit(self, pos_snap, prior_last_observed, prior_last_pid_hash,
                     prior_last_event_id, td_date) -> dict:
        """Detect a durable, EXACTLY-ONCE open→flat exit (P3-9).

        An exit starts cooldown when EITHER:
          * the provider reports an explicit durable exit signal (POSITION_EXITED, or the
            deprecated transient POSITION_EXITED_TODAY); OR
          * an authoritative OPEN→NO_POSITION transition is observed (prior persisted
            observed status was POSITION_OPEN, current is NO_POSITION) AND durable evidence
            distinguishes a real exit from a provider glitch (a closed_trading_date or a
            position_id).
        Cooldown is NOT started from UNKNOWN→NO_POSITION, a stale open with no provider, a
        provider exception, or a no-evidence open→flat. A durable position_event_id is the
        dedup key: a replayed close (same event id) never restarts cooldown.

        Returns {exit_detected, event_id, closed_trading_date(date|None), position_id_hash}.
        """
        pid_hash = _pid_hash(pos_snap.position_id)
        result = {"exit_detected": False, "event_id": None,
                  "closed_trading_date": None, "position_id_hash": pid_hash}
        status = pos_snap.status

        if status in EXIT_SIGNAL_STATUSES:
            closed = pos_snap.closed_trading_date or td_date
            event_id = _event_id(pid_hash or prior_last_pid_hash, closed)
            if event_id != prior_last_event_id:
                result.update(exit_detected=True, event_id=event_id, closed_trading_date=closed)
            return result

        if (status == PositionStatus.NO_POSITION
                and prior_last_observed == PositionStatus.POSITION_OPEN.value):
            has_evidence = (pos_snap.closed_trading_date is not None
                            or pos_snap.position_id is not None)
            if has_evidence:
                closed = pos_snap.closed_trading_date or td_date
                event_id = _event_id(pid_hash or prior_last_pid_hash, closed)
                if event_id != prior_last_event_id:
                    result.update(exit_detected=True, event_id=event_id,
                                  closed_trading_date=closed)
            # no durable evidence → indistinguishable from a glitch → no cooldown.
        return result

    # ── hypothetical slot / sector / heat contention ──────────────────
    def _apply_contention(self, outcomes: list) -> dict:
        live = [o for o in outcomes if "skipped" not in o]
        open_now = [o for o in live if o["new_state"] in
                    (State.POSITION_OPEN.value, State.EXIT_ONLY.value)]
        candidates = [o for o in live
                      if o["new_state"] == State.ENTRY_ELIGIBLE.value and o["entry_signal"]]
        candidates.sort(key=params.candidate_sort_key)

        slots = params.MAX_OPEN_POSITIONS - len(open_now)
        sector_counts = {}
        for o in open_now:
            if o.get("sector"):
                sector_counts[o["sector"]] = sector_counts.get(o["sector"], 0) + 1
        heat_used = 0.0
        selected, rejected = [], []

        for c in candidates:
            atr, price = c.get("atr14"), c.get("price")
            if not atr or not price or atr <= 0:
                rejected.append({**c, "rejected_reason": Reason.INDICATORS_UNAVAILABLE})
                continue
            if len(selected) >= max(slots, 0):
                rejected.append({**c, "rejected_reason": Reason.SLOT_CAP_REACHED})
                continue
            sector = c.get("sector")
            if sector and sector_counts.get(sector, 0) >= params.MAX_POSITIONS_PER_SECTOR:
                rejected.append({**c, "rejected_reason": Reason.SECTOR_CAP_REACHED})
                continue
            order = self._hypothetical_order(c, atr, price)
            if (heat_used + order.risk_usd) / self.equity > params.MAX_PORTFOLIO_HEAT + 1e-12:
                rejected.append({**c, "rejected_reason": Reason.PORTFOLIO_HEAT_EXCEEDED})
                continue
            heat_used += order.risk_usd
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
            selected.append({**c, "slot_rank": len(selected) + 1,
                             "hypothetical_order": order.__dict__})

        return {"selected": selected, "rejected": rejected}

    def _log_shadow_outputs(self, trading_date: str, contention: dict) -> None:
        """§9: store/log the hypothetical contention outputs (never PF/returns)."""
        for s in contention["selected"]:
            o = s["hypothetical_order"]
            logger.info(
                "shadow %s SELECT %s rank=%d gateway=%s qty=%d risk_usd=%.2f "
                "notional_usd=%.2f source=%s", trading_date,
                s["canonical_instrument_id"], s["slot_rank"], o["primary_gateway"],
                o["qty"], o["risk_usd"], o["notional_usd"],
                s.get("primary_gateway"))
        for r in contention["rejected"]:
            logger.info("shadow %s REJECT %s reason=%s sector=%s adv20=%s",
                        trading_date, r["canonical_instrument_id"],
                        r.get("rejected_reason"), r.get("sector"), r.get("adv20"))

    def _hypothetical_order(self, c: dict, atr: float, price: float) -> HypotheticalOrder:
        stop_distance = params.INITIAL_STOP_ATR_MULT * atr
        initial_stop = price - stop_distance
        qty_risk = int((self.equity * params.RISK_PER_TRADE) // stop_distance) if stop_distance > 0 else 0
        qty_notional = int((self.equity * params.MAX_NOTIONAL_PCT) // price) if price > 0 else 0
        qty = max(min(qty_risk, qty_notional), 0)
        return HypotheticalOrder(
            canonical_instrument_id=c["canonical_instrument_id"],
            primary_gateway=c.get("primary_gateway", "IBKR"),
            entry_price=price, initial_stop=initial_stop, qty=qty,
            risk_usd=qty * stop_distance, notional_usd=qty * price,
        )
