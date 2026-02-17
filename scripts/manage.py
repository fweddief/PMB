#!/usr/bin/env python3
"""
Main entry point for Polymarket trading bot.

Usage:
    python scripts/manage.py init           # Initialize database
    python scripts/manage.py collect        # Run one-time data collection
    python scripts/manage.py schedule       # Start scheduled data collection
    python scripts/manage.py test-scraper   # Test xtracker scraper
"""

import sys
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))

from database import DatabaseManager
from collectors import DataCollector
from scrapers import XTrackerScraper, PolymarketScraper


# Load environment
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def init_database():
    """Initialize the database."""
    logger.info("Initializing database...")
    db = DatabaseManager()
    db.create_tables()

    # Run migrations
    logger.info("Running database migrations...")
    _run_migrations(db)

    logger.info("✓ Database initialized successfully")


def _run_migrations(db: DatabaseManager):
    """Run database migrations to add missing columns."""
    from sqlalchemy import text

    with db.get_session() as session:
        try:
            # Check and add velocity_5m column
            result = session.execute(text("""
                SELECT COUNT(*)
                FROM pragma_table_info('tweet_data')
                WHERE name = 'velocity_5m'
            """))
            has_5m = result.scalar() > 0

            # Check and add velocity_20m column
            result = session.execute(text("""
                SELECT COUNT(*)
                FROM pragma_table_info('tweet_data')
                WHERE name = 'velocity_20m'
            """))
            has_20m = result.scalar() > 0

            if not has_5m:
                logger.info("  Adding velocity_5m column...")
                session.execute(text("ALTER TABLE tweet_data ADD COLUMN velocity_5m FLOAT"))
                logger.info("  ✓ Added velocity_5m column")

            if not has_20m:
                logger.info("  Adding velocity_20m column...")
                session.execute(text("ALTER TABLE tweet_data ADD COLUMN velocity_20m FLOAT"))
                logger.info("  ✓ Added velocity_20m column")

            if has_5m and has_20m:
                logger.info("  ✓ All velocity columns exist")

            session.commit()

        except Exception as e:
            logger.error(f"  ✗ Migration failed: {e}")
            session.rollback()
            raise

    # Fix token IDs for trading (update existing outcomes with correct CLOB token IDs)
    try:
        logger.info("Updating token IDs for trading...")
        _fix_token_ids(db)
    except Exception as e:
        logger.warning(f"Token ID migration had issues (non-critical): {e}")
        # Continue anyway - token IDs will be correct for newly created markets


def _fix_token_ids(db: DatabaseManager):
    """Update market outcomes with correct CLOB token IDs for trading."""
    from database import PolymarketMarket, MarketOutcome

    with db.get_session() as session:
        try:
            # Check if we need to update token IDs by sampling a few outcomes
            # Token IDs from CLOB are very long (>60 chars), market IDs are short (<10 chars)
            sample_outcomes = session.query(MarketOutcome).limit(10).all()

            if not sample_outcomes:
                logger.info("  ✓ No outcomes to check")
                return

            # Check if any outcome has a short token ID
            needs_update = any(len(str(outcome.outcome_id)) < 20 for outcome in sample_outcomes)

            if not needs_update:
                logger.info("  ✓ Token IDs already updated")
                return

            logger.info("  Updating token IDs from Polymarket API...")

            scraper = PolymarketScraper()

            # Get all markets and update their outcomes
            all_markets = session.query(PolymarketMarket).all()
            market_ids = set(market.market_id for market in all_markets)

            for market_id in market_ids:
                try:
                    market_data = scraper.get_market_by_id(str(market_id))
                    if not market_data:
                        continue

                    outcomes_data = market_data.get('markets', [])

                    for outcome_data in outcomes_data:
                        outcome_text = outcome_data.get('groupItemTitle', '')

                        # Get CLOB token ID
                        clob_token_ids_str = outcome_data.get('clobTokenIds', '[]')
                        try:
                            clob_token_ids = eval(clob_token_ids_str)
                            token_id = clob_token_ids[0] if clob_token_ids else outcome_data.get('id')
                        except:
                            token_id = outcome_data.get('id')

                        # Update outcome
                        outcome = session.query(MarketOutcome).join(PolymarketMarket).filter(
                            and_(
                                PolymarketMarket.market_id == market_id,
                                MarketOutcome.outcome_text == outcome_text
                            )
                        ).first()

                        if outcome and len(str(token_id)) > 20:
                            outcome.outcome_id = str(token_id)

                except Exception as e:
                    logger.warning(f"  Could not update market {market_id}: {e}")
                    continue

            session.commit()
            logger.info("  ✓ Token IDs updated for trading")

        except Exception as e:
            logger.warning(f"  Could not update token IDs: {e}")
            session.rollback()


def run_collection():
    """Run a one-time data collection cycle."""
    logger.info("Running data collection...")

    db = DatabaseManager()
    polymarket_api_key = os.getenv('POLYMARKET_API_KEY')
    collector = DataCollector(db, polymarket_api_key)

    results = collector.run_collection_cycle(
        collect_tweets=True,
        collect_markets=True
    )

    logger.info(f"✓ Collection complete: {results}")

    # Update paper trading positions with latest prices
    try:
        from trading.paper_trader import PaperTradingTrader
        from database import PriceSnapshot

        paper_trader = PaperTradingTrader(db)

        with db.get_session() as session:
            # Get latest prices
            latest_prices = (
                session.query(PriceSnapshot)
                .order_by(PriceSnapshot.timestamp.desc())
                .limit(50)
                .all()
            )

            price_updates = {}
            for price_snapshot in latest_prices:
                outcome_id = str(price_snapshot.outcome_id)
                if outcome_id not in price_updates:
                    price_updates[outcome_id] = price_snapshot.price

            if price_updates:
                paper_trader.update_position_prices(price_updates)
                balance = paper_trader.get_balance()
                logger.info(f"✓ Paper trading updated: ${balance['total_value']:,.2f} "
                           f"(P&L: ${balance['pnl']:+,.2f})")
    except Exception as e:
        logger.warning(f"Could not update paper trading: {e}")


def test_xtracker_scraper():
    """Test the xtracker scraper."""
    logger.info("Testing xtracker scraper...")

    scraper = XTrackerScraper()
    data = scraper.scrape_current_count(method='http')

    if data:
        logger.info(f"✓ Successfully scraped data: {data}")
    else:
        logger.warning("⚠ Scraping returned no data")


def test_polymarket_scraper():
    """Test the Polymarket scraper."""
    logger.info("Testing Polymarket scraper...")

    scraper = PolymarketScraper()

    # Search for Elon markets
    markets = scraper.get_active_elon_tweet_markets()

    if markets:
        logger.info(f"✓ Found {len(markets)} active Elon tweet markets")
        for market in markets[:3]:  # Show first 3
            title = market.get('title', market.get('question', 'N/A'))
            market_id = market.get('id', 'N/A')
            volume = market.get('volume', 0)
            logger.info(f"  - [{market_id}] {title}")
            logger.info(f"    Volume: ${volume:,.2f}")

            # Get prices for this market
            prices = scraper.get_market_prices(market_id)
            if prices:
                logger.info(f"    Found {len(prices)} outcome brackets")
                # Show a few example prices
                for price in prices[:5]:
                    logger.info(f"      {price['outcome_text']}: ${price['price']:.4f}")
    else:
        logger.warning("⚠ No markets found")


def start_scheduler(enable_auto_trading: bool = False, trading_mode: str = None):
    """
    Start the scheduled data collection.

    Args:
        enable_auto_trading: If True, automatically execute paper trades when edge exists
    """
    logger.info("Starting scheduler...")
    if enable_auto_trading:
        logger.info("⚠️  AUTO-TRADING ENABLED - Will execute paper trades when edge > 3%")
    else:
        logger.info("Auto-trading disabled - Will only update prices")

    from services.runtime import BotRuntime
    from services.trading_engine import TradingMode

    mode = TradingMode(trading_mode or os.getenv('TRADING_MODE', 'paper'))
    runtime = BotRuntime(
        scheduler_type='blocking',
        trading_mode=mode,
        enable_auto_trading=enable_auto_trading,
    )
    runtime.start()


def print_usage():
    """Print usage information."""
    print("""
Polymarket Trading Bot - Automated Trading System

Usage:
    python scripts/manage.py <command> [--auto-trade] [--mode live|paper]

Commands:
    init              Initialize database tables
    collect           Run one-time data collection (updates paper trading)
    schedule          Start scheduled data collection (blocking worker)
    test-scraper      Test xtracker.io scraper
    test-polymarket   Test Polymarket API scraper

Flags:
    --auto-trade      Enable automatic trading (paper or live mode)
    --mode            Trading mode (paper or live). Defaults to .env TRADING_MODE

Examples:
    python scripts/manage.py init
    python scripts/manage.py collect
    python scripts/manage.py schedule --mode paper
    python scripts/manage.py schedule --auto-trade --mode live

Auto-Trading Thresholds (configurable in .env):
    MIN_EDGE_FOR_TRADE=3.0        # Only trade when edge > 3%
    MIN_KELLY_FOR_TRADE=0.05      # Only trade when Kelly > 5% of max
    """)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1].lower()

    # Check for flags
    enable_auto_trading = '--auto-trade' in sys.argv
    trading_mode = None
    if '--mode' in sys.argv:
        try:
            mode_index = sys.argv.index('--mode')
            trading_mode = sys.argv[mode_index + 1]
        except (ValueError, IndexError):
            logger.error("--mode flag provided without value (paper/live)")
            sys.exit(1)

    commands = {
        'init': init_database,
        'collect': run_collection,
        'schedule': lambda: start_scheduler(enable_auto_trading, trading_mode),
        'test-scraper': test_xtracker_scraper,
        'test-polymarket': test_polymarket_scraper,
    }

    if command in commands:
        try:
            commands[command]()
        except KeyboardInterrupt:
            logger.info("\n✓ Interrupted by user")
        except Exception as e:
            logger.error(f"✗ Error: {e}", exc_info=True)
            sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        print_usage()
        sys.exit(1)


if __name__ == '__main__':
    main()
