"""Deterministic, idempotent registry seed from existing config (read-only).

Reads instruments.json and instruments_ig.json WITHOUT modifying them, and populates
universe.db's canonical_instruments + gateway maps. Safe to rerun (pure upserts).

Safety invariants enforced here:
  * IBKR is the primary gateway for v1 (all seeded instruments).
  * IG mappings are persisted as metadata only: verification_status='UNVERIFIED',
    order_routing_blocked=1 — never converted to an order-capable mapping.
  * hard_disabled state is preserved exactly; XAUUSD/XAGUSD are never enabled and
    are never added as candidates.
  * No EPIC is guessed or validated; no broker/data call is made.
"""
import json
import logging
from pathlib import Path

from bot.universe.db import REPO_ROOT, connect
from bot.universe.registry import Registry

logger = logging.getLogger("universe.seed")

# The ten operator-approved overlaps that are pinned to IBKR primary in v1.
APPROVED_IBKR_OVERLAPS = frozenset(
    {"SGLN", "SSLN", "AVGO", "SU", "ANTO", "PLTR", "NBIS", "AAPL", "MSFT", "BARC"}
)

_TZ_BY_CCY = {"GBP": "Europe/London", "EUR": "Europe/Paris", "USD": "America/New_York"}


def _region(currency: str, exchange: str) -> str:
    if currency == "GBP":
        return "LSE"
    if currency == "EUR":
        return "EU"
    return "US"


def canonical_id(symbol: str, currency: str, exchange: str) -> str:
    """Deterministic broker-neutral id, e.g. US_AAPL, LSE_BARC, EU_SU."""
    return f"{_region(currency, exchange)}_{symbol}"


def _read_json(path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def seed_registry(db_path: str,
                  instruments_path=None,
                  instruments_ig_path=None) -> dict:
    instruments_path = instruments_path or (REPO_ROOT / "instruments.json")
    instruments_ig_path = instruments_ig_path or (REPO_ROOT / "instruments_ig.json")

    reg = Registry(db_path)

    ibkr = _read_json(instruments_path)
    for inst in ibkr.get("layer1_active", []):
        symbol = inst.get("symbol")
        if not symbol:
            continue
        currency = inst.get("currency", "USD")
        exchange = inst.get("exchange", "SMART")
        cid = canonical_id(symbol, currency, exchange)
        hard = bool(inst.get("hard_disabled", False))
        enabled = bool(inst.get("enabled", True))

        reg.upsert_canonical({
            "canonical_instrument_id": cid,
            "display_symbol": symbol,
            "name": inst.get("name"),
            "asset_class": "EQUITY",          # config carries no asset-class; default
            "sector": None,                   # absent in config (0/29) → NULL/unknown
            "industry": None,
            "exchange": exchange,
            "currency": currency,
            "timezone": _TZ_BY_CCY.get(currency),
            "research_symbol": symbol,
            # administratively_active mirrors operator 'enabled'; hard_disabled is separate.
            "administratively_active": 1 if (enabled and not hard) else 0,
            "hard_disabled": 1 if hard else 0,
            "disabled_reason": inst.get("disabled_reason"),
            "primary_gateway": "IBKR",
        })

        # IBKR mapping: derived from config (NOT qualifyContracts-verified; no conId in file).
        reg.upsert_gateway_ibkr({
            "canonical_instrument_id": cid,
            "conId": None,
            "symbol": symbol,
            "secType": inst.get("sec_type", "STK"),
            "exchange": exchange,
            "primaryExchange": inst.get("primaryExchange"),
            "currency": currency,
            "verification_status": "CONFIG_DERIVED",
        })

    # IG mappings — metadata only, always UNVERIFIED + order_routing_blocked.
    ig = _read_json(instruments_ig_path)
    for inst in ig.get("layer1_active", []):
        symbol = inst.get("symbol")
        epic = inst.get("ig_epic")
        if not symbol:
            continue
        currency = inst.get("currency", "USD")
        exchange = inst.get("exchange", "SMART")
        cid = canonical_id(symbol, currency, exchange)
        if reg.get_canonical(cid) is None:
            # IG-only symbol (none today): create a minimal canonical, still IBKR-primary
            # per v1 policy, so the IG mapping has a home — but it stays order-blocked.
            reg.upsert_canonical({
                "canonical_instrument_id": cid, "display_symbol": symbol,
                "name": inst.get("name"), "asset_class": "EQUITY",
                "exchange": exchange, "currency": currency,
                "timezone": _TZ_BY_CCY.get(currency), "research_symbol": symbol,
                "administratively_active": 0, "hard_disabled": 0,
                "primary_gateway": "IBKR",
            })
        reg.upsert_gateway_ig({
            "canonical_instrument_id": cid,
            "epic": epic,
            "instrument_type": None,        # not verified (no IG reference call)
            "currency": currency,
            "verification_status": "UNVERIFIED",
            # order_routing_blocked is forced to 1 inside the registry.
        })

    # Summary computed from final DB state → idempotent regardless of pre-existing rows.
    canon = reg.all_canonical()
    with connect(db_path) as conn:
        ibkr_n = conn.execute("SELECT COUNT(*) FROM gateway_map_ibkr").fetchone()[0]
        ig_n = conn.execute("SELECT COUNT(*) FROM gateway_map_ig").fetchone()[0]
    summary = {
        "canonical": len(canon),
        "ibkr_maps": int(ibkr_n),
        "ig_maps": int(ig_n),
        "hard_disabled": sum(1 for c in canon if c["hard_disabled"]),
        "ibkr_primary_overlaps": sum(
            1 for c in canon
            if c["display_symbol"] in APPROVED_IBKR_OVERLAPS and c["primary_gateway"] == "IBKR"),
    }
    logger.info("universe.db registry seeded: %s", summary)
    return summary
