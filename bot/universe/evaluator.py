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

Sizing note: hypothetical qty/risk use the frozen breakout constants
(bot/universe/params, mirroring backtest/breakout_sim). v1 shadow computes risk in a
single-currency approximation (equity and price treated in the same unit); real FX
normalisation is deferred. These are hypothetical figures only — never orders.
"""
import hashlib
import json
import logging
from typing import Callable, Optional

from backtest.breakout_strategy import compute_indicators
from bot.universe import params
from bot.universe.eligibility import structural_eligibility
from bot.universe.models import HypotheticalOrder, Reason, State
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
                 evaluator_version: Optional[str] = None):
        from bot.universe import EVALUATOR_VERSION
        self.registry = registry
        self.bars_provider = bars_provider   # callable(canonical_rec)->dict|None ; NO broker
        self.flags = flags                   # object/dict supporting .get(name)
        self.equity = float(equity)
        self.evaluator_version = evaluator_version or EVALUATOR_VERSION

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
            snap["price"] = float(last["close"])
            snap["ohlc_valid"] = _last_bar_valid(last)
            cols = ["sma50", "sma200", "atr14", "adx14", "high20_excl"]
            snap["indicators_available"] = bool(ind.iloc[-1][cols].notna().all())
            n = min(20, len(ind))
            snap["adv20_usd"] = float((ind["close"] * ind["volume"]).tail(n).mean())
            entry_signal = bool(last["entry_signal"])
            trend_break = bool(last["trend_break"])
            atr = None if last["atr14"] != last["atr14"] else float(last["atr14"])
            price = snap["price"]
            sma50 = None if last["sma50"] != last["sma50"] else float(last["sma50"])
        else:
            snap.update({"bar_count": 0, "price": None, "ohlc_valid": False,
                         "indicators_available": False, "adv20_usd": None})

        elig = structural_eligibility(snap)

        has_open = prior_state in (State.POSITION_OPEN.value, State.EXIT_ONLY.value)
        exited = bool(has_open and trend_break)   # hypothetical trend-break exit
        ctx = {
            "hard_disabled": bool(rec.get("hard_disabled")),
            "admin_active": bool(rec.get("administratively_active", 1)),
            "admin_paused": bool(src.get("admin_paused", False)),
            "has_open_position": has_open,
            "exited_this_session": exited,
        }
        outcome = transition(prior_state, prior_passes, prior_failures,
                             prior_cooldown, elig, ctx)

        feature_snapshot = {
            **{k: snap.get(k) for k in (
                "bar_count", "price", "adv20_usd", "indicators_available",
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
            selected.append({**c, "hypothetical_order": order.__dict__})

        return {"selected": selected, "rejected": rejected}

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
