"""
Trading Bot v7.2 — main.py

Main entry point and orchestrator. Owns the main loop, plugin registry,
watchdog, weekend sleep, and graceful shutdown handling.

All trading logic lives in bot/layer1.py (active), bot/layer2.py (accum),
and bot/layer3_silver.py (scalper). Config loaded from instruments.json.

To add a new plugin:
  1. Create bot/plugins/my_plugin.py (extend BasePlugin)
  2. Import it below
  3. Call bot.register_plugin(MyPlugin(cfg))

Plugins:
  LearningLoop    — records every trade to SQLite, time-based weekly retrain
  TelegramAlerts  — trade alerts, daily summary (deduped), error notifications
  SentimentEngine — Claude API news sentiment gate (blocks bad-news BUYs)

Run:   python3 main.py
Stop:  Ctrl+C (positions remain open on IBKR)
"""

import sys
import os
import json
import signal
import sqlite3
import datetime
import argparse
import time
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from bot.config        import Config
from bot.guardrails     import validate_no_edge_guardrails, validate_hard_disabled_instruments
from bot.brokers       import create_broker
from bot.market_hours  import MarketHours
from bot.layer1        import ActiveTrading
from bot.layer2        import Accumulation
from bot.layer3_silver import SilverScalper
from bot.dashboard     import Dashboard
from bot.logger        import log, banner, separator
from bot.regime.flags  import FeatureFlags
from bot.regime.orchestrator import RegimeOrchestrator
from bot.regime.log_setup    import setup_regime_logging
from bot.regime.cache        import RegimeCache
from bot.regime.classifier   import RegimeClassifier
from bot.regime.cost_tracker import CostTracker
from bot.regime.scheduler    import RegimeClassificationScheduler
from bot.regime.smoothing_store import SmoothedStateStore
from bot.regime.blocked_entries import RegimeBlockedEntriesLog
from bot.degradation.instrument_pause_registry import InstrumentPauseRegistry
from bot.overlays.registry import active_overlays as overlay_active_overlays, init_overlay_registry
from bot.regime.router     import route as regime_route
from bot.shadow.counterfactual_logger import CounterfactualLogger
from bot.shadow.trade_simulator import ShadowTradeSimulator
from bot.shadow.position_metadata_store import PositionMetadataStore

BASE_DIR = Path(__file__).parent

# ── Active plugins ────────────────────────────────────────────
from bot.plugins.learning_loop import LearningLoop
from bot.plugins.sentiment     import SentimentEngine
from bot.alerts                import TelegramAlerts
from bot.watchdog              import Watchdog
from bot.llm                   import create_llm

# ── Future plugins (uncomment to activate) ────────────────────
# from bot.plugins.macro_filter import MacroFilter
# from bot.plugins.ml_override  import MLOverride


# ── Dynamic Universe shadow runtime wiring (Gate C) ───────────────────────────
# Default-off, fail-closed wiring that can LATER start the Dynamic Universe shadow
# scheduler — but ONLY when the master flag is explicitly true AND a valid, broker-free
# shadow configuration (a dedicated shadow DB path + injected non-live providers) is
# supplied. This tranche WIRES the path (so it is reviewable) but does not ACTIVATE it.
#
# Inertness contract (verified by tests/universe/test_shadow_runtime_wiring.py):
#   * No top-level ``bot.universe`` import in main.py — the W1/W2 boundary is imported
#     LAZILY inside ``init_shadow_runtime`` and ONLY after the master flag is confirmed true.
#   * Flag absent/false (the production default) → ``init_shadow_runtime`` returns immediately:
#     nothing imported from bot.universe, nothing constructed, no ``sqlite3.connect``, no
#     ``migrate()``, no DB file, no provider construction/call, no scheduler.
#   * Flag true but config/provider invalid → ``build_shadow_scheduler`` (W2) fails closed —
#     it validates first and constructs nothing / touches no filesystem — and a stable,
#     non-secret reason is logged. No DB, no provider call, no scheduler.
#   * No live provider default (policy, design §5): in production NO providers are injected,
#     so the build always fails closed at ``bars_provider_missing`` and the scheduler stays
#     None → the per-cycle seam (``TradingBot._maybe_run_shadow_cycle``) is a guarded no-op.
#   * A constructed scheduler (reachable only with injected non-live stub providers, i.e.
#     tests/rehearsal) is shadow-only: it submits/modifies/cancels no orders, opens/closes no
#     positions, calls no broker, and writes only to its dedicated shadow DB.

SHADOW_RUNTIME_DISABLED         = "shadow_runtime_disabled"
SHADOW_RUNTIME_CONFIG_INVALID   = "shadow_runtime_config_invalid"
SHADOW_RUNTIME_PROVIDER_MISSING = "shadow_runtime_provider_missing"
SHADOW_RUNTIME_DB_PATH_UNSAFE   = "shadow_runtime_db_path_unsafe"
SHADOW_RUNTIME_READY            = "shadow_runtime_ready"
SHADOW_RUNTIME_NOT_STARTED      = "shadow_runtime_not_started"

_SHADOW_MASTER_FLAG = "enable_dynamic_universe_shadow"

# Map the W1/W2 validator's fail-closed reason codes → stable startup-evidence events.
_SHADOW_REASON_EVENT = {
    "flag_off":                        SHADOW_RUNTIME_DISABLED,
    "shadow_db_path_missing":          SHADOW_RUNTIME_CONFIG_INVALID,
    "shadow_db_path_unsafe":           SHADOW_RUNTIME_DB_PATH_UNSAFE,
    "bars_provider_missing":           SHADOW_RUNTIME_PROVIDER_MISSING,
    "completed_bar_provider_missing":  SHADOW_RUNTIME_PROVIDER_MISSING,
    "live_provider_requires_approval": SHADOW_RUNTIME_CONFIG_INVALID,
}


@dataclass(frozen=True)
class ShadowRuntimeStartup:
    """Outcome of the startup wiring decision (no side effects unless flag on AND valid).

    ``ready`` is True only when the master flag is on, the broker-free shadow config fully
    validated, and a shadow scheduler was constructed — reachable only with injected non-live
    providers (tests/rehearsal), never in production. ``scheduler`` is that shadow-only
    scheduler when ready, else None. ``reason`` carries the stable fail-closed code otherwise."""
    ready: bool
    reason: Optional[str] = None
    scheduler: object = None


def _shadow_master_flag_on(flags) -> bool:
    """Read the master flag fail-closed (any error → treated as off)."""
    try:
        return bool(flags.get(_SHADOW_MASTER_FLAG))
    except Exception:
        return False


def _default_shadow_emit(code: str, reason: Optional[str] = None) -> None:
    log(f"[UniverseShadow] {code}" + (f" (reason={reason})" if reason else ""), "INFO")


def init_shadow_runtime(flags, *, db_path=None, bars_provider=None,
                        completed_bar_provider=None,
                        builder=None, emit=None) -> ShadowRuntimeStartup:
    """Decide, fail-closed, whether to construct the Dynamic Universe shadow scheduler.

    Master-flag gate FIRST: when the flag is absent/false this returns immediately having
    imported NOTHING from ``bot.universe`` and constructed NOTHING (no DB, no provider, no
    scheduler) — only a single ``shadow_runtime_disabled`` evidence line is logged. Only when
    the flag is true does it LAZILY import the W1/W2 boundary and call ``build_shadow_scheduler``,
    which validates first and constructs nothing unless the shadow config is fully valid and the
    providers are non-live. Every flag-on-but-blocked outcome logs a stable, non-secret reason
    plus ``shadow_runtime_not_started`` and returns ``ready=False`` with no scheduler. Never
    raises; never starts runtime scheduling (the caller's per-cycle seam does that, and only
    when a scheduler exists)."""
    emit = emit or _default_shadow_emit

    if not _shadow_master_flag_on(flags):
        # Normal production posture: flag off → no import, no construction, no DB, no provider.
        emit(SHADOW_RUNTIME_DISABLED, reason="flag_off")
        return ShadowRuntimeStartup(ready=False, reason="flag_off", scheduler=None)

    def _blocked(reason: str) -> ShadowRuntimeStartup:
        emit(_SHADOW_REASON_EVENT.get(reason, SHADOW_RUNTIME_CONFIG_INVALID), reason=reason)
        emit(SHADOW_RUNTIME_NOT_STARTED, reason=reason)
        return ShadowRuntimeStartup(ready=False, reason=reason, scheduler=None)

    # Flag ON — import the boundary LAZILY (never at module top); fail closed on import error.
    try:
        from bot.universe.shadow_runtime import build_shadow_scheduler
    except Exception:
        return _blocked("boundary_import_failed")

    build = builder or build_shadow_scheduler
    try:
        result = build(flags=flags, db_path=db_path, bars_provider=bars_provider,
                       completed_bar_provider=completed_bar_provider)
    except Exception:
        # A provider/factory/builder exception must fail closed — no start, no DB, no crash.
        return _blocked("build_exception")

    if not getattr(result, "ok", False):
        return _blocked(getattr(result, "reason", None) or "config_invalid")

    # Flag on + fully valid config + non-live providers → a shadow-only scheduler was built.
    emit(SHADOW_RUNTIME_READY)
    return ShadowRuntimeStartup(ready=True, reason=None, scheduler=result.scheduler)


class TradingBot:
    """
    Main orchestrator.
    Owns the main loop, plugin registry, watchdog, and shutdown handling.
    All trading logic lives in layer1.py, layer2.py and their dependencies.
    """

    VERSION = "7.2"

    def __init__(self, broker_override: str = None, config_path: str = None):
        self.cfg     = Config(config_path) if config_path else Config()
        self.broker_type = broker_override or self.cfg._raw.get('settings', {}).get('broker', 'ibkr')
        self.broker  = create_broker(self.broker_type, self.cfg)
        self.hours   = MarketHours()
        self.plugins : list = []

        # ── LLM providers ─────────────────────────────────────
        settings = self.cfg._raw.get('settings', {})
        try:
            self.llm = create_llm(settings.get('llm_provider', 'groq'))
        except Exception:
            self.llm = None
        try:
            self.llm_review = create_llm(
                settings.get('llm_provider_review',
                             settings.get('llm_provider', 'groq')))
        except Exception:
            self.llm_review = None
        try:
            self.llm_advisor = create_llm(
                settings.get('llm_provider_advisor',
                             settings.get('llm_provider', 'groq')))
        except Exception:
            self.llm_advisor = None

        # News collection state
        self._last_news_collection = 0
        self._advisor_ran_this_week = False
        self._daily_summary_sent = False

        # Heartbeat monitoring state
        self._last_successful_cycle_ts = time.time()
        self._last_successful_cycle_num = 0
        self._heartbeat_alert_sent = False
        self._error_count = 0

        # Health summary state
        self._last_health_summary_ts = time.time()

        # Startup summary (sent once after first successful cycle)
        self._startup_summary_sent = False

        # ── Register active plugins ───────────────────────────
        self.alerts = TelegramAlerts(self.cfg)
        self.alerts.broker_label = self.broker_type.upper()

        self.register_plugin(LearningLoop(self.cfg, llm=self.llm_review, alerts=self.alerts))
        self.register_plugin(self.alerts)

        self.register_plugin(SentimentEngine(self.cfg, alerts=self.alerts))

        # ── Uncomment to activate future plugins ──────────────
        # self.register_plugin(MacroFilter(self.cfg))
        # self.register_plugin(MLOverride(self.cfg))

        # ── Regime orchestrator (§14) ─────────────────────────
        flag_config = self.cfg._raw.get('settings', {}).get('feature_flags', {})
        self.flags = FeatureFlags(flag_config)

        # ── Dynamic Universe shadow scheduler wiring (Gate C; default-off, fail-closed) ──
        # Owned by the TradingBot runtime path. ``init_shadow_runtime`` checks the master flag
        # FIRST: in production (flag absent/false) it imports no bot.universe module and
        # constructs nothing, so this line is a pure no-op beyond one startup log. No live
        # providers are injected (policy: no live default), so even with the flag on the build
        # fails closed at provider validation and ``self.shadow_runtime.scheduler`` stays None.
        self.shadow_runtime = init_shadow_runtime(
            self.flags, **self._resolve_shadow_runtime_config(settings))

        regime_db = str(BASE_DIR / 'regime.db')
        init_overlay_registry(regime_db)
        self.pause_registry = InstrumentPauseRegistry(regime_db)
        self.cf_logger = CounterfactualLogger(regime_db)
        self.pm_store = PositionMetadataStore(regime_db)
        self.regime_cache = RegimeCache(regime_db)
        self.regime_cost_tracker = CostTracker(regime_db)
        self.smoothing_store = SmoothedStateStore(regime_db)
        self.regime_classifier = RegimeClassifier(
            cache=self.regime_cache,
            cost_tracker=self.regime_cost_tracker,
        )
        self.regime_blocked_entries_log = RegimeBlockedEntriesLog(regime_db)
        self.shadow_trade_simulator = ShadowTradeSimulator(self.cf_logger)

        self.orchestrator = RegimeOrchestrator(
            flags=self.flags,
            pause_registry=self.pause_registry,
            overlay_registry_fn=overlay_active_overlays,
            router_fn=regime_route,
            smoothing_store=self.smoothing_store,
            counterfactual_logger=self.cf_logger,
            position_metadata_store=self.pm_store,
            telegram_alerts=self.alerts,
            config_path=config_path,
            regime_cache=self.regime_cache,
            blocked_entries_log=self.regime_blocked_entries_log,
            shadow_trade_simulator=self.shadow_trade_simulator,
        )
        self.register_plugin(self.orchestrator)

        self.regime_scheduler = RegimeClassificationScheduler(
            flags=self.flags,
            classifier=self.regime_classifier,
            cache=self.regime_cache,
            smoothing_store=self.smoothing_store,
            bars_fetcher=self._regime_bars_fetcher,
        )

        setup_regime_logging(str(BASE_DIR))

        # ── Wire alerts to broker for order failure notifications ─
        self.broker.set_alerts(self.alerts)

        # ── Pass plugins to layer1 ────────────────────────────
        self.l1   = ActiveTrading(self.cfg, self.broker, self.plugins,
                                  alerts=self.alerts, llm=self.llm)
        self.l2   = Accumulation(self.cfg, self.broker)
        self.l3   = SilverScalper(self.cfg, self.broker, alerts=self.alerts)
        self.dash = Dashboard(self.cfg)

        # ── Watchdog: alert if bot appears stuck ──────────────
        stale_mins = max(self.cfg.check_interval_mins * 3, 10)
        self.watchdog = Watchdog(alerts=self.alerts, max_stale_mins=stale_mins)

        # ── Graceful shutdown on Ctrl+C ───────────────────────
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def register_plugin(self, plugin) -> None:
        """Register a plugin. Plugins run in registration order."""
        self.plugins.append(plugin)
        log(f"Plugin registered: {plugin.name}")

    def _resolve_shadow_runtime_config(self, settings) -> dict:
        """Provider/path injection seam for the Dynamic Universe shadow scheduler.

        Returns the kwargs for ``init_shadow_runtime`` (``db_path`` / ``bars_provider`` /
        ``completed_bar_provider``). By policy (design §5) this supplies NO live default: the
        providers are ``None`` unless a future, separately-approved tranche injects a
        broker-free provider here. ``db_path`` is read from an EXPLICIT config block only
        (``settings.dynamic_universe_shadow.shadow_db_path``) — never the production default —
        so production (no such block) yields ``None`` and the wiring fails closed."""
        shadow_cfg = (settings or {}).get('dynamic_universe_shadow', {}) or {}
        return {
            "db_path": shadow_cfg.get('shadow_db_path'),  # explicit only; absent → None → fail closed
            "bars_provider": None,                        # no live default (injection seam)
            "completed_bar_provider": None,               # no live default (injection seam)
        }

    def _shadow_canonical_records(self) -> list:
        """Canonical-records source for the shadow scheduler. Candidate ingestion is a SEPARATE,
        not-yet-approved tranche; this returns an empty list (a documented placeholder) so the
        wired call site introduces no ingestion and no provider/broker call."""
        fn = getattr(self, '_shadow_records_fn', None)
        return list(fn()) if fn is not None else []

    def _maybe_run_shadow_cycle(self) -> None:
        """Per-cycle seam for the Dynamic Universe shadow scheduler — a guarded no-op unless a
        shadow scheduler was constructed at startup (master flag on + valid broker-free config +
        injected non-live providers). In production no providers are injected, so
        ``self.shadow_runtime.scheduler`` is None and this returns immediately: no provider call,
        no DB open, no broker call. The scheduler, when present, is shadow-only — it submits,
        modifies, or cancels no orders, opens or closes no positions, and calls no broker."""
        rt = getattr(self, 'shadow_runtime', None)
        if rt is None or rt.scheduler is None:
            return
        try:
            rt.scheduler.maybe_run(self._shadow_canonical_records())
        except Exception as e:
            log(f"[UniverseShadow] cycle error: {e}", "WARN")

    def run(self) -> None:
        """Main loop — runs forever until Ctrl+C."""
        banner([
            f"Trading Bot v{self.VERSION}",
            f"Account  : {self.cfg.account}",
            f"Interval : every {self.cfg.check_interval_mins} minutes",
            f"Active   : {len(self.cfg.active_instruments)} instruments",
            f"Plugins  : {', '.join(p.name for p in self.plugins) or 'none'}",
        ])

        # Qualify all contracts on startup
        log("Qualifying Layer 1 contracts...")
        self.cfg.active_instruments = self.broker.qualify_contracts(self.cfg.active_instruments)
        log("Qualifying Layer 2 contracts...")
        self.cfg.accum_instruments  = self.broker.qualify_contracts(self.cfg.accum_instruments)
        log("Qualifying Layer 3 contracts...")
        self.l3.qualify(self.broker)

        # Notify plugins bot has started
        for plugin in self.plugins:
            plugin.on_start()

        # ── Regime-filter warm-up warning ─────────────────────
        # When the filter is live, any instrument without a smoothed
        # regime yet (scheduler hasn't classified it since startup)
        # has its entries BLOCKED until classification lands. Surface
        # the count once at startup so a fully-blocked filter isn't
        # mistaken for a dead bot.
        if self.flags.get("enable_regime_filter_live"):
            instruments = self.cfg.active_instruments
            no_regime = 0
            for inst in instruments:
                symbol = inst.get("symbol")
                if not symbol:
                    continue
                try:
                    if self.smoothing_store.get_latest(symbol) is None:
                        no_regime += 1
                except Exception:
                    # Treat an unreadable smoothing store as "no regime"
                    # for warning purposes — it would block too.
                    no_regime += 1
            if no_regime > 0:
                log(f"REGIME FILTER LIVE: {no_regime} of {len(instruments)} "
                    f"instruments have no smoothed regime — entries will be "
                    f"BLOCKED until classification lands", "WARN")

        # Start watchdog
        self.watchdog.start()

        cycle = 0

        while True:
            # ── Weekend sleep — no markets open ─────────────
            now = datetime.datetime.now(datetime.timezone.utc)
            if now.weekday() >= 5:  # Saturday=5, Sunday=6
                self.watchdog.set_sleep_mode(True)
                log("Weekend — markets closed, sleeping 1 hour")
                try:
                    self.broker.sleep(3600)
                except (ConnectionError, OSError, Exception) as e:
                    log(f"Weekend sleep connection error: {e} — using fallback sleep", "WARN")
                    time.sleep(3600)
                continue

            self.watchdog.set_sleep_mode(False)
            if not self.broker.is_connected():
                log(f"Reconnecting to {self.broker_type.upper()} after weekend sleep...")
                self.broker.reconnect()

            # ── Heartbeat check ──────────────────────────
            elapsed = time.time() - self._last_successful_cycle_ts
            if elapsed >= 300 and not self._heartbeat_alert_sent:
                last_t = datetime.datetime.fromtimestamp(
                    self._last_successful_cycle_ts, tz=datetime.timezone.utc
                ).strftime('%H:%M UTC')
                self.alerts.send(
                    f"\U0001f534 No heartbeat for 5 minutes. "
                    f"Last cycle: #{self._last_successful_cycle_num} at {last_t}"
                )
                self._heartbeat_alert_sent = True

            cycle += 1
            separator(f"CYCLE #{cycle}  ·  "
                      f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            try:
                # Notify plugins cycle starting
                for plugin in self.plugins:
                    plugin.on_cycle_start(cycle)

                # ── Layer 1: Active trading (every cycle) ─────
                self.l1.run()

                # ── Layer 2: Accumulation (every 6 cycles) ────
                if cycle % 6 == 1:
                    self.l2.run()

                # ── Layer 3: Silver Scalper (every cycle, LSE hours) ─
                self.l3.run()

                # ── Daily regime classifier scheduler (idempotent) ──
                try:
                    sched_summary = self.regime_scheduler.maybe_run(
                        self.cfg.active_instruments
                    )
                    if sched_summary["classified"]:
                        log(f"[Scheduler] Classified "
                            f"{len(sched_summary['classified'])} instruments")
                    if sched_summary["errors"]:
                        log(f"[Scheduler] Errors: {sched_summary['errors']}",
                            "WARN")
                except Exception as e:
                    log(f"[Scheduler] Cycle error: {e}", "WARN")

                # ── Dynamic Universe shadow scheduler (Gate C wiring; default-off) ──
                # Guarded no-op unless a shadow scheduler was constructed at startup
                # (flag on + valid broker-free config + injected non-live providers). In
                # production no providers are injected → scheduler is None → this never runs,
                # opens no DB, calls no provider, and touches no broker.
                self._maybe_run_shadow_cycle()

                # ── Dashboard update ──────────────────────────
                self.dash.update(
                    cycle       = cycle,
                    signal_rows = self.l1.signal_rows,
                    accum_rows  = self.l2.accum_rows,
                    total_pnl   = self.l1.total_pnl,
                    lse_open    = self.hours.lse_open(),
                    us_open     = self.hours.us_open(),
                )

                # ── Notify plugins cycle ended ────────────────
                for plugin in self.plugins:
                    plugin.on_cycle_end(cycle, self.l1.signal_rows,
                                        self.l1.total_pnl)

                # ── Startup summary (once, after first cycle) ─
                if not self._startup_summary_sent:
                    self._send_startup_summary(cycle)

                # ── News collection (every N hours) ──────────
                self._maybe_collect_news()

                # ── Weekly advisor (Sunday 20:00 UTC) ────────
                self._maybe_run_advisor(now)

                # ── Daily P&L summary (21:00 UTC) ────────────
                if now.hour == 21 and now.minute < self.cfg.check_interval_mins and not self._daily_summary_sent:
                    self._send_daily_summary()
                    self._daily_summary_sent = True
                if now.hour == 22:
                    self._daily_summary_sent = False

                # ── Watchdog heartbeat ────────────────────────
                self.watchdog.heartbeat(cycle)

                # ── Heartbeat recovery ───────────────────────
                if self._heartbeat_alert_sent:
                    now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M UTC')
                    self.alerts.send(
                        f"\U0001f7e2 Bot recovered. "
                        f"Cycle #{cycle} at {now_str}"
                    )
                    self._heartbeat_alert_sent = False
                self._last_successful_cycle_ts = time.time()
                self._last_successful_cycle_num = cycle

                # ── Health summary (every 4 hours) ───────────
                if time.time() - self._last_health_summary_ts >= 14400:
                    self._send_health_summary(cycle)

                log(f"Cycle #{cycle} complete. "
                    f"Next in {self.cfg.check_interval_mins} minutes.")
                self.broker.sleep(self.cfg.check_interval)

            except Exception as e:
                self._error_count += 1
                log(f"Cycle error: {e}", "ERROR")
                import traceback
                log(traceback.format_exc(), "ERROR")
                self.alerts.send_error(f"Cycle #{cycle} error: {e}")
                self.broker.reconnect()

    def _regime_bars_fetcher(self, inst: dict):
        """Daily-bar fetcher injected into RegimeClassificationScheduler.

        Returns None when the contract isn't qualified yet (pre-startup) or
        when the broker has no data — the scheduler treats that as an error
        for that instrument and moves on.
        """
        contract = inst.get("contract")
        if contract is None:
            return None
        try:
            return self.broker.fetch_bars(contract, days=300, bar_size="1 day")
        except Exception as e:
            log(f"[Scheduler] fetch_bars failed for "
                f"{inst.get('symbol', '?')}: {e}", "WARN")
            return None

    def _get_today_trades(self) -> list:
        """Get today's closed trades from learning_loop.db."""
        try:
            ll_db = str(BASE_DIR / 'learning_loop.db')
            if not os.path.exists(ll_db):
                return []
            conn = sqlite3.connect(ll_db)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT symbol, pnl_usd, outcome FROM trades
                WHERE open = 0 AND outcome IS NOT NULL
                AND date(timestamp) = date('now')
            """)
            trades = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return trades
        except Exception:
            return []

    def _send_daily_summary(self) -> None:
        """Send end-of-day P&L summary via Telegram."""
        try:
            today_trades = self._get_today_trades()
            daily_pnl = sum(t["pnl_usd"] for t in today_trades)
            wins = sum(1 for t in today_trades if t["outcome"] == "WIN")
            losses = sum(1 for t in today_trades if t["outcome"] == "LOSS")
            open_count = len(self.l1.tracker.open)

            msg = (
                f"Daily Summary\n"
                f"Trades today: {len(today_trades)} "
                f"({wins}W / {losses}L)\n"
                f"Daily P&L: ${daily_pnl:+.2f}\n"
                f"Open positions: {open_count}\n"
                f"Portfolio P&L: ${self.l1.total_pnl:+.2f}"
            )

            self.alerts.send(msg)
            log(f"[DailySummary] Sent: {len(today_trades)} trades, "
                f"P&L: ${daily_pnl:+.2f}")
        except Exception as e:
            log(f"[DailySummary] Failed: {e}", "WARN")

    def _send_startup_summary(self, cycle: int) -> None:
        try:
            rows = self.l1.signal_rows
            open_pos = [r for r in rows if r.get('pos', 0) != 0]
            symbols = ', '.join(r['symbol'] for r in open_pos)

            pnl_by_ccy = {}
            for r in open_pos:
                ccy = r.get('currency', 'USD')
                pnl_by_ccy[ccy] = pnl_by_ccy.get(ccy, 0.0) + r.get('unreal_pnl', 0.0)

            ccy_symbols = {'USD': '$', 'EUR': '€', 'GBP': '£'}
            pnl_parts = []
            for ccy in ('USD', 'EUR', 'GBP'):
                if ccy in pnl_by_ccy:
                    sym = ccy_symbols.get(ccy, ccy + ' ')
                    pnl_parts.append(f"{sym}{pnl_by_ccy[ccy]:+.2f}")
            for ccy, val in sorted(pnl_by_ccy.items()):
                if ccy not in ('USD', 'EUR', 'GBP'):
                    pnl_parts.append(f"{ccy} {val:+.2f}")

            lines = [
                f"\U0001f7e2 <b>Bot started successfully</b>",
                f"Cycle #{cycle} complete",
                f"Active instruments: {len(self.cfg.active_instruments)}",
            ]
            if open_pos:
                lines.append(f"Open positions: {len(open_pos)} ({symbols})")
            else:
                lines.append("Open positions: 0")
            if pnl_parts:
                lines.append(f"P&L: {' | '.join(pnl_parts)}")

            self.alerts.send('\n'.join(lines))
            self._startup_summary_sent = True
            log("[Startup] Summary sent via Telegram")
        except Exception as e:
            log(f"[Startup] Summary failed: {e}", "WARN")
            self._startup_summary_sent = True

    def _send_health_summary(self, cycle: int) -> None:
        try:
            open_count = len(self.l1.tracker.open)
            msg = (
                f"\U0001f4ca Health Summary\n"
                f"Cycles: {cycle} | Open: {open_count}\n"
                f"P&L: ${self.l1.total_pnl:+.2f} | Errors: {self._error_count}"
            )
            self.alerts.send(msg)
            self._last_health_summary_ts = time.time()
            log(f"[Health] Summary sent")
        except Exception as e:
            log(f"[Health] Failed: {e}", "WARN")

    def _maybe_collect_news(self) -> None:
        """Collect news headlines every N hours."""
        settings = self.cfg._raw.get('settings', {})
        if not settings.get('llm_news_collection_enabled', False):
            return
        if not self.llm or not self.llm.is_available():
            return

        interval = settings.get('llm_news_interval_hours', 4) * 3600
        if time.time() - self._last_news_collection < interval:
            return

        try:
            from bot.llm.news_collector import (
                collect_news, score_headlines, save_headlines
            )
            from bot.logger import log
            log("[News] Collecting headlines...")
            for inst in self.cfg.active_instruments:
                headlines = collect_news(inst)
                scored = score_headlines(self.llm, inst['symbol'], headlines)
                save_headlines(inst['symbol'], scored)
            self._last_news_collection = time.time()
            log(f"[News] Collection complete for "
                f"{len(self.cfg.active_instruments)} instruments")
        except Exception as e:
            from bot.logger import log
            log(f"[News] Collection failed: {e}", "WARN")

    def _maybe_run_advisor(self, now) -> None:
        """Run weekly advisor on Sunday at 20:00 UTC."""
        settings = self.cfg._raw.get('settings', {})
        if not settings.get('llm_advisor_enabled', False):
            return
        if not self.llm_advisor or not self.llm_advisor.is_available():
            return

        if now.weekday() == 6 and now.hour == 20 and not self._advisor_ran_this_week:
            try:
                from bot.llm.advisor import generate_weekly_report
                from bot.logger import log
                import sqlite3

                # Fetch trades
                ll_db = str(BASE_DIR / 'learning_loop.db')
                trades = []
                if os.path.exists(ll_db):
                    conn = sqlite3.connect(ll_db)
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.row_factory = sqlite3.Row
                    cursor = conn.execute(
                        "SELECT * FROM trades WHERE open=0 "
                        "AND timestamp > datetime('now', '-7 days')"
                    )
                    trades = [dict(r) for r in cursor.fetchall()]
                    conn.close()

                report = generate_weekly_report(
                    self.llm_advisor, trades, [], [],
                    self.cfg.active_instruments
                )

                # Save report
                advisor_db = str(BASE_DIR / 'advisor.db')
                conn = sqlite3.connect(advisor_db)
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS advisor_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        report_json TEXT NOT NULL
                    )
                """)
                conn.execute(
                    "INSERT INTO advisor_reports (timestamp, report_json) "
                    "VALUES (?, ?)",
                    (now.isoformat(), json.dumps(report))
                )
                conn.commit()
                conn.close()

                # Telegram summary
                if self.alerts and report.get('summary'):
                    self.alerts.send(
                        f"<b>Weekly Advisor Report</b>\n{report['summary'][:500]}"
                    )

                self._advisor_ran_this_week = True
                log(f"[Advisor] Weekly report generated")

            except Exception as e:
                from bot.logger import log
                log(f"[Advisor] Failed: {e}", "WARN")

        # Reset flag on Monday
        if now.weekday() == 0:
            self._advisor_ran_this_week = False

    def _shutdown(self, sig=None, frame=None) -> None:
        """Graceful shutdown — notify plugins, watchdog, log final state."""
        separator("SHUTDOWN")
        log("Bot stopped by user.")
        self.watchdog.stop()
        for plugin in self.plugins:
            try:
                plugin.on_shutdown()
            except Exception:
                pass
        log(f"Final P&L: ${self.l1.total_pnl:+.2f}")
        log(f"Positions remain open on {self.broker_type.upper()}. Manage manually.")
        sys.exit(0)


def validate_environment(config_file: str = None) -> None:
    """
    Pre-flight checks before starting the bot.
    Exits with clear message if anything is wrong.
    """
    errors = []

    # 1. instruments.json exists and is valid JSON
    config_path = Path(config_file) if config_file else BASE_DIR / 'instruments.json'
    if not config_path.exists():
        errors.append(f"instruments.json not found at {config_path}")
    else:
        try:
            with open(config_path) as f:
                data = json.load(f)
            if 'settings' not in data or 'layer1_active' not in data:
                errors.append("instruments.json missing 'settings' or 'layer1_active' keys")
            # No-edge guardrail: refuse to start if a known-marginal
            # instrument is enabled without an explicit override.
            errors.extend(validate_no_edge_guardrails(data))
            # Hard-disabled invariant: refuse to start if a broker-ineligible
            # / administratively hard-disabled instrument is enabled.
            errors.extend(validate_hard_disabled_instruments(data))
        except json.JSONDecodeError as e:
            errors.append(f"instruments.json has invalid JSON: {e}")

    # 2. Check env vars (warn, don't fail — bot works without Telegram)
    if not os.environ.get('TELEGRAM_BOT_TOKEN'):
        log("WARNING: TELEGRAM_BOT_TOKEN not set — Telegram alerts disabled", "WARN")
    if not os.environ.get('TELEGRAM_CHAT_ID'):
        log("WARNING: TELEGRAM_CHAT_ID not set — Telegram alerts disabled", "WARN")

    # 3. Web directory is writable
    web_dir = BASE_DIR / 'web'
    if web_dir.exists() and not os.access(web_dir, os.W_OK):
        errors.append(f"Web directory not writable: {web_dir}")

    # 4. Database directory is accessible
    for db_name in ['positions.db', 'learning_loop.db', 'layer3_silver.db']:
        db_path = BASE_DIR / db_name
        if db_path.exists() and not os.access(db_path, os.W_OK):
            errors.append(f"Database not writable: {db_path}")

    # 5. Broker-specific connectivity checks
    try:
        with open(config_path) as f:
            s = json.load(f).get('settings', {})
        broker = s.get('broker', 'ibkr')
        if broker == 'ibkr':
            port = s.get('port', 0)
            if port not in (4001, 4002, 7496, 7497, 4000):
                log(f"WARNING: Unusual IBKR port {port} — "
                    f"expected 4001/4002 (Gateway) or 7496/7497 (TWS)", "WARN")
        elif broker == 'ig':
            for var in ('IG_USERNAME', 'IG_PASSWORD', 'IG_API_KEY'):
                if not os.environ.get(var):
                    log(f"WARNING: {var} not set — IG broker will fail to connect", "WARN")
    except Exception:
        pass

    if errors:
        print("\n=== STARTUP VALIDATION FAILED ===")
        for e in errors:
            print(f"  ERROR: {e}")
        print("\nFix the above issues and try again.")
        sys.exit(1)

    log("Startup validation passed")


# ── Entry point ───────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="CogniflowAI Trading Bot")
    parser.add_argument("--broker", default=None,
                        help="Broker to use: ibkr or ig (overrides instruments.json)")
    parser.add_argument("--config", default=None,
                        help="Path to instruments JSON config (default: instruments.json)")
    args = parser.parse_args()

    validate_environment(config_file=args.config)
    bot = TradingBot(broker_override=args.broker, config_path=args.config)
    bot.run()
