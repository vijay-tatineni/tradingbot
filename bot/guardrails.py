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


class ConfigGuardrailError(RuntimeError):
    """Raised when a loaded config violates a hard guardrail."""


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
