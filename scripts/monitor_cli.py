#!/usr/bin/env python3
"""
Monitor script - Shows bot status, market curve, and recommendations.

Usage:
    python scripts/monitor_cli.py           # Show full dashboard
    python scripts/monitor_cli.py status    # Just status
    python scripts/monitor_cli.py curve     # Just bell curve
    python scripts/monitor_cli.py recs      # Just recommendations
"""

import sys
import os
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))
from database import DatabaseManager
from analysis.monitor import BotMonitor


def print_status(monitor: BotMonitor):
    """Print bot status."""
    status = monitor.check_status()

    print("\n" + "="*70)
    print("  BOT STATUS")
    print("="*70)

    if status['working']:
        print("✓ Status: WORKING")
    else:
        print("✗ Status: NOT WORKING")

    print()

    # Tweet data
    if status['tweet_data']:
        td = status['tweet_data']
        print(f"📊 Tweet Data:")
        print(f"   Current Count: {td['count']} tweets")
        print(f"   Last Update: {td['last_update']} ({td['minutes_ago']}m ago)")
        if td['velocity']:
            print(f"   Velocity: {td['velocity']:.1f} tweets/hour")
    else:
        print("📊 Tweet Data: NO DATA")

    print()

    # Market data
    if status['market_data']:
        md = status['market_data']
        print(f"📈 Market:")
        print(f"   {md['title']}")
        print(f"   Period: {md['week_start']} to {md['week_end']}")
    else:
        print("📈 Market: NO DATA")

    print()

    # Price data
    if status['price_data']:
        pd = status['price_data']
        print(f"💰 Prices:")
        print(f"   Last Update: {pd['last_update']} ({pd['minutes_ago']}m ago)")
    else:
        print("💰 Prices: NO DATA")

    # Warnings
    if status['warnings']:
        print()
        print("⚠️  Warnings:")
        for warning in status['warnings']:
            print(f"   - {warning}")

    print("="*70)


def print_bell_curve(monitor: BotMonitor):
    """Print market probability distribution."""
    curve = monitor.get_market_bell_curve()

    if not curve:
        print("\n✗ No market data available")
        return

    print("\n" + "="*70)
    print("  MARKET BELL CURVE (Implied Probabilities)")
    print("="*70)
    print()
    print(f"{'Bracket':<15} {'Price':>10} {'Prob %':>10} {'Visual':>30}")
    print("-"*70)

    for item in curve:
        bracket = item['bracket']
        price = item['price']
        prob = item['implied_prob']

        # Visual bar
        bar_length = int(prob * 30 / max(i['implied_prob'] for i in curve))
        bar = '█' * bar_length

        print(f"{bracket:<15} ${price:>9.4f} {prob:>9.1f}% {bar:>30}")

    print("="*70)


def print_prediction(monitor: BotMonitor):
    """Print bot prediction."""
    pred = monitor.calculate_prediction()

    if 'error' in pred:
        print(f"\n✗ {pred['error']}")
        return

    print("\n" + "="*70)
    print("  BOT PREDICTION (Normal Distribution Model)")
    print("="*70)
    print()
    print(f"Week Progress: {pred['week_progress']}% ({pred['elapsed_days']:.1f} days elapsed)")
    print(f"Current Count: {pred['current_count']} tweets this week")
    velocity = pred.get('velocity', {})
    v1 = velocity.get('velocity_1h')
    v6 = velocity.get('velocity_6h')
    v24 = velocity.get('velocity_24h')
    accel = velocity.get('acceleration')
    print(f"Velocity (1h/6h/24h): "
          f"{(v1 or 0):.1f} / {(v6 or 0):.1f} / {(v24 or 0):.1f} tweets/hr")
    if accel is not None:
        print(f"Acceleration: {accel:+.2f} tweets/hr²")
    print(f"Daily Velocity: {pred['tweets_per_day']:.1f} tweets/day")
    print(f"Hourly Avg: {pred['tweets_per_hour']:.1f} tweets/hour")
    print(f"Days Remaining: {pred['days_remaining']:.1f} days")
    print()
    print(f"📊 PREDICTED TOTAL: {pred['predicted_total']} tweets")
    print(f"   Confidence Range: {pred['confidence_lower']} - {pred['confidence_upper']}")
    print(f"   Confidence: ±{pred['confidence_pct']:.0f}% (narrows as week progresses)")
    print(f"   μ = {pred['mu']}  σ = {pred['sigma']}")
    print(f"   Week Ends: {pred['week_end']}")
    print("="*70)


def print_recommendations(monitor: BotMonitor, bankroll: float = 1000.0):
    """Print trading recommendations with time-based capital deployment strategy."""
    recs = monitor.get_recommendations(bankroll=bankroll, max_kelly=0.25)

    if not recs:
        print("\n✗ No recommendations available")
        return

    # Get deployment metadata from first recommendation
    meta = recs[0].get('_meta', {})
    week_progress = meta.get('week_progress', 0)
    capital_deployed_pct = meta.get('capital_deployed_pct', 0)
    available_capital = meta.get('available_capital', 0)
    reserved_capital = meta.get('reserved_capital', 0)
    floor_share_count = meta.get('floor_share_count', 0)

    print("\n" + "="*110)
    print(f"  TRADING RECOMMENDATIONS - Time-Based Strategy (Week Progress: {week_progress}%)")
    print("="*110)
    print()
    print(f"💰 Capital Deployment:")
    print(f"   Available Now: ${available_capital:,.2f} ({capital_deployed_pct:.1f}% of ${bankroll:,.0f})")
    print(f"   Reserved: ${reserved_capital:,.2f} (deploys as week progresses)")
    print()

    # Separate floor shares from core positions
    floor_shares = [r for r in recs if r.get('is_floor_share', False)]
    core_positions = [r for r in recs if not r.get('is_floor_share', False)]

    # Print floor shares first (lottery tickets)
    if floor_shares:
        print("🎰 FLOOR SHARES (Lottery Tickets - Early Week Priority)")
        print(f"{'Bracket':<12} {'Timing':<8} {'Size $':>10} {'Shares':>8} {'Edge %':>8} {'ROI':>10}")
        print("-"*110)
        for rec in floor_shares[:10]:  # Top 10 floor shares
            print(f"{rec['bracket']:<12} {rec['timing']:<8} ${rec['position_size']:>9.2f} "
                  f"{rec['shares']:>7.0f} {rec['edge']:>7.1f}% {rec['roi_if_win']:>9.0f}%")
        print()

    # Print core positions
    if core_positions:
        print("📊 CORE POSITIONS (Kelly-Sized Based on Edge)")
        print(f"{'Bracket':<12} {'Action':<13} {'Timing':<8} {'Kelly %':>8} {'Size $':>10} "
              f"{'Shares':>8} {'Edge %':>8}")
        print("-"*110)

        for rec in core_positions[:15]:  # Top 15 core positions
            action = rec['action']

            # Color code actions
            if action == 'STRONG BUY':
                action_str = f"🟢 {action}"
            elif action == 'BUY':
                action_str = f"🟡 {action}"
            elif action == 'SMALL':
                action_str = f"🔵 {action}"
            else:
                action_str = f"⚪ {action}"

            print(f"{rec['bracket']:<12} {action_str:<13} {rec['timing']:<8} {rec['kelly_pct']:>7.1f}% "
                  f"${rec['position_size']:>9.2f} {rec['shares']:>7.0f} {rec['edge']:>7.1f}%")

    # Calculate total recommended allocation
    total_position = sum(r['position_size'] for r in recs if r['position_size'] > 0)
    floor_total = sum(r['position_size'] for r in floor_shares if r['position_size'] > 0)
    core_total = sum(r['position_size'] for r in core_positions if r['position_size'] > 0)

    print("-"*110)
    print(f"{'TOTAL ALLOCATED':<34} ${total_position:>9.2f} "
          f"(Floor: ${floor_total:.2f} + Core: ${core_total:.2f})")
    print()
    print("Timing Guide:")
    print("  EARLY: Deploy now (early week opportunities)")
    print("  NOW: Deploy immediately (high confidence)")
    print("  WAIT: Hold capital, wait for more data")
    print("  LATER: Deploy in late week when confidence increases")
    print()
    print("Action Legend:")
    print("  🎰 FLOOR: Lottery ticket (<$0.01, 50x+ upside)")
    print("  🟢 STRONG BUY: Kelly ≥ 20% of max - Very high edge")
    print("  🟡 BUY: Kelly ≥ 7.5% of max - Good edge")
    print("  🔵 SMALL: Kelly ≥ 2.5% of max - Small edge")
    print()
    print("Strategy Notes:")
    print(f"  - Early week ({week_progress:.0f}%): Focus on floor shares, reserve capital for later")
    print(f"  - Capital deployment scales from 20% (start) → 90% (end of week)")
    print(f"  - Floor shares: Fixed ~$10 per bracket for 50x-100x asymmetric upside")
    print(f"  - Core positions: Kelly-sized, scaled by available capital")
    print("="*110)


def main():
    """Main entry point."""
    db = DatabaseManager()
    monitor = BotMonitor(db)

    # Get bankroll from command line or use default
    bankroll = 1000.0  # Default $1000
    if len(sys.argv) > 2:
        try:
            bankroll = float(sys.argv[2])
        except ValueError:
            pass

    if len(sys.argv) > 1:
        command = sys.argv[1].lower()

        if command == 'status':
            print_status(monitor)
        elif command == 'curve':
            print_bell_curve(monitor)
        elif command == 'pred' or command == 'prediction':
            print_prediction(monitor)
        elif command == 'recs' or command == 'recommendations':
            print_prediction(monitor)
            print_recommendations(monitor, bankroll)
        else:
            print(f"Unknown command: {command}")
            print("Usage: python monitor.py [status|curve|pred|recs] [bankroll]")
            print("Example: python monitor.py recs 5000")
    else:
        # Show everything
        print_status(monitor)
        print()
        print_prediction(monitor)
        print()
        print_bell_curve(monitor)
        print()
        print_recommendations(monitor, bankroll)


if __name__ == '__main__':
    main()
