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

from datetime import date, datetime

from backtest.breakout_strategy import compute_indicators
from bot.universe import params
from bot.universe.eligibility import structural_eligibility
from bot.universe.fx import normalize_to_usd
from bot.universe.risk_gate import evaluate_entry_risk
from bot.universe.sizing import SizingInputs
from bot.universe.models import (
    AUTHORITATIVE_STATUSES, ELIGIBILITY_MODE_SHADOW, EligibilityResult, EXIT_SIGNAL_STATUSES,
    HypotheticalOrder, PositionSnapshot, PositionStatus, Reason, State, StateOutcome,
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


def _synth_close_event_id(canonical_instrument_id, pid_hash, opened, closed) -> str:
    """Lifecycle-safe synthetic close-event identity (R1.3 / Finding 1).

    Keyed on the canonical instrument, the (hashed) position identity, AND the lifecycle
    WINDOW (opened + closed trading dates) — so two DISTINCT lifecycles that reuse the same
    position_id and even close on the same trading date still get DISTINCT ids (their open
    dates differ). A replay of the SAME close yields the SAME id (no cooldown reset). The
    ``close:v2`` version prefix lets the scheme evolve. Never embeds a raw account id or any
    unredacted sensitive identifier (the position id is pre-hashed).

    NOTE: ``opened`` is the required lifecycle discriminator; the caller must NOT invoke this
    without it (a missing open date is treated as an ambiguous close → reconciliation, never a
    collision-prone fallback). The old ``(pid_hash, closed)``-only identity is removed.
    """
    canonical = (f"close:v2:{canonical_instrument_id}|{pid_hash or '?'}"
                 f"|{_date_iso(opened)}|{_date_iso(closed)}")
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


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
                 fx_provider=None,
                 enforce_verified_identity: bool = False,
                 identity_store=None,
                 require_candidate_source: bool = False,
                 candidate_store=None,
                 enforce_portfolio_heat: bool = False,
                 base_currency=None,
                 base_fx_provider=None,
                 portfolio_risk_provider=None,
                 risk_evaluation_time=None):
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
        # ── R2A-1 canonical-identity / verified-mapping pre-entry gate (P3-6 / P3-7) ──
        # DEFAULT-OFF: when False the evaluator behaves exactly as before (the gate is never
        # consulted, no identity table is read). When True, an instrument with no verified
        # identity / active verified listing / VERIFIED_REFERENCE_MATCH fresh IBKR mapping
        # is BLOCKED from NEW entry (reason recorded); the gate never forces liquidation,
        # alters a position, or calls a broker, and position reconciliation still dominates.
        # The gate reads ONLY universe.db via an IdentityStore on the SAME db_path.
        self.enforce_verified_identity = bool(enforce_verified_identity)
        self._identity_store = identity_store
        # ── R2B persisted candidate-source selection gate (P3-4) ──
        # DEFAULT-OFF: when False the evaluator selects exactly as before. When True, NEW-entry
        # selection consumes candidates ONLY through the persisted effective-candidate store
        # (CandidateStore.effective_candidates); an instrument with no effective candidate is
        # excluded from contention (a fail-closed reason is recorded). Candidate presence is
        # necessary but NOT sufficient — all other gates still apply. The gate affects NEW
        # entries only: existing open-position management is unchanged, and it never forces
        # liquidation or calls a broker. Reads ONLY universe.db via a CandidateStore on the
        # SAME db_path.
        self.require_candidate_source = bool(require_candidate_source)
        self._candidate_store = candidate_store
        # ── R2C FX-normalized sizing + inherited/open-book portfolio heat gate
        #    (BLOCKER-S / P3-5) ──
        # DEFAULT-OFF: when False the contention path behaves EXACTLY as before — the two new
        # broker-free seams are NEVER consulted (zero FX-provider / portfolio-provider calls)
        # and selection is bit-identical. When True, each NEW-entry candidate must additionally
        # pass FX-normalized sizing (into the account base currency, deterministic Decimal, never
        # USD-assumed) AND inherited/open-book post-trade heat (existing open positions + open
        # orders/pending intents + the proposed trade) against the base-currency limit — else it
        # is rejected with a fail-closed reason code. The gate affects NEW entries only: existing
        # open-position management is untouched, it never forces liquidation, and it never calls
        # a broker. base_currency is REQUIRED when enabled (never defaulted to USD); self.equity
        # is treated as base-currency equity. risk_evaluation_time is the INJECTED order-intent
        # time used for FX / snapshot / equity freshness (the layer never reads the wall clock).
        self.enforce_portfolio_heat = bool(enforce_portfolio_heat)
        self.base_currency = base_currency
        self.base_fx_provider = base_fx_provider
        self.portfolio_risk_provider = portfolio_risk_provider
        self.risk_evaluation_time = risk_evaluation_time
        self._run_trading_date = None        # set per run in maybe_run (R2C snapshot date-match)

    def _identity_gate_store(self):
        if self._identity_store is None:
            from bot.universe.identity_store import IdentityStore
            self._identity_store = IdentityStore(self.registry.db_path)
        return self._identity_store

    def _candidate_gate_store(self):
        if self._candidate_store is None:
            from bot.universe.candidate_store import CandidateStore
            self._candidate_store = CandidateStore(self.registry.db_path)
        return self._candidate_store

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

        # ── R2B: NEW-entry selection consumes the persisted effective-candidate store ONLY ──
        # Read effective candidates FIRST (selection on current TTL), then count the completed
        # session (read-then-count TTL). Default-off → effective_selection is None and
        # contention behaves exactly as before.
        effective_selection = None
        if self.require_candidate_source:
            effective_selection = self._candidate_gate_store().effective_candidates(trading_date)
        # R2C gate needs the evaluation trading date (snapshot date-match). Stored here so the
        # contention helper keeps its existing signature; only read when enforce_portfolio_heat.
        self._run_trading_date = trading_date
        contention = self._apply_contention(outcomes, effective_selection)
        if self.require_candidate_source:
            self._candidate_gate_store().tick_ttl_atomic(trading_date)
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
        prior_last_counted = prior.get("cooldown_last_counted_trading_date") if prior else None
        prior_started_date = prior.get("cooldown_started_trading_date") if prior else None

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

        # ── R2A-1 canonical-identity / verified-mapping gate (default-off; P3-6 / P3-7) ──
        # Additive entry block: if enabled and the instrument lacks a verified identity /
        # active verified listing / VERIFIED_REFERENCE_MATCH fresh IBKR mapping, its
        # blocking reason codes are folded into eligibility (passes→False) so it cannot
        # advance to ENTRY_ELIGIBLE. Position/reconciliation logic below is untouched.
        if self.enforce_verified_identity:
            gate = self._identity_gate_store().entry_identity_gate(rec, td_date)
            if not gate.passes:
                merged = [r for r in elig.reason_codes if r != Reason.ELIGIBLE]
                merged += [r for r in gate.reason_codes if r not in merged]
                elig = EligibilityResult(passes=False, reason_codes=merged)

        # ── 3. authoritative position snapshot (task §2 / P3-8) ───────
        # No provider / error / malformed → UNKNOWN (fail-safe). A stale prior state is
        # NEVER used as proof of an open position.
        pos_snap = self._position_snapshot(cid, td_date)

        # ── 5. position lifecycle: authoritative continuity + durable exactly-once
        #       exit (R1.1 — P3-8/P3-9). A non-authoritative observation (UNKNOWN / error /
        #       stale / future / missing provider) updates only the LATEST observed status
        #       and NEVER erases the LAST AUTHORITATIVE evidence, so an exit that happens
        #       during an outage is still detected, exactly once, when an authoritative
        #       evidence-bearing close arrives.
        cont = self._position_continuity(cid, prior, pos_snap, td_date)

        # ── 6. cooldown transition inputs (P3-2 counting rules) ───────
        # A session counts at most once and only when a completed bar exists (weekends/
        # holidays/missing bars → not a completed session → never decrement); a duplicate
        # same-date run (trading_date not strictly after the last counted date) does not
        # count twice.
        session_complete = (bars is not None and len(bars) > 0
                            and bool(src.get("fresh_bar", True)))
        countable = bool(session_complete
                         and (prior_last_counted is None
                              or str(trading_date) > str(prior_last_counted)))
        ctx = {
            "hard_disabled": bool(rec.get("hard_disabled")),
            "admin_active": bool(rec.get("administratively_active", 1)),
            "admin_paused": bool(src.get("admin_paused", False)),
            "has_open_position": cont["has_open_position"],
            "position_unknown": cont["position_unknown"],
            "reconciliation_required": cont["reconciliation_required"],
            "exit_detected": cont["exit_detected"],
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
            # non-sensitive operational status + DETERMINISTIC lifecycle markers (no
            # wall-clock): folding these into the hash makes a differing close-event /
            # authoritative status / reconciliation surface as a feature-hash change, which
            # the content-aware idempotency check treats as a conflict (R1.1 §6).
            "position_status": pos_snap.status.value,
            "latest_observed_position_status": cont["latest_observed_position_status"],
            "last_authoritative_position_status": cont["last_authoritative_position_status"],
            "position_reconciliation_required": cont["reconciliation_required"],
            "exit_detected": cont["exit_detected"],
            "position_event_id": cont["last_processed_position_event_id"],
        }
        fhash = _snapshot_hash(feature_snapshot)

        # ── persisted cooldown bookkeeping (P3-2) ─────────────────────
        cooldown_remaining = (None if cooldown_ambiguous else int(outcome.cooldown_remaining))
        started_date = trading_date if outcome.cooldown_started else prior_started_date
        last_counted = trading_date if outcome.cooldown_counted else prior_last_counted

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
            # ── R1.1 authoritative position continuity ──
            # latest observation (UNKNOWN may overwrite this) ...
            "latest_observed_position_status": cont["latest_observed_position_status"],
            "latest_observed_at": cont["latest_observed_at"],
            # ... vs last AUTHORITATIVE evidence (survives outages; never erased by UNKNOWN):
            "last_authoritative_position_status": cont["last_authoritative_position_status"],
            "last_authoritative_position_id_hash": cont["last_authoritative_position_id_hash"],
            "last_authoritative_observed_at": cont["last_authoritative_observed_at"],
            "position_reconciliation_required": cont["reconciliation_required"],
            # durable exactly-once close markers (P3-9):
            "last_processed_position_event_id": cont["last_processed_position_event_id"],
            "last_position_close_trading_date": cont["last_position_close_trading_date"],
            # P3-R1-A discriminator-qualified close-event key (reuse-violation detection):
            "last_close_event_key": cont["last_close_event_key"],
            # deprecated v2 mirror (kept = latest observed; not read by runtime logic):
            "last_observed_position_status": cont["latest_observed_position_status"],
            "last_observed_position_id_hash": cont["last_authoritative_position_id_hash"],
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
            "instrument_uid": rec.get("instrument_uid"),
            "new_state": outcome.new_state.value,
            "prior_state": prior_state,
            "reason_codes": outcome.reason_codes,
            "entry_signal": entry_signal, "trend_break": trend_break,
            "price": price, "atr14": atr, "adv20": snap.get("adv20_usd"),
            "sector": snap.get("sector"),
            "spread": src.get("spread"),
            "primary_gateway": rec.get("primary_gateway"),
            # R2C: the LISTING/instrument currency (not USD-normalised) carried for
            # FX-normalized sizing into the account base currency (default-off gate).
            "instrument_currency": snap.get("currency"),
            "listing_uid": rec.get("listing_uid"),
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
        """Resolve a PositionSnapshot from the INJECTED provider.

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

    # ── R1.1 authoritative position continuity ────────────────────────
    @staticmethod
    def _observed_date(pos_snap: PositionSnapshot, td_date):
        """Return (observed_date, malformed). A bare status (no observed_at) is taken as
        observed on the evaluation trading date. A non-date/datetime observed_at → malformed."""
        oa = pos_snap.observed_at
        if oa is None:
            return td_date, False
        if isinstance(oa, datetime):
            return oa.date(), False
        if isinstance(oa, date):
            return oa, False
        return None, True       # malformed timestamp → non-authoritative

    @staticmethod
    def _is_authoritative(pos_snap, observed_date, malformed, td_date, prior_auth_at) -> bool:
        """An observation is AUTHORITATIVE only when it is a real position status from a
        fresh, in-order snapshot. UNKNOWN, malformed/future/stale/out-of-order → False."""
        if pos_snap.status not in AUTHORITATIVE_STATUSES:
            return False
        if malformed or observed_date is None:
            return False
        if observed_date > td_date:                                  # future-dated
            return False
        if (td_date - observed_date).days > params.MAX_POSITION_SNAPSHOT_STALENESS_DAYS:
            return False                                             # stale
        if prior_auth_at:
            try:
                if observed_date < date.fromisoformat(str(prior_auth_at)):
                    return False                                    # older than last authoritative
            except ValueError:
                pass
        return True

    @staticmethod
    def _valid_lifecycle_date(v):
        """Strictly parse a lifecycle date (R2A-0.1). Accept ONLY a ``datetime.date`` /
        ``datetime`` or a strict ISO ``YYYY-MM-DD`` string. Reject ``None``, empty/malformed
        strings, impossible dates, and any other type → returns ``None`` (the caller fails
        closed). Deliberately NO permissive ``str()`` coercion of malformed values."""
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v)      # strict YYYY-MM-DD; rejects empty/malformed
            except ValueError:
                return None
        return None

    @staticmethod
    def _qualified_close_key(cid, pid_hash, opened, closed, explicit) -> str:
        """Deterministic, versioned lifecycle-qualified close key (R2A-0.1). Binds the FULL
        close lifecycle — canonical instrument id, hashed position id, valid opened & closed
        trading dates, and the provider ``close_event_id`` when supplied. The explicit id is
        folded in but is NOT itself sufficient: this key only exists when every required
        lifecycle field is present and valid (see ``_lifecycle_close``). Two closes that share an
        explicit id but differ in ANY lifecycle component (opened / closed / position hash) get
        DIFFERENT keys → provider-contract violation, never a masked replay."""
        return (f"close-key:v2:{cid}|{pid_hash}|{opened.isoformat()}|{closed.isoformat()}"
                f"|{explicit if explicit is not None else '-'}")

    def _lifecycle_close(self, cid, pos_snap: PositionSnapshot, td_date):
        """Resolve a close's lifecycle-qualified identity, or return ``None`` when the lifecycle
        evidence is insufficient/invalid (R2A-0.1 operator ruling — an explicit ``close_event_id``
        alone is NOT sufficient). Requires a ``position_id`` (→ hash), a VALID
        ``opened_trading_date`` and ``closed_trading_date``, with ``opened <= closed <= td_date``
        and ``opened`` not in the future. Any missing/malformed/impossible field → ``None`` and
        the caller fails closed to POSITION_RECONCILIATION. Never substitutes ``?`` / empty /
        raw-malformed / guessed values."""
        if pos_snap.position_id is None:
            return None                                       # missing position id
        opened = self._valid_lifecycle_date(pos_snap.opened_trading_date)
        closed = self._valid_lifecycle_date(pos_snap.closed_trading_date)
        if opened is None or closed is None:
            return None                                       # missing/malformed opened or closed
        if opened > td_date:                                  # future opened date
            return None
        if closed < opened:                                   # closed before opened
            return None
        if closed > td_date:                                  # closed after the observation/eval date
            return None
        pid_hash = _pid_hash(pos_snap.position_id)
        explicit = (str(pos_snap.close_event_id)
                    if pos_snap.close_event_id is not None else None)
        event_id = (explicit if explicit is not None
                    else _synth_close_event_id(cid, pid_hash, opened, closed))
        return {
            "qualified_key": self._qualified_close_key(cid, pid_hash, opened, closed, explicit),
            "event_id": event_id, "explicit_id": explicit, "closed": closed, "pid_hash": pid_hash,
        }

    @staticmethod
    def _stale_close(closed, prior_close) -> bool:
        """True if `closed` predates the already-processed close date (older event after a
        newer one) — such a close must be ignored, never restart a newer lifecycle."""
        if prior_close is None:
            return False
        try:
            c = closed if isinstance(closed, date) else date.fromisoformat(str(closed))
            return c < date.fromisoformat(str(prior_close))
        except (ValueError, TypeError):
            return False

    def _position_continuity(self, cid, prior, pos_snap: PositionSnapshot, td_date) -> dict:
        """Resolve the authoritative position lifecycle for one evaluation (R1.1, P3-8/P3-9).

        Separates the LATEST observation (which a non-authoritative UNKNOWN may overwrite)
        from the LAST AUTHORITATIVE evidence (which non-authoritative observations must NEVER
        erase). Detects a durable, exactly-once open→flat exit keyed off the last
        AUTHORITATIVE open; an open→flat WITHOUT explicit closure evidence sets a persistent
        `position_reconciliation_required` block (never assume flat / never manufacture an
        exit). A bare position_id is NOT closure evidence.
        """
        prior = prior or {}
        p_auth_status = prior.get("last_authoritative_position_status")
        p_auth_pid = prior.get("last_authoritative_position_id_hash")
        p_auth_at = prior.get("last_authoritative_observed_at")
        p_event = prior.get("last_processed_position_event_id")
        p_close = prior.get("last_position_close_trading_date")
        p_close_key = prior.get("last_close_event_key")   # P3-R1-A discriminator-qualified key
        recon = bool(prior.get("position_reconciliation_required"))

        observed_date, malformed = self._observed_date(pos_snap, td_date)
        authoritative = self._is_authoritative(pos_snap, observed_date, malformed,
                                               td_date, p_auth_at)
        status = pos_snap.status
        pid_hash = _pid_hash(pos_snap.position_id)
        obs_iso = (observed_date.isoformat() if observed_date else td_date.isoformat())

        # carry authoritative evidence forward by default; touch ONLY when authoritative.
        new_auth_status, new_auth_pid, new_auth_at = p_auth_status, p_auth_pid, p_auth_at
        new_event, new_close = p_event, p_close
        new_close_key = p_close_key
        exit_detected = False

        if authoritative:
            is_exit_signal = status in EXIT_SIGNAL_STATUSES
            open_to_flat = (status == PositionStatus.NO_POSITION
                            and p_auth_status == PositionStatus.POSITION_OPEN.value)

            if status == PositionStatus.POSITION_OPEN:
                recon = False                                    # confirmed open clears the block
                new_auth_status = PositionStatus.POSITION_OPEN.value
                new_auth_pid = pid_hash or p_auth_pid
                new_auth_at = obs_iso
            elif is_exit_signal or open_to_flat:
                # R2A-0.1 (operator ruling): a close is processed ONLY with COMPLETE, VALID
                # lifecycle evidence — a position_id (→ hash), a valid opened_trading_date and a
                # valid closed_trading_date, with opened <= closed <= the evaluation date and
                # opened not in the future. An explicit provider close_event_id is PREFERRED and
                # folded into the qualified key, but is NOT by itself sufficient lifecycle
                # evidence. Any missing/malformed required field → fail closed to
                # POSITION_RECONCILIATION (entry blocked, no cooldown, no processed marker,
                # authoritative OPEN anchor retained — never substitute '?'/empty/guessed values).
                lc = self._lifecycle_close(cid, pos_snap, td_date)
                if lc is None:
                    recon = True                                 # insufficient/invalid lifecycle evidence
                else:
                    qkey, closed, ev = lc["qualified_key"], lc["closed"], lc["event_id"]
                    if self._stale_close(closed, p_close):
                        # an OLDER close arriving after a newer one → ignore (never restart a
                        # newer lifecycle); the anchor is already past this close.
                        recon = False
                        new_auth_status = PositionStatus.NO_POSITION.value
                        new_auth_pid = lc["pid_hash"] or p_auth_pid
                        new_auth_at = obs_iso
                    elif qkey == p_close_key:
                        # exact replay of the already-processed close → idempotent, no reset.
                        recon = False
                        new_auth_status = PositionStatus.NO_POSITION.value
                        new_auth_pid = lc["pid_hash"] or p_auth_pid
                        new_auth_at = obs_iso
                    elif lc["explicit_id"] is not None and ev == p_event:
                        # SAME explicit close_event_id but a DIFFERENT qualified lifecycle (the
                        # opened/closed date or position hash differs) → provider-contract
                        # violation. Fail closed: keep the authoritative OPEN anchor, no cooldown,
                        # do NOT mark the event processed, no entry. Never a masked second close.
                        recon = True
                    else:
                        # genuine new close backed by complete, valid lifecycle evidence.
                        exit_detected = True
                        new_event = ev
                        new_close_key = qkey
                        new_close = closed.isoformat()
                        recon = False                            # close reconciled
                        new_auth_status = PositionStatus.NO_POSITION.value
                        new_auth_pid = lc["pid_hash"] or p_auth_pid
                        new_auth_at = obs_iso
            else:
                # authoritative NO_POSITION with NO prior authoritative open → ordinary flat.
                new_auth_status = PositionStatus.NO_POSITION.value
                new_auth_at = obs_iso
        # else: non-authoritative → latest_* updated below, authoritative evidence untouched,
        #       reconciliation block (if any) persists across the outage.

        return {
            "has_open_position": authoritative and status == PositionStatus.POSITION_OPEN,
            "position_unknown": not authoritative,
            "reconciliation_required": recon,
            "exit_detected": exit_detected,
            "latest_observed_position_status": status.value,
            "latest_observed_at": obs_iso,
            "last_authoritative_position_status": new_auth_status,
            "last_authoritative_position_id_hash": new_auth_pid,
            "last_authoritative_observed_at": new_auth_at,
            "last_processed_position_event_id": new_event,
            "last_position_close_trading_date": new_close,
            "last_close_event_key": new_close_key,
        }

    # ── hypothetical slot / sector / heat contention ──────────────────
    def _apply_contention(self, outcomes: list, effective_selection=None) -> dict:
        live = [o for o in outcomes if "skipped" not in o]
        # POSITION_RECONCILIATION conservatively counts as occupying a slot/sector: the
        # position MAY exist (ownership unresolved), so reserving its slot avoids
        # over-allocating new hypothetical entries. This preserves the pre-R1.2 behaviour
        # where the (overloaded) EXIT_ONLY uncertainty case reserved a slot (P2-C).
        # NOTE: open-position states are derived from the per-instrument transition and are
        # UNAFFECTED by candidate gating — candidate expiry/absence changes NEW-entry
        # eligibility only; existing positions keep their slot and continue to be managed.
        open_now = [o for o in live if o["new_state"] in
                    (State.POSITION_OPEN.value, State.EXIT_ONLY.value,
                     State.POSITION_RECONCILIATION.value)]
        candidates = [o for o in live
                      if o["new_state"] == State.ENTRY_ELIGIBLE.value and o["entry_signal"]]
        # ── R2B: gate NEW-entry candidates on the persisted effective-candidate store ──
        # Candidate presence is necessary but NOT sufficient: an ENTRY_ELIGIBLE instrument with
        # an entry signal is offered to contention ONLY if it has an effective candidate (keyed
        # by its verified instrument_uid). Raw source output cannot bypass this store. An
        # instrument blocked by the candidate gate is recorded with its fail-closed reason.
        if effective_selection is not None:
            gated, blocked_by_candidate = [], []
            eff, blk = effective_selection.effective, effective_selection.blocked
            for o in candidates:
                iuid = o.get("instrument_uid")
                if iuid and iuid in eff:
                    gated.append(o)
                else:
                    reason = blk.get(iuid, Reason.CANDIDATE_INACTIVE)
                    blocked_by_candidate.append({**o, "rejected_reason": reason})
            candidates = gated
        else:
            blocked_by_candidate = []
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
            # ── R2C FX-normalized sizing + inherited/open-book heat gate (default-off) ──
            # Sizing+heat are checked BEFORE slot/sector contention (task §5 ordering). When the
            # gate is off this branch is never entered → bit-identical selection, zero provider
            # calls. The inherited-book heat snapshot is authoritative for EXISTING positions /
            # open orders; within-run accumulation across newly-selected candidates is still
            # handled by the legacy float accumulator below (documented limitation).
            if self.enforce_portfolio_heat:
                rd = self._r2c_entry_gate(c, atr, price)
                if rd is not None and not rd.ok:
                    rejected.append({**c, "rejected_reason": rd.reason})
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

        return {"selected": selected, "rejected": rejected + blocked_by_candidate}

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

    def _r2c_entry_gate(self, c: dict, atr: float, price: float):
        """R2C FX-normalized sizing + inherited/open-book heat decision for one NEW-entry
        candidate (BLOCKER-S / P3-5). Returns a RiskDecision (ok True/False) or None when the
        gate is disabled. Broker-free: consults ONLY the two injected providers. ``price``/ATR
        are in the LISTING/instrument currency; risk is normalized into the account base
        currency. A failed gate blocks the NEW entry only (recorded reason); it never alters or
        liquidates an existing position."""
        stop_distance = params.INITIAL_STOP_ATR_MULT * float(atr)
        inputs = SizingInputs(
            base_currency=self.base_currency,
            instrument_currency=c.get("instrument_currency"),
            entry_price=price,
            stop_distance=stop_distance,
            equity_base=self.equity,
            risk_per_trade_pct=params.RISK_PER_TRADE,
            max_notional_pct=params.MAX_NOTIONAL_PCT,
            canonical_instrument_id=c.get("canonical_instrument_id"),
            instrument_uid=c.get("instrument_uid"),
            listing_uid=c.get("listing_uid"))
        return evaluate_entry_risk(
            inputs,
            evaluation_time=self.risk_evaluation_time,
            evaluation_date=self._run_trading_date,
            fx_provider=self.base_fx_provider,
            portfolio_provider=self.portfolio_risk_provider,
            max_portfolio_heat_pct=params.MAX_PORTFOLIO_HEAT)

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
