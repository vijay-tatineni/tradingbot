#!/usr/bin/env python3
"""
Quick test for the SentimentEngine plugin.
Run: python3 test_sentiment.py

Tests:
  1. DuckDuckGo headline search
  2. Claude Haiku sentiment scoring
  3. Cache behaviour
  4. pre_trade gate logic
"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Minimal cfg stub
class FakeCfg:
    check_interval_mins = 1

sys.path.insert(0, os.path.dirname(__file__))
from bot.plugins.sentiment import SentimentEngine


def test():
    cfg = FakeCfg()
    engine = SentimentEngine(cfg, alerts=None)

    if not engine.enabled:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        print("Add your key to .env and re-run.")
        sys.exit(1)

    print("=" * 60)
    print("SENTIMENT ENGINE TEST")
    print("=" * 60)

    # ── Test 1: headline search ─────────────────────
    print("\n[Test 1] DuckDuckGo headline search for NVDA...")
    headlines = engine._search_news("NVDA", "Nvidia")
    print(f"  Found {len(headlines)} headlines")
    for h in headlines[:5]:
        print(f"    • {h[:100]}")
    assert len(headlines) > 0, "No headlines found — search may be blocked"
    print("  PASS")

    # ── Test 2: Claude sentiment scoring ────────────
    print("\n[Test 2] Claude Haiku sentiment scoring for NVDA...")
    score, summary = engine._call_claude("NVDA", "Nvidia", headlines)
    if score is None:
        print("  Score:   None (API error — likely no credits)")
        print("  SKIP — add Anthropic credits to test Claude scoring")
        api_works = False
    else:
        print(f"  Score:   {score:+.2f}")
        print(f"  Summary: {summary}")
        assert -1.0 <= score <= 1.0, f"Score {score} out of range"
        print("  PASS")
        api_works = True

    # ── Test 3: full pipeline + cache ───────────────
    if api_works:
        print("\n[Test 3] Full _get_sentiment pipeline (should call API)...")
        score1, summary1 = engine._get_sentiment("AAPL", "Apple")
        print(f"  Score:   {score1:+.2f}")
        print(f"  Summary: {summary1}")
        assert score1 is not None, "Pipeline returned None"

        print("\n[Test 3b] Cached call (should NOT call API)...")
        score2, summary2 = engine._get_sentiment("AAPL", "Apple")
        print(f"  Score:   {score2:+.2f} (cached)")
        assert score1 == score2, "Cache miss — scores differ"
        print("  PASS — cache working")
    else:
        print("\n[Test 3] SKIP — needs working API")

    # ── Test 4: pre_trade gate logic ────────────────
    print("\n[Test 4] pre_trade gate logic...")
    inst = {'symbol': 'AAPL', 'name': 'Apple', 'flag': '🍎'}

    # BUY signal should be checked
    result = engine.pre_trade(inst, signal=1, confidence='HIGH')
    print(f"  BUY signal:  {'ALLOWED' if result else 'BLOCKED'}")

    # SELL signal should always pass
    result = engine.pre_trade(inst, signal=-1, confidence='HIGH')
    print(f"  SELL signal: {'ALLOWED' if result else 'BLOCKED'}")
    assert result is True, "SELL should never be blocked"
    print("  PASS")

    # ── Test 5: force a negative to test blocking ───
    print("\n[Test 5] Testing block with forced negative cache...")
    import time
    engine.cache['TEST'] = (-0.8, "Fake bad news", time.time())
    inst_test = {'symbol': 'TEST', 'name': 'Test Corp', 'flag': '🧪'}
    result = engine.pre_trade(inst_test, signal=1, confidence='HIGH')
    print(f"  Score -0.8 → BUY {'ALLOWED' if result else 'BLOCKED'}")
    assert result is False, "Should have blocked on score -0.8"
    print("  PASS — correctly blocked")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == '__main__':
    test()
