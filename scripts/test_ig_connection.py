"""
Quick test: connect to IG, fetch prices for one instrument,
verify the data format matches what the bot expects.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from bot.brokers.ig import IGBroker


class MockConfig:
    def __init__(self):
        self._settings = {
            "broker": "ig",
            "ig_acc_type": os.environ.get("IG_ACC_TYPE", "demo"),
        }
        self.active_instruments = []
        self.accum_instruments = []
        self.portfolio_loss_limit = 10000

    def get(self, key, default=None):
        return self._settings.get(key, default)


def test_connection():
    print("=" * 50)
    print("IG Markets Connection Test")
    print("=" * 50)

    cfg = MockConfig()
    broker = IGBroker(cfg)

    print("\n1. Connecting to IG...")
    broker.connect()
    connected = broker.is_connected()
    print(f"   Connected: {connected}")
    assert connected, "Failed to connect to IG"

    # Use FTSE index (always available on demo) to verify bar fetching
    index_epic = "IX.D.FTSE.DAILY.IP"
    print(f"\n2. Fetching 4h bars for FTSE 100 ({index_epic})...")
    df = broker.fetch_bars(index_epic, days=30, bar_size="4 hours")
    assert df is not None and not df.empty, "No bars returned for FTSE index"
    print(f"   Got {len(df)} bars")
    print(f"   Columns: {list(df.columns)}")
    required_cols = {"open", "high", "low", "close"}
    assert required_cols.issubset(set(df.columns)), \
        f"Missing columns: {required_cols - set(df.columns)}"
    print(f"   Last bar: {df.iloc[-1].to_dict()}")

    # Try an equity epic (may fail on demo — that's OK)
    equity_epic = "KA.D.BARC.CASH.IP"
    print(f"\n3. Fetching 4h bars for BARC ({equity_epic})...")
    df2 = broker.fetch_bars(equity_epic, days=30, bar_size="4 hours")
    if df2 is not None and not df2.empty:
        print(f"   Got {len(df2)} bars")
    else:
        print("   No data (demo accounts often restrict equity history)")
        print("   This is expected — live account will have full access")

    print(f"\n4. Fetching price snapshot for FTSE...")
    price = broker.fetch_price_snapshot(index_epic)
    print(f"   Price: {price}")

    print(f"\n5. Checking open positions...")
    positions = broker.get_all_positions()
    print(f"   Open positions: {len(positions)}")
    for p in positions:
        print(f"     {p.symbol}: {p.qty} @ {p.avg_cost} {p.currency}")

    print(f"\n6. Total P&L: {broker.get_total_pnl()}")

    broker.disconnect()
    print("\n" + "=" * 50)
    print("IG connection test PASSED!")
    print("=" * 50)


if __name__ == "__main__":
    test_connection()
