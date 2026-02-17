#!/usr/bin/env python3
"""
Paper trading execution - Test strategies risk-free with simulated money.

Usage:
    python scripts/paper_trade_cli.py                  # Execute recommendations
    python scripts/paper_trade_cli.py --performance    # Show performance dashboard
    python scripts/paper_trade_cli.py --reset          # Reset paper account to $1000
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))
from database import DatabaseManager
from analysis.monitor import BotMonitor
from trading.paper_trader import PaperTradingTrader


def execute_paper_trades(bankroll: float = 1000.0):
    """Execute current recommendations in paper trading account."""
    print("="*80)
    print("  PAPER TRADING - SIMULATION MODE")
    print("="*80)
    print()

    # Initialize
    db = DatabaseManager()
    monitor = BotMonitor(db)
    paper_trader = PaperTradingTrader(db, starting_balance=bankroll)

    # Get current balance
    balance = paper_trader.get_balance()
    print(f"💰 Paper Account Balance:")
    print(f"   Cash: ${balance['cash']:,.2f}")
    print(f"   Positions: ${balance['position_value']:,.2f}")
    print(f"   Total Value: ${balance['total_value']:,.2f}")
    print(f"   P&L: ${balance['pnl']:+,.2f} ({balance['pnl_pct']:+.1f}%)")
    print()

    # Get current positions for sell recommendations
    positions_dict = paper_trader.get_positions_dict()
    print(f"📊 Current Positions: {len(positions_dict)}")

    # Get recommendations
    print("📊 Calculating recommendations...")
    recommendations = monitor.get_recommendations(
        bankroll=balance['cash'],
        max_kelly=0.25,
        current_positions=positions_dict
    )

    if not recommendations:
        print("✗ No recommendations available")
        return

    # Separate buys and sells
    sells = [r for r in recommendations if r.get('is_sell', False) and r['position_size'] > 0]
    buys = [r for r in recommendations if not r.get('is_sell', False) and r['position_size'] > 0]

    print()
    print(f"📈 Recommendations: {len(buys)} buys, {len(sells)} sells")
    print()

    # Execute sells first (free up capital)
    executed_sells = 0
    sell_value = 0

    if sells:
        print("💰 EXECUTING SELLS (Taking Profits):")
        print("-"*80)
        for rec in sells:
            success = paper_trader.execute_sell(
                outcome_id=rec['outcome_id'],
                bracket=rec['bracket'],
                shares=rec['shares'],
                price=rec['market_price'],
                action=rec['action'],
                our_prob=rec['our_prob'] / 100,
                market_prob=rec['market_prob'] / 100,
                edge=rec['edge'] / 100,
                week_progress=rec.get('_meta', {}).get('week_progress'),
            )
            if success:
                executed_sells += 1
                sell_value += rec['position_size']
        print()

    # Execute buys
    executed_buys = 0
    buy_cost = 0

    if buys:
        print("🛒 EXECUTING BUYS:")
        print("-"*80)
        for rec in buys[:20]:  # Limit to top 20
            success = paper_trader.execute_buy(
                outcome_id=rec['outcome_id'],
                bracket=rec['bracket'],
                shares=rec['shares'],
                price=rec['market_price'],
                action=rec['action'],
                our_prob=rec['our_prob'] / 100,
                market_prob=rec['market_prob'] / 100,
                edge=rec['edge'] / 100,
                week_progress=rec.get('_meta', {}).get('week_progress'),
            )
            if success:
                executed_buys += 1
                buy_cost += rec['position_size']
        print()

    # Get prediction for snapshot
    prediction = monitor.calculate_prediction()
    week_progress = prediction.get('week_progress', 0)
    predicted_total = prediction.get('predicted_total', 0)
    current_count = prediction.get('current_count', 0)

    # Update prices and create snapshot
    print("📸 Creating portfolio snapshot...")
    # In real implementation, fetch current prices from market
    # For now, we'll just create snapshot with current state
    paper_trader.create_snapshot(
        week_progress=week_progress,
        predicted_count=predicted_total,
        actual_count=current_count,
    )

    # Print summary
    final_balance = paper_trader.get_balance()
    print()
    print("="*80)
    print("  EXECUTION SUMMARY")
    print("="*80)
    print(f"Sells Executed: {executed_sells} (${sell_value:,.2f} proceeds)")
    print(f"Buys Executed: {executed_buys} (${buy_cost:,.2f} cost)")
    print()
    print(f"💰 Updated Balance:")
    print(f"   Cash: ${final_balance['cash']:,.2f}")
    print(f"   Positions: ${final_balance['position_value']:,.2f}")
    print(f"   Total Value: ${final_balance['total_value']:,.2f}")
    print(f"   P&L: ${final_balance['pnl']:+,.2f} ({final_balance['pnl_pct']:+.1f}%)")
    print("="*80)


def show_performance():
    """Show detailed performance dashboard."""
    db = DatabaseManager()
    paper_trader = PaperTradingTrader(db)

    perf = paper_trader.get_performance_summary()

    print("\n" + "="*80)
    print("  PAPER TRADING PERFORMANCE DASHBOARD")
    print("="*80)
    print()
    print(f"📊 Account: {perf['account_name']}")
    print(f"   Starting Balance: ${perf['starting_balance']:,.2f}")
    print(f"   Current Value: ${perf['total_value']:,.2f}")
    print(f"   Total P&L: ${perf['total_pnl']:+,.2f} ({perf['pnl_pct']:+.1f}%)")
    print()
    print(f"💵 Breakdown:")
    print(f"   Cash: ${perf['current_cash']:,.2f}")
    print(f"   Position Value: ${perf['position_value']:,.2f}")
    print(f"   Realized P&L: ${perf['realized_pnl']:+,.2f}")
    print(f"   Unrealized P&L: ${perf['unrealized_pnl']:+,.2f}")
    print()
    print(f"📈 Trading Stats:")
    print(f"   Total Trades: {perf['num_trades']}")
    print(f"   Buys: {perf['num_buys']}")
    print(f"   Sells: {perf['num_sells']}")
    print(f"   Open Positions: {perf['num_positions']}")
    print()
    print(f"🎯 Performance:")
    print(f"   Win Rate: {perf['win_rate']:.1f}%")
    print(f"   Wins: {perf['num_wins']}")
    print(f"   Losses: {perf['num_losses']}")
    print(f"   Avg Win: ${perf['avg_win']:+,.2f}")
    print(f"   Avg Loss: ${perf['avg_loss']:+,.2f}")
    print()

    # Show open positions
    positions = paper_trader.get_open_positions()
    if positions:
        print(f"📊 OPEN POSITIONS ({len(positions)}):")
        print(f"{'Bracket':<15} {'Shares':<10} {'Avg Cost':<12} {'Current':<12} {'P&L':<12}")
        print("-"*80)
        for pos in positions:
            print(f"{pos['bracket']:<15} {pos['shares']:<9.0f} "
                  f"${pos['average_cost']:<11.4f} ${pos['current_price']:<11.4f} "
                  f"${pos['unrealized_pnl']:+,.2f}")
    else:
        print("📊 No open positions")

    print("="*80)


def reset_account(bankroll: float = 1000.0):
    """Reset paper trading account."""
    print("⚠️  WARNING: This will delete all paper trading history!")
    print(f"Reset to ${bankroll:,.2f}? Type 'YES' to confirm: ", end='')
    response = input().strip()

    if response != 'YES':
        print("❌ Reset cancelled")
        return

    db = DatabaseManager()

    # Drop and recreate paper trading tables
    from database.paper_trading_schema import Base
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine)

    # Create new account
    paper_trader = PaperTradingTrader(db, starting_balance=bankroll)

    print(f"✓ Paper trading account reset to ${bankroll:,.2f}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Paper trading execution and monitoring')
    parser.add_argument('--performance', action='store_true', help='Show performance dashboard')
    parser.add_argument('--reset', action='store_true', help='Reset paper trading account')
    parser.add_argument('--bankroll', type=float, default=1000.0, help='Starting bankroll (for reset)')

    args = parser.parse_args()

    if args.reset:
        reset_account(args.bankroll)
    elif args.performance:
        show_performance()
    else:
        execute_paper_trades(args.bankroll)


if __name__ == '__main__':
    main()
