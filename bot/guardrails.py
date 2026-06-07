"""
bot/guardrails.py — config safety guardrails.

No-edge guardrail
-----------------
Refuse to run (and refuse to save via the API) when an ENABLED layer1
instrument's notes imply no / marginal edge unless it carries an explicit
override:

  * experiment: true            → kept in a paper experiment on purpose
  * allow_new_entries: false    → exits-only, no new risk

This stops a known-marginal instrument from silently going live via
dashboard/optimiser drift. It deliberately keys off the *notes* text: it
catches the ANET/NBIS class (notes literally say "no edge" / "marginal")
and is NOT a general "no silent additions" gate — an instrument with clean
or empty notes passes straight through.
"""

BAD_EDGE_TERMS = ("no edge", "marginal")

# Hard-disabled invariant
# -----------------------
# An instrument may be administratively / broker-eligibility hard-disabled.
# `hard_disabled: true` is the authoritative structured flag. The legacy
# free-text reason below is accepted as backward-compatible defense-in-depth
# ONLY — safety must never depend solely on matching this string.
HARD_DISABLED_REASON = "no_cfd_market_data_paper_account"


class ConfigGuardrailError(RuntimeError):
    """Raised when a loaded config violates a hard guardrail."""


class HardDisabledViolation(ValueError):
    """Raised when a write would enable a hard-disabled instrument.

    Subclasses ValueError so existing ValueError handling in save() still
    fires, while the type lets API routes map it to HTTP 409 instead of an
    unhandled 500.
    """


def validate_no_edge_guardrails(config: dict) -> list:
    """Return a list of error strings (empty list = config is clean).

    An enabled layer1 instrument whose notes contain a BAD_EDGE_TERM must
    carry experiment:true or allow_new_entries:false, else it is flagged.
    """
    errors = []
    for inst in config.get("layer1_active", []):
        if not inst.get("enabled", True):
            continue
        notes = (inst.get("notes") or "").lower()
        is_known_bad = any(term in notes for term in BAD_EDGE_TERMS)
        if not is_known_bad:
            continue
        is_experiment = inst.get("experiment") is True
        is_exits_only = inst.get("allow_new_entries") is False
        if not is_experiment and not is_exits_only:
            errors.append(
                f"{inst.get('symbol')} notes imply no/marginal edge but "
                "instrument is enabled without experiment:true or "
                "allow_new_entries:false"
            )
    return errors


def validate_hard_disabled_instruments(config: dict) -> list:
    """Return a list of error strings (empty list = config is clean).

    Hard-disabled invariant: no normal write may leave a broker-ineligible /
    administratively hard-disabled instrument enabled. An instrument is
    flagged when:

        enabled == true
        AND (
            hard_disabled == true
            OR disabled_reason == HARD_DISABLED_REASON
        )

    `hard_disabled is True` is the authoritative structured flag. The
    free-text reason match is backward-compatible defense-in-depth only;
    safety does not depend on it alone.

    A missing `enabled` key defaults to True (matching the bot's own
    `i.get('enabled', True)` semantics) so a malformed config that omits the
    flag still fails closed.

    Re-enabling a hard-disabled instrument must instead go through a
    separate, explicitly named, audited administrative endpoint — never a
    normal dashboard toggle, full save or walk-forward application.
    """
    errors = []
    for inst in config.get("layer1_active", []):
        if not inst.get("enabled", True):
            continue
        is_hard_disabled = inst.get("hard_disabled") is True
        is_legacy_reason = inst.get("disabled_reason") == HARD_DISABLED_REASON
        if is_hard_disabled or is_legacy_reason:
            if is_hard_disabled:
                why = "hard_disabled:true"
            else:
                why = f"disabled_reason=='{HARD_DISABLED_REASON}'"
            errors.append(
                f"{inst.get('symbol')} is hard-disabled ({why}) and cannot be "
                "enabled by a normal write; use the administrative override "
                "endpoint instead"
            )
    return errors
