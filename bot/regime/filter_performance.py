"""
Aggregation for the regime-filter experiment dashboard tile.

Pure functions: rows in, dict out. No DB access here — the api_server
endpoint reads the rows, then calls this module to compute the
performance summary. Keeps the math testable in isolation.

P&L is reported as raw return percentage, `(exit - entry) / entry * 100`,
which is currency-neutral. The metric assumes LONG positions (the
bot's predominant direction); a few rare shorts would invert the sign
of their per-trade pnl_pct, but the bias on the aggregate is small
given the LONG-heavy population. Documented limitation, not corrected.
"""
import statistics
from typing import Optional


def _safe_pct_return(entry, exit_) -> Optional[float]:
    if entry is None or exit_ is None:
        return None
    try:
        entry = float(entry)
        exit_ = float(exit_)
    except (TypeError, ValueError):
        return None
    if entry == 0:
        return None
    return (exit_ - entry) / entry * 100


def _summarise(returns: list[float]) -> dict:
    if not returns:
        return {
            "n": 0,
            "mean_pnl_pct": None,
            "median_pnl_pct": None,
            "n_wins": 0,
            "n_losses": 0,
            "win_rate": None,
        }
    wins = sum(1 for r in returns if r > 0)
    losses = sum(1 for r in returns if r < 0)
    return {
        "n": len(returns),
        "mean_pnl_pct": statistics.mean(returns),
        "median_pnl_pct": statistics.median(returns),
        "n_wins": wins,
        "n_losses": losses,
        "win_rate": wins / len(returns),
    }


def aggregate(live_trades: list[dict],
              blocked_entries: list[dict],
              shadow_trades: list[dict]) -> dict:
    """Build the regime-filter performance summary.

    live_trades rows need: entry_price, exit_price.
    blocked_entries rows need: smoothed_regime, shadow_trade_id.
    shadow_trades rows need: id, entry_price, exit_price, pnl, pnl_pct,
        status. (Open shadows are counted but contribute no P&L until
        they close.)
    """
    live_returns = [
        r for r in (_safe_pct_return(t.get("entry_price"),
                                     t.get("exit_price"))
                    for t in live_trades) if r is not None
    ]
    live_summary = _summarise(live_returns)

    shadow_by_id = {s["id"]: s for s in shadow_trades}

    by_regime: dict[str, list[float]] = {}
    open_shadow_ids: set = set()
    n_blocked_total = 0
    n_blocked_no_shadow = 0
    n_blocked_open_shadow = 0
    n_blocked_closed_shadow = 0
    all_blocked_returns: list[float] = []

    for entry in blocked_entries:
        n_blocked_total += 1
        regime = entry.get("smoothed_regime") or "unknown"
        bucket = by_regime.setdefault(regime, [])
        sid = entry.get("shadow_trade_id")
        if not sid:
            n_blocked_no_shadow += 1
            continue
        shadow = shadow_by_id.get(sid)
        if shadow is None:
            n_blocked_no_shadow += 1
            continue
        if shadow.get("status") != "CLOSED":
            n_blocked_open_shadow += 1
            open_shadow_ids.add(sid)
            continue
        n_blocked_closed_shadow += 1
        # Prefer the simulator's stored pnl_pct (already sign-aware for
        # SHORT shadows); fall back to (exit-entry)/entry if missing.
        pct = shadow.get("pnl_pct")
        if pct is None:
            pct = _safe_pct_return(shadow.get("entry_price"),
                                   shadow.get("exit_price"))
        if pct is None:
            continue
        bucket.append(float(pct))
        all_blocked_returns.append(float(pct))

    by_regime_summary = {
        regime: _summarise(returns)
        for regime, returns in by_regime.items()
    }
    # Ensure stable keys even when a regime has no rows yet.
    for regime in ("RANGING", "UNCLEAR", "unknown"):
        by_regime_summary.setdefault(regime, _summarise([]))

    blocked_summary = {
        "n_total": n_blocked_total,
        "n_with_no_shadow": n_blocked_no_shadow,
        "n_open_shadows": n_blocked_open_shadow,
        "n_closed_shadows": n_blocked_closed_shadow,
        "by_regime": by_regime_summary,
    }

    comparison = _build_comparison(live_returns, all_blocked_returns)

    return {
        "live_trades": live_summary,
        "blocked_entries": blocked_summary,
        "comparison": comparison,
    }


def _build_comparison(live_returns: list[float],
                      blocked_returns: list[float]) -> dict:
    """Decide what story the data tells about the filter's effect."""
    have_live = bool(live_returns)
    have_blocked = bool(blocked_returns)

    if not have_live and not have_blocked:
        return {
            "mean_live_pnl_pct": None,
            "mean_blocked_shadow_pnl_pct": None,
            "net_effect_pct_per_trade": None,
            "verdict": "insufficient data — no live trades and no closed shadows",
        }
    if not have_blocked:
        return {
            "mean_live_pnl_pct": statistics.mean(live_returns),
            "mean_blocked_shadow_pnl_pct": None,
            "net_effect_pct_per_trade": None,
            "verdict": "no closed shadow trades yet — flip the flag and wait",
        }
    if not have_live:
        return {
            "mean_live_pnl_pct": None,
            "mean_blocked_shadow_pnl_pct": statistics.mean(blocked_returns),
            "net_effect_pct_per_trade": None,
            "verdict": "no live trades in window — comparison not possible",
        }

    mean_live = statistics.mean(live_returns)
    mean_blocked = statistics.mean(blocked_returns)
    net = mean_live - mean_blocked

    if abs(net) < 0.1:
        verdict = (f"roughly neutral: live mean {mean_live:+.2f}% vs "
                   f"would-have-blocked mean {mean_blocked:+.2f}%")
    elif net > 0:
        verdict = (f"filter HELPED: live mean {mean_live:+.2f}% > "
                   f"would-have-blocked mean {mean_blocked:+.2f}% "
                   f"(net +{net:.2f}% per trade)")
    else:
        verdict = (f"filter HURT: live mean {mean_live:+.2f}% < "
                   f"would-have-blocked mean {mean_blocked:+.2f}% "
                   f"(net {net:.2f}% per trade)")

    return {
        "mean_live_pnl_pct": mean_live,
        "mean_blocked_shadow_pnl_pct": mean_blocked,
        "net_effect_pct_per_trade": net,
        "verdict": verdict,
    }
