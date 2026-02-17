#!/usr/bin/env python3
"""
Automated paper trading daemon - Runs continuously to update prices and execute trades.

Usage:
    python scripts/auto_paper_trade.py                    # Run once
    python scripts/auto_paper_trade.py --daemon           # Run continuously (every 15 min)
    python scripts/auto_paper_trade.py --daemon --auto-trade  # Auto-execute recommendations
"""

import sys
import os
import time
import argparse
from datetime import datetime
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))
from database import DatabaseManager, PriceSnapshot, MarketOutcome
from analysis.monitor import BotMonitor
from trading.paper_trader import PaperTradingTrader

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def update_paper_positions():
    """Update all paper trading positions with current market prices."""
    logger.info("Updating paper trading positions with current prices...")

    db = DatabaseManager()
    paper_trader = PaperTradingTrader(db)

    with db.get_session() as session:
        # Get all latest prices from the database
        latest_prices = (
            session.query(PriceSnapshot)
            .order_by(PriceSnapshot.timestamp.desc())
            .limit(50)
            .all()
        )

        # Group by outcome_id and get most recent price
        price_updates = {}
        for price_snapshot in latest_prices:
            outcome_id = str(price_snapshot.outcome_id)
            if outcome_id not in price_updates:
                price_updates[outcome_id] = price_snapshot.price

        logger.info(f"Found {len(price_updates)} current market prices")

        # Update position prices (mark-to-market)
        paper_trader.update_position_prices(price_updates)
        logger.info("✓ Position prices updated")

        # Get updated balance
        balance = paper_trader.get_balance()
        logger.info(f"Portfolio Value: ${balance['total_value']:,.2f} "
                   f"(P&L: ${balance['pnl']:+,.2f}, {balance['pnl_pct']:+.1f}%)")

        return balance


def auto_execute_recommendations():
    """Automatically execute trading recommendations."""
    logger.info("Checking for trading opportunities...")

    db = DatabaseManager()
    monitor = BotMonitor(db)
    paper_trader = PaperTradingTrader(db)

    # Get current balance and positions
    balance = paper_trader.get_balance()
    positions_dict = paper_trader.get_positions_dict()

    # Get recommendations
    recommendations = monitor.get_recommendations(
        bankroll=balance['total_value'],  # Use total value, not just cash
        max_kelly=0.25,
        current_positions=positions_dict
    )

    if not recommendations:
        logger.info("No recommendations available")
        return

    # Separate buys and sells
    sells = [r for r in recommendations if r.get('is_sell', False) and r['position_size'] > 0]
    buys = [r for r in recommendations if not r.get('is_sell', False) and r['position_size'] > 0]

    logger.info(f"Found {len(buys)} buy opportunities, {len(sells)} sell opportunities")

    # Execute sells first (take profits!)
    executed_sells = 0
    for rec in sells:
        # Only execute if timing is NOW
        if rec.get('timing') == 'NOW':
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

    if executed_sells > 0:
        logger.info(f"✓ Executed {executed_sells} SELL orders (profit taking)")

    # Execute buys
    executed_buys = 0
    for rec in buys[:10]:  # Limit to top 10
        # Execute floor shares (EARLY timing) and NOW timing
        if rec.get('timing') in ['EARLY', 'NOW']:
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

    if executed_buys > 0:
        logger.info(f"✓ Executed {executed_buys} BUY orders")

    if executed_buys == 0 and executed_sells == 0:
        logger.info("No trades executed (waiting for better timing)")

    # Create snapshot
    prediction = monitor.calculate_prediction()
    if 'error' not in prediction:
        paper_trader.create_snapshot(
            week_progress=prediction.get('week_progress', 0),
            predicted_count=prediction.get('predicted_total', 0),
            actual_count=prediction.get('current_count', 0),
        )

    # Show updated balance
    final_balance = paper_trader.get_balance()
    logger.info(f"Updated Portfolio: ${final_balance['total_value']:,.2f} "
               f"(P&L: ${final_balance['pnl']:+,.2f}, {final_balance['pnl_pct']:+.1f}%)")


def run_daemon(interval_minutes: int = 15, auto_trade: bool = False):
    """
    Run continuously, updating prices and optionally executing trades.

    Args:
        interval_minutes: How often to run (default 15 minutes)
        auto_trade: If True, automatically execute recommendations
    """
    logger.info("="*80)
    logger.info("  AUTOMATED PAPER TRADING DAEMON")
    logger.info("="*80)
    logger.info(f"Mode: {'AUTO-TRADE' if auto_trade else 'PRICE UPDATES ONLY'}")
    logger.info(f"Interval: Every {interval_minutes} minutes")
    logger.info(f"Press Ctrl+C to stop")
    logger.info("="*80)

    iteration = 0

    try:
        while True:
            iteration += 1
            logger.info(f"\n{'='*80}")
            logger.info(f"Iteration #{iteration} - {datetime.utcnow()}")
            logger.info(f"{'='*80}")

            # Always update prices
            update_paper_positions()

            # Optionally execute trades
            if auto_trade:
                auto_execute_recommendations()

            # Wait for next iteration
            logger.info(f"\n⏳ Waiting {interval_minutes} minutes until next update...")
            time.sleep(interval_minutes * 60)

    except KeyboardInterrupt:
        logger.info("\n\n✓ Daemon stopped by user")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Automated paper trading daemon')
    parser.add_argument('--daemon', action='store_true',
                       help='Run continuously (default: run once)')
    parser.add_argument('--auto-trade', action='store_true',
                       help='Automatically execute recommendations (requires --daemon)')
    parser.add_argument('--interval', type=int, default=15,
                       help='Update interval in minutes (default: 15)')

    args = parser.parse_args()

    if args.auto_trade and not args.daemon:
        print("⚠️  --auto-trade requires --daemon flag")
        print("Usage: python auto_paper_trade.py --daemon --auto-trade")
        return

    if args.daemon:
        run_daemon(interval_minutes=args.interval, auto_trade=args.auto_trade)
    else:
        # Run once
        print("Running single update cycle...")
        print()
        update_paper_positions()
        if args.auto_trade:
            print()
            auto_execute_recommendations()
        print()
        print("✓ Update complete")
        print()
        print("💡 To run continuously: python auto_paper_trade.py --daemon")
        print("💡 To auto-trade: python auto_paper_trade.py --daemon --auto-trade")


if __name__ == '__main__':
    main()
