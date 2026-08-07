"""P2-2: USD currency normalisation for eligibility.

Covers every conversion (USD/GBP/GBX/EUR), every fail-closed FX failure mode (missing,
zero, negative, NaN, infinite, stale, future, unknown currency, unknown price unit,
GBP/GBX ambiguity, invalid normalised values), the FX freshness rule, the USD-normalised
price / ADV20 threshold boundaries (below / equal / above), and the rule that a USD
instrument (and the flag-off path) never invokes the FX provider. Deterministic fixtures
only — no broker / EODHD / external FX call."""
from datetime import date, timedelta

import pytest

from bot.universe import params
from bot.universe.eligibility import structural_eligibility
from bot.universe.fx import MAX_FX_STALENESS_DAYS, FxQuote, normalize_to_usd
from bot.universe.models import ELIGIBILITY_MODE_SHADOW, Reason
from bot.universe.evaluator import ShadowEvaluator
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    OFF, ON, SpyProvider, StaticFxRateProvider, inst, make_bars, write_configs,
)

TD = date(2026, 6, 10)


class FixedFx:
    """Returns one configured FxQuote (or None) for any currency/date; records calls."""
    def __init__(self, quote):
        self._q = quote
        self.calls = []

    def get_to_usd_rate(self, currency, trading_date):
        self.calls.append((currency, trading_date))
        return self._q


def _norm(**over):
    kw = dict(price_local=20.0, adv20_local=100_000_000.0, currency="USD",
              price_unit=None, trading_date=TD, fx_provider=None)
    kw.update(over)
    return normalize_to_usd(**kw)


# ── successful conversions ────────────────────────────────────────────────────

def test_usd_rate_is_one_and_no_provider_call():
    spy = FixedFx(FxQuote(2.0, TD))   # would corrupt the result if (wrongly) consulted
    r = _norm(currency="USD", price_local=12.0, adv20_local=25_000_000.0, fx_provider=spy)
    assert r.ok and r.fx_to_usd == 1.0
    assert r.price_usd == 12.0 and r.adv20_usd == 25_000_000.0
    assert spy.calls == []            # USD never touches the FX provider


def test_gbp_major_conversion():
    fx = FixedFx(FxQuote(1.25, TD))
    r = _norm(currency="GBP", price_unit="MAJOR", price_local=8.0,
              adv20_local=16_000_000.0, fx_provider=fx)
    assert r.ok and r.fx_to_usd == 1.25
    assert r.price_usd == 10.0 and r.adv20_usd == 20_000_000.0
    assert r.price_local == 8.0 and r.currency == "GBP" and r.price_unit == "MAJOR"


def test_gbx_pence_converted_via_gbp_then_usd():
    # GBX ÷ 100 = GBP, then × GBPUSD. 800p → £8 → $10 at 1.25.
    fx = FixedFx(FxQuote(1.25, TD))
    r = _norm(currency="GBP", price_unit="GBX", price_local=800.0,
              adv20_local=1_600_000_000.0, fx_provider=fx)
    assert r.ok and r.price_unit == "GBX"
    assert r.price_usd == 10.0                      # 800/100*1.25
    assert r.adv20_usd == 20_000_000.0              # 1.6e9/100*1.25


def test_eur_major_conversion():
    fx = FixedFx(FxQuote(1.10, TD))
    r = _norm(currency="EUR", price_unit="MAJOR", price_local=10.0,
              adv20_local=20_000_000.0, fx_provider=fx)
    assert r.ok and r.fx_to_usd == 1.10
    assert r.price_usd == pytest.approx(11.0) and r.adv20_usd == pytest.approx(22_000_000.0)


# ── fail-closed FX failure modes (never fall back to local) ───────────────────

def test_missing_provider_for_non_usd_fails_closed():
    r = _norm(currency="GBP", price_unit="MAJOR", fx_provider=None)
    assert not r.ok and r.reason == Reason.FX_CONVERSION_UNAVAILABLE
    assert r.price_usd is None and r.adv20_usd is None


def test_provider_returns_none_fails_closed():
    r = _norm(currency="GBP", price_unit="MAJOR", fx_provider=FixedFx(None))
    assert not r.ok and r.reason == Reason.FX_CONVERSION_UNAVAILABLE


def test_provider_raises_fails_closed():
    class Boom:
        def get_to_usd_rate(self, currency, trading_date):
            raise RuntimeError("fx source down")
    r = _norm(currency="GBP", price_unit="MAJOR", fx_provider=Boom())
    assert not r.ok and r.reason == Reason.FX_CONVERSION_UNAVAILABLE


@pytest.mark.parametrize("rate", [0.0, -1.25, float("nan"), float("inf"), float("-inf")])
def test_invalid_rate_values_fail_closed(rate):
    fx = FixedFx(FxQuote(rate, TD))
    r = _norm(currency="GBP", price_unit="MAJOR", fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_RATE_INVALID
    assert r.price_usd is None


def test_stale_rate_beyond_window_fails_closed():
    stale = TD - timedelta(days=MAX_FX_STALENESS_DAYS + 1)
    fx = FixedFx(FxQuote(1.25, stale))
    r = _norm(currency="GBP", price_unit="MAJOR", fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_RATE_STALE


def test_rate_at_staleness_boundary_is_accepted():
    edge = TD - timedelta(days=MAX_FX_STALENESS_DAYS)     # documented previous session
    fx = FixedFx(FxQuote(1.25, edge))
    r = _norm(currency="GBP", price_unit="MAJOR", price_local=8.0,
              adv20_local=16_000_000.0, fx_provider=fx)
    assert r.ok and r.fx_effective_date == edge and r.price_usd == 10.0


def test_future_rate_rejected():
    fx = FixedFx(FxQuote(1.25, TD + timedelta(days=1)))   # never use a future FX rate
    r = _norm(currency="GBP", price_unit="MAJOR", fx_provider=fx)
    assert not r.ok and r.reason == Reason.FX_RATE_INVALID


def test_unknown_currency_fails_closed():
    r = _norm(currency="JPY", price_unit="MAJOR", fx_provider=FixedFx(FxQuote(0.007, TD)))
    assert not r.ok and r.reason == Reason.CURRENCY_UNKNOWN


@pytest.mark.parametrize("unit", [None, "PENCE", "minor", "foo"])
def test_unknown_or_missing_price_unit_for_non_usd_fails_closed(unit):
    r = _norm(currency="GBP", price_unit=unit, fx_provider=FixedFx(FxQuote(1.25, TD)))
    assert not r.ok and r.reason == Reason.PRICE_UNIT_UNKNOWN


def test_gbx_as_currency_is_ambiguous():
    # 'GBX' in the currency field cannot be disambiguated from GBP-with-default-unit.
    r = _norm(currency="GBX", price_unit="GBX", fx_provider=FixedFx(FxQuote(1.25, TD)))
    assert not r.ok and r.reason == Reason.GBX_GBP_UNIT_AMBIGUOUS


def test_gbx_unit_on_non_gbp_currency_is_ambiguous():
    r = _norm(currency="EUR", price_unit="GBX", fx_provider=FixedFx(FxQuote(1.10, TD)))
    assert not r.ok and r.reason == Reason.GBX_GBP_UNIT_AMBIGUOUS


def test_usd_with_non_major_unit_rejected():
    r = _norm(currency="USD", price_unit="GBX")
    assert not r.ok and r.reason == Reason.PRICE_UNIT_UNKNOWN


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_local_price_fails_closed(bad):
    r = _norm(currency="USD", price_local=bad)
    assert not r.ok and r.reason == Reason.NORMALIZED_PRICE_INVALID


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_invalid_local_adv20_fails_closed(bad):
    r = _norm(currency="USD", adv20_local=bad)
    assert not r.ok and r.reason == Reason.NORMALIZED_ADV20_INVALID


def test_zero_adv20_is_valid_but_below_threshold():
    # 0 dollar-volume is a legitimate (illiquid) value, not an invalid one.
    r = _norm(currency="USD", adv20_local=0.0)
    assert r.ok and r.adv20_usd == 0.0


# ── USD-normalised threshold boundaries (through the eligibility predicate) ────

def _eligibility_for(norm):
    """Build an otherwise-eligible snapshot carrying ONLY the normalised price/adv/reason."""
    snap = {
        "bar_count": params.MIN_HISTORY_BARS + 10,
        "fresh_bar": True, "ohlc_valid": True, "indicators_available": True,
        "research_mapping_ok": True, "ibkr_mapping_ok": True,
        "cooldown_remaining": 0, "corp_action_status": "ok", "sector": "Financials",
        "price": norm.price_usd, "adv20_usd": norm.adv20_usd, "fx_reason": norm.reason,
    }
    return structural_eligibility(snap, mode=ELIGIBILITY_MODE_SHADOW)


def _gbp_norm(price_gbp, adv20_gbp, rate=1.25):
    return _norm(currency="GBP", price_unit="MAJOR", price_local=price_gbp,
                 adv20_local=adv20_gbp, fx_provider=FixedFx(FxQuote(rate, TD)))


@pytest.mark.parametrize("price_gbp,below", [
    (7.99, True),    # 9.9875 USD  → below $10
    (8.00, False),   # 10.00 USD   → at threshold (not below)
    (8.01, False),   # 10.0125 USD → above
])
def test_usd_normalised_price_threshold_boundary(price_gbp, below):
    norm = _gbp_norm(price_gbp, 100_000_000.0)     # adv well above its threshold
    assert norm.ok
    r = _eligibility_for(norm)
    assert (Reason.PRICE_BELOW_MIN in r.reason_codes) is below


@pytest.mark.parametrize("adv20_gbp,below", [
    (15_999_000.0, True),    # 19,998,750 USD → below $20M
    (16_000_000.0, False),   # 20,000,000 USD → at threshold
    (16_001_000.0, False),   # 20,001,250 USD → above
])
def test_usd_normalised_adv20_threshold_boundary(adv20_gbp, below):
    norm = _gbp_norm(20.0, adv20_gbp)              # price ($25) well above its threshold
    assert norm.ok
    r = _eligibility_for(norm)
    assert (Reason.ADV20_BELOW_MIN in r.reason_codes) is below


def test_fx_reason_blocks_eligibility_and_is_recorded():
    # A fail-closed normalisation propagates its reason code and blocks (never silently
    # passes a local value as USD).
    norm = _norm(currency="GBP", price_unit="MAJOR", fx_provider=None)
    r = _eligibility_for(norm)
    assert not r.passes
    assert Reason.FX_CONVERSION_UNAVAILABLE in r.reason_codes
    assert Reason.ELIGIBLE not in r.reason_codes


# ── evaluator-level: provider invocation discipline ───────────────────────────

def _seed(tmp_path, instruments):
    p1, p2 = write_configs(tmp_path, instruments, [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def test_flag_false_invokes_no_fx_provider(tmp_path):
    db = _seed(tmp_path, [inst("BARC", currency="GBP", exchange="SMART")])
    reg = Registry(db)
    cid = canonical_id("BARC", "GBP", "SMART")
    spy_fx = StaticFxRateProvider({"GBP": 1.25})
    ev = ShadowEvaluator(reg, SpyProvider({cid: {"bars": make_bars(), "price_unit": "MAJOR",
                                                 "corp_action_status": "ok", "sector": "Fin"}}),
                         OFF, equity=100_000, fx_provider=spy_fx)
    r = ev.maybe_run("2026-06-10")
    assert r == {"ran": False, "reason": "flag_off", "evaluated": 0}
    assert spy_fx.calls == []                   # flag off → FX provider never consulted


def test_usd_instrument_does_not_invoke_fx_provider(tmp_path):
    db = _seed(tmp_path, [inst("AAPL", currency="USD", exchange="NASDAQ")])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    spy_fx = StaticFxRateProvider({"GBP": 1.25})
    ev = ShadowEvaluator(reg, SpyProvider({cid: {"bars": make_bars(), "corp_action_status": "ok",
                                                 "sector": "Tech"}}),
                         ON, equity=100_000, fx_provider=spy_fx)
    ev.maybe_run("2026-06-10")
    assert spy_fx.calls == []                   # USD assumed 1.0; provider untouched


def test_gbp_instrument_normalised_and_provider_called(tmp_path):
    db = _seed(tmp_path, [inst("BARC", currency="GBP", exchange="SMART")])
    reg = Registry(db)
    cid = canonical_id("BARC", "GBP", "SMART")
    spy_fx = StaticFxRateProvider({"GBP": 1.25})
    ev = ShadowEvaluator(reg, SpyProvider({cid: {"bars": make_bars(), "price_unit": "MAJOR",
                                                 "corp_action_status": "ok", "sector": "Fin"}}),
                         ON, equity=100_000, fx_provider=spy_fx)
    o = ev.maybe_run("2026-06-10")["outcomes"][0]
    assert spy_fx.calls and spy_fx.calls[0][0] == "GBP"
    # the persisted snapshot carries USD-normalised adv20 (price_local × volume × rate)
    assert o["adv20"] is not None


def test_non_usd_without_provider_is_data_ineligible(tmp_path):
    # No FX provider + GBP instrument → fail-closed, never treated as USD.
    db = _seed(tmp_path, [inst("BARC", currency="GBP", exchange="SMART")])
    reg = Registry(db)
    cid = canonical_id("BARC", "GBP", "SMART")
    ev = ShadowEvaluator(reg, SpyProvider({cid: {"bars": make_bars(), "price_unit": "MAJOR",
                                                 "corp_action_status": "ok", "sector": "Fin"}}),
                         ON, equity=100_000, fx_provider=None)
    o = ev.maybe_run("2026-06-10")["outcomes"][0]
    assert Reason.FX_CONVERSION_UNAVAILABLE in o["reason_codes"]
