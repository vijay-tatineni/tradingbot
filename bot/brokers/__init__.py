"""
bot/brokers — Broker abstraction layer.

Provides a swappable adapter pattern so the bot can support
multiple brokers (IBKR today, MetaApi/Vantage later).
"""

from bot.brokers.base import BaseBroker, BrokerPosition, FillResult, PositionInfo


def create_broker(broker_type: str, cfg) -> BaseBroker:
    """Factory function to create the right broker adapter."""
    if broker_type == "ibkr":
        from bot.brokers.ibkr import IBKRBroker
        return IBKRBroker(cfg)
    elif broker_type == "ig":
        from bot.brokers.ig import IGBroker
        return IGBroker(cfg)
    else:
        raise ValueError(f"Unknown broker type: {broker_type}")
