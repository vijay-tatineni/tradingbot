"""
backtest — Walk-forward testing framework for CogniflowAI Trading Bot.

Imports the bot's exact indicator and signal logic, runs it offline
against historical OHLCV data, and grid-searches optimal parameters
across rolling train/test windows.

Usage:
    python3 -m backtest.run --profile paper
    python3 -m backtest.run --profile paper --symbol BARC --skip-download
"""
