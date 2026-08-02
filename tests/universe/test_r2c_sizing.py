"""R2C BLOCKER-S — FX-normalized sizing into the account base currency.

Covers to-base FX resolution (same-currency=1, cross-currency, every fail-closed mode, no
default-USD / no default-1.0 fallback) and Decimal position sizing (base-currency risk/notional,
floored quantity, conservative rounding, deterministic Decimal). Broker-free fixtures only.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from bot.universe.models import Reason
from bot.universe.sizing import (
    FxRate, SizingInputs, resolve_to_base_fx, size_position, to_decimal,
)
from tests.universe._fixtures import SpyBaseFxRateProvider

NOW = datetime(2026, 6, 18, 12, 0, 0)


def _fx(rates=None, **kw):
    return SpyBaseFxRateProvider(rates=rates, **kw)


# ── FX normalization ───────────────────────────────────────────────────
def test_same_currency_fx_is_one_no_provider_call():
    fx = _fx()
    r = resolve_to_base_fx(instrument_currency="USD", base_currency="USD",
                           evaluation_time=NOW, fx_provider=fx)
    assert r.ok and r.rate == Decimal(1) and r.source == "same_currency"
    assert fx.calls == []                              # same-currency never consults the provider


def test_cross_currency_conversion():
    fx = _fx({("GBP", "USD"): "1.25"})
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                           evaluation_time=NOW, fx_provider=fx)
    assert r.ok and r.rate == Decimal("1.25") and r.fx_pair == "GBPUSD"
    assert fx.calls == [("GBP", "USD", NOW.isoformat())]


def test_missing_fx_blocks():
    fx = _fx({})                                       # provider returns None for any pair
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                           evaluation_time=NOW, fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_RATE_MISSING


def test_stale_fx_blocks():
    stale = NOW - timedelta(seconds=301)               # > MAX_FX_RATE_AGE_SECONDS (300)
    fx = _fx({("GBP", "USD"): "1.25"}, as_of=stale)
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                           evaluation_time=NOW, fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_RATE_STALE


def test_invalid_fx_non_positive_blocks():
    for bad in ("0", "-1.25"):
        fx = _fx({("GBP", "USD"): "1.25"}, bad_rate=bad)
        r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                               evaluation_time=NOW, fx_provider=fx)
        assert not r.ok and r.reason == Reason.FX_RATE_INVALID


def test_future_fx_rate_is_invalid():
    fx = _fx({("GBP", "USD"): "1.25"}, as_of=NOW + timedelta(seconds=60))
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                           evaluation_time=NOW, fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_RATE_INVALID


def test_currency_mismatch_blocks():
    fx = _fx({("GBP", "USD"): "1.25"}, mismatch=True)  # returns a ZZZ/USD rate for a GBP/USD ask
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                           evaluation_time=NOW, fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_CURRENCY_MISMATCH


def test_provider_exception_blocks():
    fx = _fx(raises=RuntimeError("boom"))
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                           evaluation_time=NOW, fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_PROVIDER_UNAVAILABLE


def test_no_default_usd_assumption():
    # base currency unknown → fail closed, never silently assume USD.
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency=None,
                           evaluation_time=NOW, fx_provider=_fx())
    assert not r.ok and r.reason == Reason.SIZING_CURRENCY_UNRESOLVED


def test_no_default_one_fallback_for_cross_currency():
    # cross-currency with NO provider must NOT fall back to 1.0 — it fails closed.
    r = resolve_to_base_fx(instrument_currency="GBP", base_currency="USD",
                           evaluation_time=NOW, fx_provider=None)
    assert not r.ok and r.reason == Reason.FX_PROVIDER_UNAVAILABLE
    assert r.rate is None                              # never a substituted multiplier


# ── sizing ──────────────────────────────────────────────────────────────
def _inputs(**kw):
    base = dict(base_currency="USD", instrument_currency="GBP", entry_price="100",
                stop_distance="2", equity_base="100000")
    base.update(kw)
    return SizingInputs(**base)


def test_risk_and_notional_calculated_in_base_currency():
    fx = _fx({("GBP", "USD"): "1.25"})
    r = size_position(_inputs(), evaluation_time=NOW, fx_provider=fx)
    # entry 100 GBP → 125 USD; stop 2 GBP → 2.5 USD; risk budget 500 USD → qty_risk 200;
    # notional cap 20000 USD / 125 → qty_notional 160; qty 160.
    assert r.ok and r.qty == 160
    assert r.risk_base == Decimal("400") and r.notional_base == Decimal("20000")
    assert r.base_currency == "USD" and r.fx_rate == Decimal("1.25")


def test_quantity_positive_and_valid_same_currency():
    r = size_position(_inputs(instrument_currency="USD", entry_price="100", stop_distance="2"),
                      evaluation_time=NOW, fx_provider=_fx())
    assert r.ok and r.qty == 200 and r.qty > 0


def test_missing_price_blocks():
    for bad in (None, "0", "-5"):
        r = size_position(_inputs(entry_price=bad), evaluation_time=NOW, fx_provider=_fx())
        assert not r.ok and r.reason == Reason.SIZING_PRICE_MISSING


def test_missing_stop_blocks():
    r = size_position(_inputs(stop_distance=None, stop_price=None),
                      evaluation_time=NOW, fx_provider=_fx())
    assert not r.ok and r.reason == Reason.SIZING_STOP_MISSING
    # non-positive stop distance is equally unusable.
    r2 = size_position(_inputs(stop_distance="0"), evaluation_time=NOW, fx_provider=_fx())
    assert not r2.ok and r2.reason == Reason.SIZING_STOP_MISSING


def test_stop_derived_from_stop_price():
    r = size_position(_inputs(instrument_currency="USD", entry_price="100",
                              stop_distance=None, stop_price="98"),
                      evaluation_time=NOW, fx_provider=_fx())
    assert r.ok and r.stop_distance == Decimal("2") and r.qty == 200


def test_risk_above_limit_blocks():
    # stop distance dwarfs the per-trade risk budget → cannot size even 1 share by risk.
    r = size_position(_inputs(instrument_currency="USD", entry_price="10", stop_distance="600"),
                      evaluation_time=NOW, fx_provider=_fx())
    assert not r.ok and r.reason == Reason.SIZING_RISK_EXCEEDS_LIMIT


def test_notional_above_limit_blocks():
    # one share's notional exceeds the per-instrument notional cap → blocked.
    r = size_position(_inputs(instrument_currency="USD", entry_price="30000", stop_distance="1"),
                      evaluation_time=NOW, fx_provider=_fx())
    assert not r.ok and r.reason == Reason.SIZING_NOTIONAL_EXCEEDS_LIMIT


def test_rounding_is_conservative_floor():
    # 100000 * 0.005 / 3 = 166.66… → floored to 166 (never rounded up to 167).
    r = size_position(_inputs(instrument_currency="USD", entry_price="50", stop_distance="3"),
                      evaluation_time=NOW, fx_provider=_fx())
    assert r.ok and r.qty == 166
    assert r.risk_base == Decimal("498")               # 166 * 3, within the 500 budget


def test_decimal_behaviour_is_deterministic():
    # float inputs are routed through str → no binary drift; results are Decimal.
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(float("nan")) is None and to_decimal(float("inf")) is None
    r = size_position(_inputs(instrument_currency="USD", entry_price=100.0, stop_distance=2.0),
                      evaluation_time=NOW, fx_provider=_fx())
    assert isinstance(r.risk_base, Decimal) and isinstance(r.notional_base, Decimal)
    # repeated runs are byte-identical.
    r2 = size_position(_inputs(instrument_currency="USD", entry_price=100.0, stop_distance=2.0),
                       evaluation_time=NOW, fx_provider=_fx())
    assert (r.qty, r.risk_base, r.notional_base) == (r2.qty, r2.risk_base, r2.notional_base)


def test_cross_currency_missing_provider_blocks_sizing():
    # no base FX provider for a cross-currency instrument → fail closed (never 1.0).
    r = size_position(_inputs(), evaluation_time=NOW, fx_provider=None)
    assert not r.ok and r.reason == Reason.FX_PROVIDER_UNAVAILABLE


def test_base_currency_unresolved_blocks_sizing():
    r = size_position(_inputs(base_currency=None), evaluation_time=NOW, fx_provider=_fx())
    assert not r.ok and r.reason == Reason.SIZING_CURRENCY_UNRESOLVED
