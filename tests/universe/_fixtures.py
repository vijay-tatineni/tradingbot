"""Shared test helpers for the Dynamic Universe shadow tests (synthetic data only —
no downloads, no broker, no live DB)."""
import json

import numpy as np
import pandas as pd


def make_bars(n=300, start=100.0, slope=1.0, volume=2_000_000.0):
    """Deterministic OHLCV frame. slope>0 → clean uptrend (fires breakout entry +
    high ADX); slope==0 → flat (no breakout signal)."""
    close = start + slope * np.arange(n, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "open": close - 0.3, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.full(n, float(volume)),
    })


class FlagsStub:
    """Minimal flags object supporting .get(name) (mirrors FeatureFlags.get)."""
    def __init__(self, **vals):
        self._vals = vals

    def get(self, name):
        return bool(self._vals.get(name, False))


ON = FlagsStub(enable_dynamic_universe_shadow=True)
OFF = FlagsStub(enable_dynamic_universe_shadow=False)


def write_configs(tmp_path, instruments, ig_instruments=None):
    """Write minimal instruments.json + instruments_ig.json into tmp_path.
    Returns (instruments_path, instruments_ig_path)."""
    ibkr = {"_version": "1.0", "settings": {"broker": "ibkr"},
            "layer1_active": instruments, "layer2_accumulation": [], "layer3_silver": []}
    ig = {"_version": "1.0", "settings": {"broker": "ig"},
          "layer1_active": ig_instruments if ig_instruments is not None else [],
          "layer2_accumulation": [], "layer3_silver": []}
    p1 = tmp_path / "instruments.json"
    p2 = tmp_path / "instruments_ig.json"
    p1.write_text(json.dumps(ibkr, indent=2))
    p2.write_text(json.dumps(ig, indent=2))
    return str(p1), str(p2)


def inst(symbol, currency="USD", exchange="NASDAQ", enabled=True,
         hard_disabled=False, **extra):
    d = {"symbol": symbol, "currency": currency, "exchange": exchange,
         "enabled": enabled, "name": symbol, "sec_type": "STK"}
    if hard_disabled:
        d["hard_disabled"] = True
        d["enabled"] = False
    d.update(extra)
    return d


class SpyProvider:
    """Bars provider that records calls and asserts no broker is ever touched.
    Maps canonical_instrument_id -> source dict."""
    def __init__(self, sources):
        self.sources = sources
        self.calls = []

    def __call__(self, rec):
        cid = rec["canonical_instrument_id"]
        self.calls.append(cid)
        return self.sources.get(cid)


def _date_iso(trading_date):
    return trading_date.isoformat() if hasattr(trading_date, "isoformat") else str(trading_date)


class StaticFxRateProvider:
    """Broker-free, deterministic FxRateProvider stub (tests / rehearsal only).

    Returns a fixed to-USD rate per currency, effective ON the requested trading date
    (zero staleness) unless an explicit ``as_of`` offset is configured. It never imports
    or calls a broker / EODHD / external FX API; the rates are hard-coded fixtures.

    rates:   {currency: rate_to_usd}
    as_of_by_ccy: {currency: date} → force a specific effective date (to test freshness)
    """
    def __init__(self, rates=None, as_of_by_ccy=None):
        self.rates = {k.upper(): float(v) for k, v in (rates or
                      {"GBP": 1.25, "EUR": 1.10}).items()}
        self.as_of_by_ccy = dict(as_of_by_ccy or {})
        self.calls = []

    def get_to_usd_rate(self, currency, trading_date):
        from bot.universe.fx import FxQuote
        cur = (currency or "").upper()
        self.calls.append((cur, _date_iso(trading_date)))
        if cur not in self.rates:
            return None
        as_of = self.as_of_by_ccy.get(cur, trading_date)
        return FxQuote(rate=self.rates[cur], as_of=as_of)


class StubPositionProvider:
    """Broker-free, read-only PositionSnapshotProvider stub (tests / rehearsal only).

    Maps canonical_instrument_id -> PositionStatus, with an optional per-(cid, date)
    override. Records every call so tests can prove the seam was used; it never imports
    or calls a broker. Returning a non-PositionStatus value lets tests exercise the
    evaluator's fail-safe UNKNOWN coercion."""
    def __init__(self, statuses=None, by_date=None):
        from bot.universe.models import PositionStatus
        self._default = PositionStatus.NO_POSITION
        self.statuses = dict(statuses or {})
        self.by_date = dict(by_date or {})
        self.calls = []

    def get_position_status(self, canonical_instrument_id, trading_date):
        self.calls.append((canonical_instrument_id, _date_iso(trading_date)))
        key = (canonical_instrument_id, _date_iso(trading_date))
        if key in self.by_date:
            return self.by_date[key]
        return self.statuses.get(canonical_instrument_id, self._default)


def iref(display_symbol="AAPL", isin="US0378331005", figi="BBG000B9XRY4", mic="XNAS",
         currency="USD", conid="265598", epic=None, name="Apple Inc",
         effective="2026-06-10", verified="2026-06-10T12:00:00", exchange="XNAS",
         status="VERIFIED", price_unit=None):
    """Build an IdentityReference for tests (broker-free; a fixture, not a provider call)."""
    from datetime import date, datetime

    from bot.universe.identity import IdentityReference, IdentityReferenceStatus
    return IdentityReference(
        status=IdentityReferenceStatus(status),
        instrument_uid=None, listing_uid=None,
        isin=isin, figi=figi, mic=mic, currency=currency, display_symbol=display_symbol,
        source="secmaster_fixture",
        effective_date=date.fromisoformat(effective),
        verified_at=datetime.fromisoformat(verified),
        ibkr_conid=conid, ig_epic=epic, instrument_name=name,
        price_unit=price_unit, exchange=exchange)


class StubIdentityProvider:
    """Broker-free, INJECTED IdentityReferenceProvider stub (tests / rehearsal only).

    Maps (display_symbol, mic, currency) -> IdentityReference, or raises a configured
    exception, to exercise the resolver's fail-closed behaviour. Never imports/calls a
    broker or data provider; the references are hard-coded fixtures."""
    def __init__(self, by_coords=None, raises=None):
        self.by_coords = dict(by_coords or {})
        self.raises = raises
        self.calls = []

    def resolve(self, *, display_symbol, mic, currency, as_of):
        self.calls.append((display_symbol, mic, currency, str(as_of)))
        if self.raises is not None:
            raise self.raises
        return self.by_coords.get((display_symbol, mic, currency))


def resolve_instrument(db, symbol="AAPL", isin="US0378331005", figi=None, mic="XNAS",
                       currency="USD", conid="265598", trading_date="2026-06-12",
                       canonical_instrument_id=None):
    """Resolve + persist a VERIFIED identity/listing (+ IBKR mapping) for a test instrument.
    Returns the resolve result dict ({instrument_uid, listing_uid, ...})."""
    from bot.universe.identity_store import IdentityStore
    st = IdentityStore(db)
    return st.resolve_identity_atomic(
        iref(symbol, isin=isin, figi=figi, mic=mic, currency=currency, conid=conid,
             exchange=mic, effective="2026-06-10", verified="2026-06-10T12:00:00"),
        trading_date, "r2b_resolver_v1", canonical_instrument_id=canonical_instrument_id)


def flat():
    """A fresh authoritative position provider reporting NO_POSITION for everything.

    Under the P3-8 contract the evaluator yields UNKNOWN (a safe non-entry hold) when NO
    provider is injected, so tests that intend ordinary flat→eligible behaviour must inject
    an explicit flat provider. A fresh instance per call keeps `.calls` isolated."""
    return StubPositionProvider()


# ── R2C broker-free test doubles (BLOCKER-S / P3-5) ────────────────────
class SpyBaseFxRateProvider:
    """Broker-free, deterministic to-BASE FxRate provider stub (tests / rehearsal only).

    Returns a fixed rate (BASE per 1 instrument unit) per (instrument_currency, base_currency),
    effective at the evaluation_time (fresh) unless an explicit ``as_of`` is configured. Records
    every call (``.calls``) so a test can prove the off-path NEVER consults it. Never imports or
    calls a broker / EODHD / external FX API; rates are hard-coded fixtures."""
    def __init__(self, rates=None, as_of=None, raises=None, mismatch=False, bad_rate=None):
        # rates: {(INST, BASE): rate}; defaults USD->EUR=0.9, GBP->USD=1.25. An explicitly
        # EMPTY dict means "no rates" (provider returns None) — only None requests the defaults.
        _r = rates if rates is not None else {("USD", "EUR"): "0.90", ("GBP", "USD"): "1.25"}
        self.rates = {(str(k[0]).upper(), str(k[1]).upper()): v for k, v in _r.items()}
        self.as_of = as_of
        self.raises = raises
        self.mismatch = mismatch        # return a rate whose currencies disagree with the request
        self.bad_rate = bad_rate        # force this (e.g. 0/negative) rate value
        self.calls = []

    def get_rate(self, *, instrument_currency, base_currency, evaluation_time):
        from decimal import Decimal

        from bot.universe.sizing import FxRate
        self.calls.append((instrument_currency, base_currency, _date_iso(evaluation_time)))
        if self.raises is not None:
            raise self.raises
        key = (str(instrument_currency).upper(), str(base_currency).upper())
        if self.bad_rate is not None:
            rate = Decimal(str(self.bad_rate))
        elif key in self.rates:
            rate = Decimal(str(self.rates[key]))
        else:
            return None
        as_of = self.as_of if self.as_of is not None else evaluation_time
        if self.mismatch:
            return FxRate("ZZZ", base_currency, rate, as_of, "spy_fx")
        return FxRate(instrument_currency, base_currency, rate, as_of, "spy_fx")


class SpyPortfolioRiskProvider:
    """Broker-free, read-only PortfolioRiskProvider stub (tests / rehearsal only). Returns a
    fixed snapshot and records every call (``.calls``) so a test can prove the off-path never
    consults it. Never imports or calls a broker."""
    def __init__(self, snapshot=None, raises=None):
        self._snapshot = snapshot
        self.raises = raises
        self.calls = []

    def snapshot(self, *, evaluation_time):
        self.calls.append(_date_iso(evaluation_time))
        if self.raises is not None:
            raise self.raises
        return self._snapshot


def fresh_snapshot(*, base_currency="USD", evaluation_time, trading_date,
                   positions=(), orders=(), equity="1000000", equity_as_of=None):
    """Build a fresh PortfolioRiskSnapshot for tests (date == trading_date, timestamp ==
    evaluation_time → zero age). positions/orders are PositionRisk tuples."""
    from bot.universe.portfolio_heat import PortfolioRiskSnapshot
    return PortfolioRiskSnapshot(
        base_currency=base_currency, open_positions=tuple(positions),
        open_orders=tuple(orders), equity_base=equity,
        equity_as_of=equity_as_of if equity_as_of is not None else evaluation_time,
        snapshot_date=trading_date, snapshot_timestamp=evaluation_time, source="spy")


def pos_risk(risk_base, *, currency="USD", instrument_uid="iuid-1", observed_at=None):
    """Build one PositionRisk contributing ``risk_base`` normalized risk (tests)."""
    from bot.universe.portfolio_heat import PositionRisk
    return PositionRisk(currency=currency, normalized_risk_base=risk_base,
                        instrument_uid=instrument_uid, observed_at=observed_at)


# ── W1 broker-free completed-bar provider stub (BLOCKER-W1) ────────────
class StubCompletedBarProvider:
    """Broker-free, INJECTED completed-bar availability stub (tests only). Records every call
    (``.calls``) so a test can prove the off/invalid path never consults it. Never imports or
    calls a broker / IBKR / IG / EODHD / live data API — it returns hard-coded fixtures.

    Modes:
      available (default) → a complete, fresh snapshot covering the requested trading_date.
      raises=<exc>        → raise (exercise fail-closed on provider exception).
      mode='unavailable'  → available=False (e.g. holiday / late bar).
      mode='incomplete'   → available=True but missing source/version proof.
      mode='stale'        → available=True but bar_end_time on a DIFFERENT date.
      is_live=True        → declares a live integration (must be rejected by validation).
    """
    def __init__(self, mode="available", raises=None, is_live=False, source="stub_bars",
                 version="v1"):
        self.mode = mode
        self.raises = raises
        self.is_live = bool(is_live)
        self.source = source
        self.version = version
        self.calls = []

    def completed_bar(self, *, record, trading_date, timeframe):
        from bot.universe.bar_provider import CompletedBarSnapshot
        self.calls.append((record.get("instrument_uid") or record.get("canonical_instrument_id"),
                           _date_iso(trading_date), timeframe))
        if self.raises is not None:
            raise self.raises
        td = _date_iso(trading_date)
        base = dict(trading_date=td, timeframe=timeframe,
                    instrument_uid=record.get("instrument_uid"),
                    listing_uid=record.get("listing_uid"))
        if self.mode == "unavailable":
            return CompletedBarSnapshot(available=False, reason="bar_unavailable", **base)
        if self.mode == "incomplete":
            return CompletedBarSnapshot(available=True, bar_end_time=f"{td}T21:00:00Z",
                                        source=None, version=None, **base)
        if self.mode == "stale":
            return CompletedBarSnapshot(available=True, bar_end_time="2000-01-01T21:00:00Z",
                                        source=self.source, version=self.version, **base)
        return CompletedBarSnapshot(available=True, bar_end_time=f"{td}T21:00:00Z",
                                    source=self.source, version=self.version, **base)
