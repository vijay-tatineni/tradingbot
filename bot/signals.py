"""
bot/signals.py
Triple Confirmation Signal Engine.

Evaluates all three indicators and returns a final signal.
This is the brain of the bot — pure logic, no IB calls, no side effects.

Signal flow:
  Gate 1: Alligator must NOT be SLEEPING
  Gate 2: All 3 must agree for BUY (alligator BULL + ma200 BULL + wr BULL)
  Gate 3: All 3 must agree for SELL (alligator BEAR + ma200 BEAR + wr BEAR)
  Partial (2/3): HOLD
"""

from dataclasses import dataclass
from typing import Optional
from bot.indicators import IndicatorBundle


@dataclass
class SignalResult:
    """
    The output of the triple confirmation engine for one instrument.

    signal     :  1 = BUY,  0 = HOLD,  -1 = SELL
    confidence : 'HIGH' | 'MEDIUM' | 'LOW'
    reason     :  human-readable explanation
    ma200_str  :  display string e.g. '>3978' or '<485'
    """
    signal:     int
    confidence: str
    reason:     str
    ma200_str:  str


class SignalEngine:
    """
    Stateless triple confirmation engine.
    Create once, call evaluate() per instrument per cycle.

    Extensible: subclass or wrap to add AI override, sentiment gate etc.
    """

    def evaluate(self, bundle: Optional[IndicatorBundle]) -> SignalResult:
        """
        Run triple confirmation on a populated IndicatorBundle.
        Returns SignalResult with signal, confidence, reason and display strings.
        """
        if bundle is None:
            return SignalResult(0, 'LOW', 'No data', '--')

        al  = bundle.alligator
        ma  = bundle.ma200
        wr  = bundle.wr
        p   = bundle.price

        # Display string for MA200 column
        ma200_str = f"{'>' if ma.trend == 'BULL' else '<'}{int(ma.value or 0)}" if ma.value else 'N/A'

        # ── Gate 1: Alligator must be awake ──────────────────
        if al.state == 'SLEEPING':
            return SignalResult(0, 'LOW', 'Alligator SLEEPING — sideways market, no trade', ma200_str)

        # ── Score bull and bear signals ───────────────────────
        wr_bull = wr.signal in ('CROSS_UP', 'ABOVE')
        wr_bear = wr.signal in ('CROSS_DOWN', 'BELOW')

        bull_score = sum([al.direction == 'BULL', ma.trend == 'BULL', wr_bull])
        bear_score = sum([al.direction == 'BEAR', ma.trend == 'BEAR', wr_bear])

        # ── Gate 2: All 3 must agree ──────────────────────────
        if bull_score == 3:
            conf   = 'HIGH' if al.state == 'EATING' else 'MEDIUM'
            reason = f"ALL 3 BULL: Alligator {al.state}, above MA200, WR {wr.value:.1f}"
            return SignalResult(1, conf, reason, ma200_str)

        if bear_score == 3:
            conf   = 'HIGH' if al.state == 'EATING' else 'MEDIUM'
            reason = f"ALL 3 BEAR: Alligator {al.state}, below MA200, WR {wr.value:.1f}"
            return SignalResult(-1, conf, reason, ma200_str)

        # ── Partial signals ───────────────────────────────────
        if bull_score == 2:
            reason = f"PARTIAL BULL ({bull_score}/3): {al.direction}/{ma.trend}/{wr.signal}"
            return SignalResult(0, 'LOW', reason, ma200_str)

        if bear_score == 2:
            reason = f"PARTIAL BEAR ({bear_score}/3): {al.direction}/{ma.trend}/{wr.signal}"
            return SignalResult(0, 'LOW', reason, ma200_str)

        reason = f"MIXED: {al.direction}/{ma.trend}/{wr.signal}"
        return SignalResult(0, 'LOW', reason, ma200_str)
