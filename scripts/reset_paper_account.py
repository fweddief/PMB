#!/usr/bin/env python3
"""
Reset paper trading account for a new market week.
Archives previous week's performance.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))

from database import DatabaseManager
from trading.paper_trader import PaperTradingTrader

def main():
    print("🔄 RESETTING PAPER TRADING ACCOUNT")
    print("=" * 80)

    db = DatabaseManager()
    paper_trader = PaperTradingTrader(db)

    # Get current performance before reset
    balance = paper_trader.get_balance()

    print(f"\n📊 Current Week Performance:")
    print(f"   Starting Balance: $1,000.00")
    print(f"   Final Balance: ${balance['total_value']:.2f}")
    print(f"   P&L: ${balance['pnl']:+.2f} ({balance['pnl_pct']:+.1f}%)")
    print(f"   Cash: ${balance['cash']:.2f}")

    # Ask for confirmation
    print("\n⚠️  This will:")
    print("   1. Archive current week's performance")
    print("   2. Close all open positions")
    print("   3. Reset balance to $1,000")
    print("   4. Clear position history")

    response = input("\nContinue? (yes/no): ")

    if response.lower() not in ['yes', 'y']:
        print("❌ Reset cancelled")
        return

    # Reset account
    result = paper_trader.reset_for_new_week(archive=True)

    if result.get('success'):
        print("\n✅ RESET COMPLETE")
        print("=" * 80)
        print(f"   Positions closed: {result['positions_closed']}")
        print(f"   New balance: ${result['new_balance']:.2f}")

        if result.get('archived_performance'):
            archived = result['archived_performance']
            print(f"\n📦 Archived Performance:")
            print(f"   P&L: ${archived['pnl']:+.2f} ({archived['pnl_pct']:+.1f}%)")
            print(f"   Total trades: {archived['total_trades']}")

        print("\n✓ Ready for next week's market!")
        print("  Run: python main.py schedule --auto-trade")
    else:
        print(f"\n❌ Reset failed: {result.get('error')}")

if __name__ == '__main__':
    main()
