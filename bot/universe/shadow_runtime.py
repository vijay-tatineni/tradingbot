"""Dynamic Universe — BLOCKER-W2: lazy, flag-gated, side-effect-free shadow construction.

Constructing a ``Registry`` / ``CandidateStore`` / ``IdentityStore`` (or calling
``seed_registry``) runs ``migrate(db_path)`` → ``sqlite3.connect(db_path)``, which CREATES the
DB file — even with the feature flag off. So eager construction (e.g. in ``TradingBot.__init__``)
would create/migrate a universe DB at process startup regardless of the flag.

This module provides the safe boundary the future runtime wiring (Gate C) must use instead:

  1. ``validate_shadow_config`` — a PURE validation with NO filesystem side effects (no stat, no
     open, no connect, no migrate). It decides whether shadow construction is permitted and, if
     not, returns a stable fail-closed reason.
  2. ``build_shadow_scheduler`` — a lazy factory that validates FIRST and constructs the
     Registry/evaluator/scheduler ONLY when the flag is on and the config is fully valid. When
     disabled or invalid it returns a fail-closed result and constructs NOTHING (no Registry, no
     store, no provider, no ``sqlite3.connect``, no ``migrate``, no DB file).

This module imports the heavy universe modules LAZILY (inside the default factories) so importing
it has no side effects, and it imports NO broker. It is NOT wired into any runtime startup here
(Gate C prerequisites only); no DB is created and no provider is called by this module.
"""
import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("universe.shadow_runtime")

FLAG = "enable_dynamic_universe_shadow"

# Production DB basenames the shadow path must NEVER open/create/collide with. ``universe.db`` is
# included so the production research DB stays ABSENT — the shadow path must use a dedicated file
# (e.g. ``universe_shadow.db``). Compared by basename only (a pure string op; no filesystem stat).
PRODUCTION_DB_BASENAMES = frozenset({
    "positions.db", "regime.db", "backtest.db", "learning_loop.db", "layer3_silver.db",
    "advisor.db", "news.db", "trades.db", "trading.db", "universe.db",
})

# Stable fail-closed reason codes.
REASON_FLAG_OFF = "flag_off"
REASON_DB_PATH_MISSING = "shadow_db_path_missing"
REASON_DB_PATH_UNSAFE = "shadow_db_path_unsafe"
REASON_BARS_PROVIDER_MISSING = "bars_provider_missing"
REASON_COMPLETED_BAR_PROVIDER_MISSING = "completed_bar_provider_missing"
REASON_LIVE_PROVIDER_REQUIRES_APPROVAL = "live_provider_requires_approval"


@dataclass(frozen=True)
class ShadowConfigResult:
    """Result of ``validate_shadow_config`` — a pure decision, no side effects.

    ``ok`` is True only when shadow construction is permitted. ``enabled`` records the raw flag
    state (False + ``ok=False`` + ``reason='flag_off'`` is the normal disabled posture, not an
    error). ``db_path`` is the validated path to use, populated only when ``ok``."""
    ok: bool
    enabled: bool
    reason: Optional[str] = None
    db_path: Optional[str] = None


@dataclass(frozen=True)
class ShadowBuildResult:
    """Result of ``build_shadow_scheduler``. ``scheduler`` is populated only when ``ok`` is True;
    otherwise it is None and ``reason`` carries the stable fail-closed code. When ``ok`` is False
    NOTHING was constructed and the filesystem was not touched."""
    ok: bool
    reason: Optional[str] = None
    scheduler: object = None


def _flag_on(flags) -> bool:
    """Read the master flag, fail-closed (any error → treated as off)."""
    try:
        return bool(flags.get(FLAG))
    except Exception:
        return False


def _provider_is_live(provider) -> bool:
    """A provider may declare ``is_live = True`` to mark itself a LIVE integration. Such providers
    require a separate explicit approval and are rejected by default. Absent the attribute a
    provider is treated as non-live (the injected stub/cached default)."""
    return bool(getattr(provider, "is_live", False))


def validate_shadow_config(*, flags, db_path: Optional[str], bars_provider,
                           completed_bar_provider) -> ShadowConfigResult:
    """Validate shadow runtime config with NO filesystem side effects (no stat/open/connect).

    Returns ``ok=False`` (fail closed) with a stable reason when: the flag is off; the flag is on
    but the shadow DB path is missing; the path collides with a production DB basename; a required
    provider (data bars / completed-bar availability) is missing; or a provided provider declares
    itself live (requires separate approval). Returns ``ok=True`` with the validated ``db_path``
    only when every check passes. Never constructs anything and never touches the filesystem."""
    enabled = _flag_on(flags)
    if not enabled:
        return ShadowConfigResult(ok=False, enabled=False, reason=REASON_FLAG_OFF)
    if not db_path or not str(db_path).strip():
        return ShadowConfigResult(ok=False, enabled=True, reason=REASON_DB_PATH_MISSING)
    base = os.path.basename(os.path.normpath(str(db_path)))
    if base in PRODUCTION_DB_BASENAMES:
        return ShadowConfigResult(ok=False, enabled=True, reason=REASON_DB_PATH_UNSAFE)
    if bars_provider is None:
        return ShadowConfigResult(ok=False, enabled=True,
                                  reason=REASON_BARS_PROVIDER_MISSING)
    if completed_bar_provider is None:
        return ShadowConfigResult(ok=False, enabled=True,
                                  reason=REASON_COMPLETED_BAR_PROVIDER_MISSING)
    if _provider_is_live(bars_provider) or _provider_is_live(completed_bar_provider):
        return ShadowConfigResult(ok=False, enabled=True,
                                  reason=REASON_LIVE_PROVIDER_REQUIRES_APPROVAL)
    return ShadowConfigResult(ok=True, enabled=True, reason=None, db_path=str(db_path))


def _default_registry_factory(db_path: str):
    """Construct a real Registry (lazy import). This is the ONLY place the DB is opened/migrated;
    it runs solely on the validated, flag-on path."""
    from bot.universe.registry import Registry
    return Registry(db_path)


def _default_evaluator_factory(*, registry, bars_provider, flags, evaluator_kwargs):
    from bot.universe.evaluator import ShadowEvaluator
    return ShadowEvaluator(registry, bars_provider, flags, **(evaluator_kwargs or {}))


def build_shadow_scheduler(*, flags, db_path: Optional[str], bars_provider,
                           completed_bar_provider,
                           now_fn: Optional[Callable] = None,
                           evaluator_kwargs: Optional[dict] = None,
                           _registry_factory: Optional[Callable] = None,
                           _evaluator_factory: Optional[Callable] = None) -> ShadowBuildResult:
    """Lazy, flag-gated factory for the shadow scheduler.

    Validates FIRST (``validate_shadow_config``); if not ok, returns a fail-closed
    ``ShadowBuildResult`` having constructed NOTHING (no Registry/store/provider, no
    ``sqlite3.connect``, no ``migrate``, no DB file, no provider call). Only when the flag is on
    and the config is fully valid does it lazily construct the Registry (the single DB-opening
    site), the evaluator, and the ``DailyUniverseScheduler`` (whose completed-bar gate is the W1
    boundary). ``_registry_factory`` / ``_evaluator_factory`` are test seams (inject spies to
    prove construction is invoked exactly when — and only when — permitted, with no real DB)."""
    cfg = validate_shadow_config(flags=flags, db_path=db_path, bars_provider=bars_provider,
                                 completed_bar_provider=completed_bar_provider)
    if not cfg.ok:
        return ShadowBuildResult(ok=False, reason=cfg.reason, scheduler=None)

    reg_factory = _registry_factory or _default_registry_factory
    ev_factory = _evaluator_factory or _default_evaluator_factory
    registry = reg_factory(cfg.db_path)               # <-- ONLY DB open/migrate site
    evaluator = ev_factory(registry=registry, bars_provider=bars_provider, flags=flags,
                           evaluator_kwargs=evaluator_kwargs)
    from bot.universe.bar_provider import as_bar_available_fn
    from bot.universe.scheduler import DailyUniverseScheduler
    scheduler = DailyUniverseScheduler(
        evaluator, flags, now_fn=now_fn,
        bar_available_fn=as_bar_available_fn(completed_bar_provider))
    return ShadowBuildResult(ok=True, reason=None, scheduler=scheduler)
