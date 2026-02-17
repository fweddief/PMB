#!/usr/bin/env python3
"""
Check paper trading performance.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))

from database import DatabaseManager
from trading.paper_trader import PaperTradingTrader

def main():
    print("📊 PAPER TRADING PERFORMANCE")
    print("=" * 80)

    db = DatabaseManager()
    paper_trader = PaperTradingTrader(db)

    # Get balance
    balance = paper_trader.get_balance()
    print(f"\n💰 Current Balance:")
    print(f"   Cash: ${balance['cash']:.2f}")
    print(f"   Position Value: ${balance['position_value']:.2f}")
    print(f"   Total Value: ${balance['total_value']:.2f}")
    print(f"   P&L: ${balance['pnl']:+.2f} ({balance['pnl_pct']:+.1f}%)")

    # Get positions
    positions = paper_trader.get_open_positions()
    if positions:
        print(f"\n📈 Open Positions ({len(positions)}):")
        for pos in positions:
            print(f"   {pos['bracket']}: {pos['shares']:.0f} shares @ ${pos['average_cost']:.4f}")
            print(f"      Current: ${pos['current_price']:.4f}, Value: ${pos['current_value']:.2f}")
            print(f"      Unrealized P&L: ${pos['unrealized_pnl']:+.2f}")
    else:
        print(f"\n📈 No open positions")

    # Get full performance summary
    summary = paper_trader.get_performance_summary()
    print(f"\n📉 Trade Statistics:")
    print(f"   Total Trades: {summary['num_trades']}")
    print(f"   Buys: {summary['num_buys']}, Sells: {summary['num_sells']}")
    if summary['num_sells'] > 0:
        print(f"   Wins: {summary['num_wins']}, Losses: {summary['num_losses']}")
        print(f"   Win Rate: {summary['win_rate']:.1f}%")
        print(f"   Realized P&L: ${summary['realized_pnl']:+.2f}")
        print(f"   Unrealized P&L: ${summary['unrealized_pnl']:+.2f}")
        if summary['num_wins'] > 0:
            print(f"   Avg Win: ${summary['avg_win']:+.2f}")
        if summary['num_losses'] > 0:
            print(f"   Avg Loss: ${summary['avg_loss']:+.2f}")

if __name__ == '__main__':
    main()
