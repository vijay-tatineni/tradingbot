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
    ELIGIBILITY_MODE_SHADOW, HypotheticalOrder, PositionStatus, Reason, State,
)
from bot.universe.registry import Registry
from bot.universe.state_machine import transition

logger = logging.getLogger("universe.evaluator")

FLAG = "enable_dynamic_universe_shadow"


def _snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, default=str).encode()
    ).hexdigest()


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
        cid = rec["canonical_instrument_id"]
        td_date = (date.fromisoformat(trading_date)
                   if isinstance(trading_date, str) else trading_date)
        prior = self.registry.get_state(cid)
        prior_state = prior["current_state"] if prior else None
        prior_passes = prior["consecutive_passes"] if prior else 0
        prior_failures = prior["consecutive_failures"] if prior else 0
        prior_cooldown = int(prior["cooldown_until"]) if (prior and prior.get("cooldown_until")) else 0

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

        pos = self._position_ctx(cid, trading_date, prior_state, trend_break)
        ctx = {
            "hard_disabled": bool(rec.get("hard_disabled")),
            "admin_active": bool(rec.get("administratively_active", 1)),
            "admin_paused": bool(src.get("admin_paused", False)),
            **pos,
        }
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
        }
        fhash = _snapshot_hash(feature_snapshot)

        # persist (idempotent history)
        self.registry.upsert_state({
            "canonical_instrument_id": cid,
            "current_state": outcome.new_state.value,
            "previous_state": prior_state,
            "reason_codes": outcome.reason_codes,
            "consecutive_passes": outcome.consecutive_passes,
            "consecutive_failures": outcome.consecutive_failures,
            "cooldown_until": str(outcome.cooldown_remaining),
            "evaluated_trading_date": trading_date,
            "feature_snapshot_hash": fhash,
            "evaluator_version": self.evaluator_version,
        })
        self.registry.append_history({
            "canonical_instrument_id": cid, "trading_date": trading_date,
            "prior_state": prior_state, "new_state": outcome.new_state.value,
            "reason_codes": outcome.reason_codes,
            "feature_snapshot": feature_snapshot, "feature_snapshot_hash": fhash,
            "evaluator_version": self.evaluator_version,
        })

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

    # ── broker-free position context (task §3) ────────────────────────
    def _position_ctx(self, cid: str, trading_date, prior_state, trend_break) -> dict:
        """Resolve operational position context for the state transition.

        With an injected PositionSnapshotProvider the status is authoritative and the
        POSITION_OPEN / EXIT_ONLY / COOLDOWN lifecycle is driven organically. Without
        a provider the legacy derivation is used (prior open-class state + hypothetical
        trend-break exit). UNKNOWN is fail-safe: possibly-open (no flat assumption),
        never entry-eligible, never liquidated.
        """
        if self.position_provider is not None:
            status = self._query_position(cid, trading_date)
            if status == PositionStatus.UNKNOWN:
                return {"has_open_position": True, "exited_this_session": False,
                        "position_unknown": True}
            has_open = status in (PositionStatus.POSITION_OPEN,
                                  PositionStatus.POSITION_EXITED_TODAY)
            exited = status == PositionStatus.POSITION_EXITED_TODAY
            return {"has_open_position": has_open, "exited_this_session": exited,
                    "position_unknown": False}
        has_open = prior_state in (State.POSITION_OPEN.value, State.EXIT_ONLY.value)
        exited = bool(has_open and trend_break)   # hypothetical trend-break exit
        return {"has_open_position": has_open, "exited_this_session": exited,
                "position_unknown": False}

    def _query_position(self, cid: str, trading_date) -> PositionStatus:
        """Query the injected provider, coercing any failure/garbage to UNKNOWN (fail
        safe). The provider is broker-free by contract; this call makes no broker access."""
        try:
            td = (date.fromisoformat(trading_date)
                  if isinstance(trading_date, str) else trading_date)
            status = self.position_provider.get_position_status(cid, td)
        except Exception:
            logger.warning("position provider error for %s on %s; treating as UNKNOWN",
                           cid, trading_date)
            return PositionStatus.UNKNOWN
        if isinstance(status, PositionStatus):
            return status
        try:
            return PositionStatus(status)
        except (ValueError, TypeError):
            return PositionStatus.UNKNOWN

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
