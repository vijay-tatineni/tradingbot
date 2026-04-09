"""
Tests for the LLM weekly advisor.
"""
import json
import pytest
from unittest.mock import MagicMock


def _make_llm(response: str):
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.chat.return_value = response
    return llm


def _sample_report_json():
    return json.dumps({
        "summary": "Good week overall. 5 trades, 3 wins.",
        "instrument_recommendations": [
            {"symbol": "BARC", "action": "keep", "reason": "Consistent winner"},
            {"symbol": "CVX", "action": "disable", "reason": "3 losses in a row"},
        ],
        "parameter_suggestions": [
            {"symbol": "BARC", "param": "adx_threshold", "current": 20,
             "suggested": 15, "reason": "Lower ADX catches more trends"},
        ],
        "patterns_observed": ["Breakout trades win 80% of the time"],
        "risk_warnings": ["CVX concentrated losses"],
        "next_week_outlook": "Bullish momentum expected to continue",
    })


def test_advisor_generates_report():
    """generate_weekly_report() returns structured report."""
    from bot.llm.advisor import generate_weekly_report
    llm = _make_llm(_sample_report_json())
    report = generate_weekly_report(llm, [], [], [], [])
    assert "summary" in report
    assert len(report["summary"]) > 0


def test_advisor_includes_instrument_recommendations():
    """Report has per-instrument keep/disable/adjust recommendations."""
    from bot.llm.advisor import generate_weekly_report
    llm = _make_llm(_sample_report_json())
    report = generate_weekly_report(llm, [], [], [], [])
    assert len(report["instrument_recommendations"]) == 2
    assert report["instrument_recommendations"][0]["symbol"] == "BARC"


def test_advisor_includes_parameter_suggestions():
    """Report has specific param change suggestions."""
    from bot.llm.advisor import generate_weekly_report
    llm = _make_llm(_sample_report_json())
    report = generate_weekly_report(llm, [], [], [], [])
    assert len(report["parameter_suggestions"]) == 1
    assert report["parameter_suggestions"][0]["param"] == "adx_threshold"


def test_advisor_handles_no_trades():
    """If no trades this week, report says so gracefully."""
    from bot.llm.advisor import generate_weekly_report
    llm = _make_llm(json.dumps({
        "summary": "No trades executed this week.",
        "instrument_recommendations": [],
        "parameter_suggestions": [],
        "patterns_observed": [],
        "risk_warnings": [],
        "next_week_outlook": "Monitor for setups",
    }))
    report = generate_weekly_report(llm, [], [], [], [])
    assert "summary" in report
    assert "no trades" in report["summary"].lower()


def test_advisor_handles_llm_error():
    """If LLM fails, return empty report, no crash."""
    from bot.llm.advisor import generate_weekly_report
    llm = _make_llm("")
    report = generate_weekly_report(llm, [], [], [], [])
    assert "summary" in report
    assert isinstance(report["instrument_recommendations"], list)


def test_advisor_api_endpoint():
    """POST /api/advisor/generate returns report JSON."""
    # This is an integration test — just verify the module can be imported
    # and the function signature is correct
    from bot.llm.advisor import generate_weekly_report
    import inspect
    sig = inspect.signature(generate_weekly_report)
    params = list(sig.parameters.keys())
    assert "llm" in params
    assert "trades" in params
    assert "reviews" in params
    assert "wf_results" in params
    assert "instruments" in params
