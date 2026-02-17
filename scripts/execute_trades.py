#!/usr/bin/env python3
"""
Execute trading recommendations from the bot.

Usage:
    python scripts/execute_trades.py              # Dry run (no real trades)
    python scripts/execute_trades.py --live       # Execute real trades
    python scripts/execute_trades.py --bankroll 5000 --live  # Custom bankroll
"""

import sys
import os
from datetime import datetime
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))
from database import DatabaseManager
from analysis.monitor import BotMonitor
from trading.polymarket_trader import PolymarketTrader


def print_execution_plan(recommendations: list, bankroll: float):
    """Print what will be executed."""
    meta = recommendations[0].get('_meta', {}) if recommendations else {}
    week_progress = meta.get('week_progress', 0)
    available_capital = meta.get('available_capital', 0)

    # Separate sells, buys (floor shares and core)
    sells = [r for r in recommendations if r.get('is_sell', False) and r['position_size'] > 0]
    floor_shares = [r for r in recommendations if r.get('is_floor_share', False) and not r.get('is_sell', False) and r['position_size'] > 0]
    core_buys = [r for r in recommendations if not r.get('is_floor_share', False) and not r.get('is_sell', False) and r['position_size'] > 0]

    print("\n" + "="*80)
    print("  EXECUTION PLAN")
    print("="*80)
    print()
    print(f"📊 Week Progress: {week_progress}%")
    print(f"💰 Available Capital: ${available_capital:,.2f} of ${bankroll:,.2f}")
    print()

    total_sell_value = sum(r['position_size'] for r in sells)
    total_floor = sum(r['position_size'] for r in floor_shares)
    total_core_buy = sum(r['position_size'] for r in core_buys)
    total_buy = total_floor + total_core_buy

    # Show sells first (profit taking!)
    if sells:
        print(f"💰 PROFIT TAKING - SELLS ({len(sells)} positions, ${total_sell_value:.2f} value)")
        print(f"{'Bracket':<15} {'Action':<15} {'Timing':<10} {'Shares':<10} {'Value':<12} {'Edge %':<10}")
        print("-"*80)
        for rec in sells[:10]:
            action_str = f"{rec['action']}"
            print(f"{rec['bracket']:<15} {action_str:<15} {rec['timing']:<10} "
                  f"{rec['shares']:<9.0f} ${rec['position_size']:<11.2f} {abs(rec['edge']):<9.1f}%")
        print()

    if floor_shares:
        print(f"🎰 FLOOR SHARES - BUYS ({len(floor_shares)} positions, ${total_floor:.2f} total)")
        print(f"{'Bracket':<15} {'Timing':<10} {'Amount':<12} {'Shares':<10} {'ROI':<10}")
        print("-"*80)
        for rec in floor_shares[:10]:
            print(f"{rec['bracket']:<15} {rec['timing']:<10} ${rec['position_size']:<11.2f} "
                  f"{rec['shares']:<9.0f} {rec['roi_if_win']:<9.0f}%")
        print()

    if core_buys:
        print(f"📊 CORE POSITIONS - BUYS ({len(core_buys)} positions, ${total_core_buy:.2f} total)")
        print(f"{'Bracket':<15} {'Action':<15} {'Timing':<10} {'Amount':<12} {'Shares':<10}")
        print("-"*80)
        for rec in core_buys[:10]:
            action_str = f"{rec['action']}"
            print(f"{rec['bracket']:<15} {action_str:<15} {rec['timing']:<10} "
                  f"${rec['position_size']:<11.2f} {rec['shares']:<9.0f}")
        print()

    print("-"*80)
    print(f"{'SELL VALUE':<40} ${total_sell_value:,.2f}")
    print(f"{'BUY COST':<40} ${total_buy:,.2f}")
    print(f"{'NET CAPITAL NEEDED':<40} ${total_buy - total_sell_value:,.2f}")
    print(f"Reserved Capital: ${bankroll - available_capital:,.2f}")
    print("="*80)


def confirm_execution(dry_run: bool) -> bool:
    """Ask user to confirm execution."""
    if dry_run:
        print("\n🔍 DRY RUN MODE - No real trades will be executed")
        return True

    print("\n⚠️  LIVE MODE - Real money will be spent!")
    print("Type 'EXECUTE' to confirm: ", end='')
    response = input().strip()

    return response == 'EXECUTE'


def main():
    """Main execution entry point."""
    parser = argparse.ArgumentParser(description='Execute trading recommendations')
    parser.add_argument('--live', action='store_true', help='Execute real trades (default: dry run)')
    parser.add_argument('--bankroll', type=float, default=1000.0, help='Total bankroll in USD')
    parser.add_argument('--max-total', type=float, help='Maximum total to spend this round')
    parser.add_argument('--floor-only', action='store_true', help='Only execute floor shares')
    parser.add_argument('--core-only', action='store_true', help='Only execute core positions')

    args = parser.parse_args()

    dry_run = not args.live
    bankroll = args.bankroll
    max_total = args.max_total or bankroll * 0.5  # Default to 50% max

    print("="*80)
    print("  POLYMARKET TRADING BOT - Execution Module")
    print("="*80)
    print()
    print(f"Mode: {'🔍 DRY RUN' if dry_run else '⚠️  LIVE TRADING'}")
    print(f"Bankroll: ${bankroll:,.2f}")
    print(f"Max Total: ${max_total:,.2f}")
    print()

    # Initialize
    db = DatabaseManager()
    monitor = BotMonitor(db)

    # Initialize trader to get positions
    print("🔌 Connecting to Polymarket...")
    try:
        trader = PolymarketTrader(
            max_position_size=float(os.getenv('MAX_POSITION_SIZE_USD', 100)),
            max_total_exposure=float(os.getenv('MAX_TOTAL_EXPOSURE_USD', 500)),
        )
        print("✓ Connected to Polymarket")
    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        return

    # Get current positions
    print("📊 Fetching current positions...")
    positions = trader.get_open_positions()
    position_dict = {str(p.get('asset_id', p.get('token_id'))): p.get('size', 0) for p in positions}
    if position_dict:
        print(f"✓ Found {len(position_dict)} open positions")
    else:
        print("⚠️  No open positions (or unable to fetch)")

    # Get recommendations with current positions for sell opportunities
    print("📊 Calculating recommendations...")
    recommendations = monitor.get_recommendations(
        bankroll=bankroll,
        max_kelly=0.25,
        current_positions=position_dict
    )

    if not recommendations:
        print("✗ No recommendations available")
        return

    # Filter based on flags
    if args.floor_only:
        recommendations = [r for r in recommendations if r.get('is_floor_share', False)]
        print("🎰 Floor shares only mode")
    elif args.core_only:
        recommendations = [r for r in recommendations if not r.get('is_floor_share', False)]
        print("📊 Core positions only mode")

    # Filter to only positions with size > 0
    recommendations = [r for r in recommendations if r.get('position_size', 0) > 0]

    # Print execution plan
    print_execution_plan(recommendations, bankroll)

    # Ask for confirmation
    if not confirm_execution(dry_run):
        print("\n❌ Execution cancelled by user")
        return

    if not dry_run:
        # Show balance before executing
        balance = trader.get_balance()
        print(f"\n💵 USDC Balance: ${balance['usdc_balance']:.2f}")
        print()

    # Execute recommendations
    print(f"\n{'🔍 SIMULATING' if dry_run else '⚡ EXECUTING'} trades...")
    print()

    results = trader.execute_recommendations(
        recommendations=recommendations,
        dry_run=dry_run,
        max_total=max_total,
    )

    # Print results
    print("\n" + "="*80)
    print("  EXECUTION RESULTS")
    print("="*80)
    print()
    print(f"Total Orders: {results['total_orders']}")
    print(f"Successful: {results['successful']} ✓")
    print(f"Failed: {results['failed']} ✗")
    print(f"Total Spent: ${results['total_spent']:.2f}")
    print()

    if results['failed'] > 0:
        print("Failed orders:")
        for result in results['results']:
            if not result['success']:
                print(f"  ✗ {result['bracket']}: {result.get('error', 'Unknown error')}")

    print("="*80)

    if dry_run:
        print("\n💡 To execute real trades, run: python execute_trades.py --live")
    else:
        print("\n✓ Execution complete!")
        print(f"📝 Log timestamp: {datetime.utcnow()}")


if __name__ == '__main__':
    main()
