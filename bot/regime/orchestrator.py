"""
Regime orchestrator plugin — §14 of CLAUDE_STRATEGY_SPEC_v3.

Integrates regime classification, entry gates, and shadow pipeline
into the main trading loop via the plugin system. Does NOT modify
layer1.py — uses pre_trade() to gate entries and post_trade() to
record position metadata.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.plugins.base_plugin import BasePlugin
from bot.regime.entry_gate import evaluate_entry_gates, EntryGateResult
from bot.regime.flags import FeatureFlags
from bot.regime.startup import log_startup

logger = logging.getLogger("regime.orchestrator")


class RegimeOrchestrator(BasePlugin):

    name = "RegimeOrchestrator"

    def __init__(self, flags: FeatureFlags,
                 pause_registry=None,
                 overlay_registry_fn=None,
                 router_fn=None,
                 smoothing_store=None,
                 counterfactual_logger=None,
                 position_metadata_store=None,
                 telegram_alerts=None,
                 config_path: Optional[str] = None):
        self._flags = flags
        self._pause_registry = pause_registry
        self._overlay_fn = overlay_registry_fn
        self._router_fn = router_fn
        self._smoothing_store = smoothing_store
        self._cf_logger = counterfactual_logger
        self._pm_store = position_metadata_store
        self._telegram = telegram_alerts
        self._config_path = config_path
        self._last_gate_results: dict[str, EntryGateResult] = {}

    def on_start(self) -> None:
        log_startup(self._flags, config_path=self._config_path,
                    telegram_alerts=self._telegram)

    def _get_overlays(self, instrument: str) -> list:
        if self._overlay_fn is None:
            return []
        try:
            now = datetime.now(timezone.utc)
            return self._overlay_fn(instrument, now, {})
        except Exception as e:
            logger.warning("Overlay check failed for %s: %s", instrument, e)
            return []

    def _get_routing(self, instrument: str, smoothed=None):
        if self._router_fn is None:
            return None
        if smoothed is None and self._smoothing_store is not None:
            try:
                smoothed = self._smoothing_store.get_latest(instrument)
            except Exception as e:
                logger.warning("Smoothing store lookup failed for %s: %s",
                               instrument, e)
                return None
        if smoothed is None:
            # No classification yet (warm-up day, scheduler hasn't run, or
            # flag-off shadow). Preserve existing "allow" behaviour upstream
            # by returning None — gate falls back to router_allows=True.
            return None
        try:
            overlays = self._get_overlays(instrument)
            return self._router_fn(smoothed, overlays, self._flags.as_dict())
        except Exception as e:
            logger.warning("Router failed for %s: %s", instrument, e)
            return None

    def _evaluate_gates(self, instrument: str) -> EntryGateResult:
        result, _ = self._evaluate_gates_with_context(instrument)
        return result

    def _evaluate_gates_with_context(self, instrument: str):
        is_paused = False
        pause_reason = None
        if self._pause_registry:
            is_paused = self._pause_registry.is_paused(instrument)
            if is_paused:
                pause_reason = self._pause_registry.pause_reason(instrument)

        overlays_live = self._flags.get("enable_event_overlays_live")
        active_overlays = self._get_overlays(instrument)
        overlay_names = []
        for o in active_overlays:
            if isinstance(o, dict):
                overlay_names.append(o.get("overlay_name", str(o)))
            else:
                overlay_names.append(getattr(o, "overlay_name", str(o)))

        router_live = self._flags.get("enable_router_live")
        smoothed = None
        if self._smoothing_store is not None:
            try:
                smoothed = self._smoothing_store.get_latest(instrument)
            except Exception as e:
                logger.warning("Smoothing store lookup failed for %s: %s",
                               instrument, e)
        routing = self._get_routing(instrument, smoothed=smoothed)
        router_allows = True
        router_block_reason = None
        if routing is not None:
            router_allows = routing.allow_new_entries
            router_block_reason = routing.block_reason

        result = evaluate_entry_gates(
            is_paused=is_paused,
            pause_reason=pause_reason,
            overlays_live=overlays_live,
            active_overlay_names=overlay_names,
            router_live=router_live,
            router_allows=router_allows,
            router_block_reason=router_block_reason,
        )
        context = {
            "is_paused": is_paused,
            "pause_reason": pause_reason,
            "overlay_names": overlay_names,
            "smoothed": smoothed,
            "routing": routing,
            "router_allows": router_allows,
            "router_block_reason": router_block_reason,
        }
        return result, context

    def pre_trade(self, inst: dict, signal: int, confidence: str) -> bool:
        if signal not in (1, -1):
            return True

        symbol = inst.get("symbol", "UNKNOWN")
        result = self._evaluate_gates(symbol)
        self._last_gate_results[symbol] = result

        if not result.allow:
            logger.info("Entry blocked for %s: gate=%s reason=%s",
                        symbol, result.gate, result.block_reason)
            return False

        return True

    def log_signal(self, inst: dict, signal: int, confidence: str,
                   live_blocked_by: Optional[str]) -> None:
        """Gap #10: log every BUY/SELL engine signal regardless of
        whether layer1 reaches the pre_trade gate. live_blocked_by
        carries which gate (if any) stopped the live trade — None
        means live took it. Best-effort; any failure is swallowed
        so live trading is unaffected.
        """
        if self._cf_logger is None:
            return
        if signal not in (1, -1):
            return

        symbol = inst.get("symbol", "UNKNOWN")
        try:
            _, context = self._evaluate_gates_with_context(symbol)

            shadow_result = evaluate_entry_gates(
                is_paused=context["is_paused"],
                pause_reason=context["pause_reason"],
                overlays_live=True,
                active_overlay_names=context["overlay_names"],
                router_live=True,
                router_allows=context["router_allows"],
                router_block_reason=context["router_block_reason"],
            )

            live_action = "TAKE" if live_blocked_by is None else "BLOCK"
            shadow_action = "TAKE" if shadow_result.allow else "BLOCK"
            disagreement_type = self._classify_disagreement(
                live_action, shadow_action, live_blocked_by
            )

            smoothed = context.get("smoothed")
            routing = context.get("routing")
            signal_payload = {"signal": signal, "confidence": confidence}
            bar_time = datetime.now(timezone.utc).isoformat()

            self._cf_logger.log_decision(
                instrument=symbol,
                bar_time=bar_time,
                live_engine="triple_confirmation",
                live_signal=signal_payload,
                live_action=live_action,
                live_trade_id=None,
                live_blocked_by=live_blocked_by,
                shadow_regime=smoothed.source_regime if smoothed else None,
                shadow_confidence=smoothed.confidence if smoothed else None,
                shadow_smoothed_regime=smoothed.effective_regime if smoothed else None,
                shadow_smoothed_days=smoothed.days_in_regime if smoothed else None,
                shadow_overlays_active=context["overlay_names"] or None,
                shadow_engine=routing.selected_engine if routing else None,
                shadow_signal=signal_payload,
                shadow_action=shadow_action,
                disagreement_type=disagreement_type,
                flag_snapshot=self._flags.as_dict(),
            )
        except Exception as e:
            logger.warning("Shadow signal logging failed for %s: %s",
                           symbol, e)

    @staticmethod
    def _classify_disagreement(live_action: str, shadow_action: str,
                               live_blocked_by: Optional[str]) -> Optional[str]:
        if live_action == shadow_action == "TAKE":
            return None
        if shadow_action == "BLOCK" and live_action == "TAKE":
            return "shadow_blocks_live_takes"
        if shadow_action == "TAKE" and live_action == "BLOCK":
            return "shadow_allows_live_blocks"
        # Both BLOCK from here — refine by live block reason.
        if live_blocked_by == "orchestrator":
            return None
        if live_blocked_by == "position_limit":
            return "shadow_blocked_position_limit_blocked"
        return "shadow_blocked_live_would_take"

    def last_gate_result(self, instrument: str) -> Optional[EntryGateResult]:
        return self._last_gate_results.get(instrument)
