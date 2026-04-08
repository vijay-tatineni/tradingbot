"""
Search for IG market epics for your instruments.

Usage:
    python3 scripts/ig_search_epics.py

Set env vars first:
    export IG_USERNAME="your_username"
    export IG_PASSWORD="your_password"
    export IG_API_KEY="your_api_key"
    export IG_ACC_TYPE="DEMO"
"""

import os
import sys

from trading_ig import IGService


def main():
    for var in ("IG_USERNAME", "IG_PASSWORD", "IG_API_KEY"):
        if not os.environ.get(var):
            print(f"ERROR: {var} not set. Export IG credentials first.")
            sys.exit(1)

    ig = IGService(
        os.environ["IG_USERNAME"],
        os.environ["IG_PASSWORD"],
        os.environ["IG_API_KEY"],
        os.environ.get("IG_ACC_TYPE", "DEMO"),
    )
    ig.create_session()
    print("Connected to IG\n")

    search_terms = [
        "Barclays", "Shell", "Microsoft", "Apple", "Alphabet",
        "Palantir", "Gold", "Silver", "EURUSD", "FTSE 100",
        "Micron", "Broadcom", "Nvidia", "Crowdstrike",
    ]

    for term in search_terms:
        print(f"--- Searching: {term} ---")
        try:
            results = ig.search_markets(term)
            if results is not None and not results.empty:
                for _, row in results.head(5).iterrows():
                    print(f"  Epic: {row.get('epic', '?'):40s} "
                          f"Name: {row.get('instrumentName', '?')}")
            else:
                print("  No results")
        except Exception as e:
            print(f"  Error: {e}")
        print()

    ig.logout()
    print("Done.")


if __name__ == "__main__":
    main()
