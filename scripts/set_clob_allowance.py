#!/usr/bin/env python3
"""
Set CLOB API allowance for trading.

This is DIFFERENT from on-chain contract approvals.
This sets the allowance in Polymarket's backend system.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import logging

from trading.polymarket_trader import PolymarketTrader
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
from database import DatabaseManager, MarketOutcome

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    print("=" * 60)
    print("Setting CLOB API Allowance")
    print("=" * 60)

    # Initialize trader
    print("\nInitializing trader...")
    trader = PolymarketTrader()

    # Check balance
    print("\nChecking current balance and allowance...")
    balance = trader.get_balance()
    print(f"  Cash: ${balance['cash']:.2f}")
    print(f"  Allowance: ${balance['allowance']:.2f}")

    # Set allowance
    print("\nSetting CLOB allowance to $1,000,000...")
    success = trader.ensure_allowance(min_allowance=1000.0)

    if success:
        # Check again
        print("\nRechecking balance and allowance...")
        balance = trader.get_balance()
        print(f"  Cash: ${balance['cash']:.2f}")
        print(f"  Allowance: ${balance['allowance']:.2f}")
        print("\n✅ Allowance set successfully!")
    else:
        print("\n❌ Failed to set allowance")

    # Optional conditional token allowances
    resp = input("\nSet allowances for all outcome tokens? (y/N): ").strip().lower()
    if resp == 'y':
        set_conditional_token_allowances(trader)

def set_conditional_token_allowances(trader: PolymarketTrader):
    print("\n============================================================")
    print("Setting allowances for conditional outcome tokens")
    print("============================================================")

    db = DatabaseManager()
    with db.get_session() as session:
        outcome_ids = [
            str(outcome_id)
            for (outcome_id,) in session.query(MarketOutcome.outcome_id).distinct().all()
            if outcome_id
        ]

    total = len(outcome_ids)
    if not outcome_ids:
        print("No outcome IDs found in database.")
        return

    for idx, outcome_id in enumerate(outcome_ids, start=1):
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=outcome_id,
            signature_type=trader.signature_type,
        )
        try:
            trader.client.update_balance_allowance(params)
            print(f"[{idx}/{total}] ✓ Allowance set for outcome {outcome_id}")
        except Exception as exc:
            print(f"[{idx}/{total}] ⚠️ Failed for {outcome_id}: {exc}")

    print("\n✅ Conditional token allowances configured")

if __name__ == "__main__":
    main()
