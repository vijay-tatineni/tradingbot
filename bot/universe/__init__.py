"""Dynamic Universe v1 — IBKR-only shadow foundation (additive, feature-flagged).

This package is **additive research/operational infrastructure**. It is NOT wired
into main.py or any live trading path: importing it has no runtime effect, and the
daily shadow evaluator only does anything when the feature flag
``enable_dynamic_universe_shadow`` is explicitly set true (default: false) in a
NON-production test/shadow environment.

It never:
  * submits, amends, or cancels any order;
  * calls a broker (IBKR or IG) or a data provider (EODHD);
  * reads or manages live positions.db / regime.db / backtest.db;
  * rewrites instruments.json or instruments_ig.json.

All execution state lives in its own research DB (``universe.db``). v1 routing is
IBKR-only; every IG mapping is persisted UNVERIFIED with order_routing_blocked=true.
See docs/dynamic_universe_shadow_implementation.md.
"""

EVALUATOR_VERSION = "dyn_universe_shadow_v1"
