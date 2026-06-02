"""
tests/test_no_edge_guardrail.py

Tests for the no-edge guardrail validator (bot/guardrails.py).

An ENABLED layer1 instrument whose notes imply no/marginal edge must carry
an explicit override (experiment:true OR allow_new_entries:false), else the
validator flags it. Disabled or clean-notes instruments are ignored.
"""

import json
from pathlib import Path

from bot.guardrails import validate_no_edge_guardrails

BASE_DIR = Path(__file__).parent.parent


def _cfg(*instruments):
    return {"layer1_active": list(instruments)}


def _inst(symbol="X", notes="", enabled=True, **extra):
    d = {"symbol": symbol, "name": symbol, "enabled": enabled, "notes": notes}
    d.update(extra)
    return d


# ── failure cases ────────────────────────────────────────────────────

def test_enabled_no_edge_no_override_fails():
    errs = validate_no_edge_guardrails(
        _cfg(_inst("FOO", notes="2yr backtest: ~$0 — no edge.")))
    assert len(errs) == 1
    assert "FOO" in errs[0]


def test_enabled_marginal_no_override_fails():
    errs = validate_no_edge_guardrails(
        _cfg(_inst("BAR", notes="marginal 4hr, low trades")))
    assert len(errs) == 1
    assert "BAR" in errs[0]


# ── pass via override ────────────────────────────────────────────────

def test_enabled_no_edge_with_exits_only_passes():
    errs = validate_no_edge_guardrails(
        _cfg(_inst("ANETish", notes="no edge", allow_new_entries=False)))
    assert errs == []


def test_enabled_marginal_with_experiment_passes():
    errs = validate_no_edge_guardrails(
        _cfg(_inst("NBISish", notes="marginal", experiment=True)))
    assert errs == []


# ── pass because not applicable ──────────────────────────────────────

def test_disabled_no_edge_passes():
    errs = validate_no_edge_guardrails(
        _cfg(_inst("OFF", notes="no edge", enabled=False)))
    assert errs == []


def test_enabled_clean_notes_passes():
    errs = validate_no_edge_guardrails(
        _cfg(_inst("GOOD", notes="+$36,340 PF 2.85 (BEST OVERALL)")))
    assert errs == []


def test_empty_notes_passes():
    """Clean/empty notes never trigger — this is NOT a general additions gate."""
    errs = validate_no_edge_guardrails(_cfg(_inst("NVTSish", notes="")))
    assert errs == []
    errs2 = validate_no_edge_guardrails(_cfg({"symbol": "NONOTES", "enabled": True}))
    assert errs2 == []


# ── edge cases ───────────────────────────────────────────────────────

def test_experiment_must_be_exactly_true():
    """experiment:'true' (string) or truthy non-True does not satisfy override."""
    errs = validate_no_edge_guardrails(
        _cfg(_inst("STR", notes="no edge", experiment="true")))
    assert len(errs) == 1


def test_allow_new_entries_must_be_exactly_false():
    """allow_new_entries:0 or None does not satisfy the exits-only override."""
    errs = validate_no_edge_guardrails(
        _cfg(_inst("ZERO", notes="no edge", allow_new_entries=0)))
    assert len(errs) == 1


def test_multiple_offenders_all_reported():
    errs = validate_no_edge_guardrails(_cfg(
        _inst("A", notes="no edge"),
        _inst("B", notes="marginal"),
        _inst("C", notes="clean +$500 PF 2.0"),
    ))
    syms = " ".join(errs)
    assert "A" in syms and "B" in syms and "C" not in syms
    assert len(errs) == 2


def test_default_enabled_true_when_key_absent():
    """Missing 'enabled' defaults to True, so the guardrail still applies."""
    errs = validate_no_edge_guardrails(
        _cfg({"symbol": "DEF", "notes": "no edge"}))
    assert len(errs) == 1


# ── the live config must pass (regression guard) ─────────────────────

def test_live_instruments_json_passes():
    """The committed live config must be clean — ANET (allow_new_entries:false)
    and NBIS (experiment:true) carry overrides; everything else has edge."""
    with open(BASE_DIR / "instruments.json") as f:
        cfg = json.load(f)
    errs = validate_no_edge_guardrails(cfg)
    assert errs == [], f"Live config violates no-edge guardrail: {errs}"
