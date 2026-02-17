"""
Scheduler for automated data collection.

Runs data collection tasks at regular intervals.
"""

import logging
import os
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from database import DatabaseManager
from collectors import DataCollector


# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class DataCollectionScheduler:
    """
    Manages scheduled data collection tasks.
    """

    def __init__(self, enable_auto_trading: bool = False):
        """
        Initialize scheduler.

        Args:
            enable_auto_trading: If True, automatically execute trades when edge exists
        """
        # Initialize database
        self.db_manager = DatabaseManager()

        # Initialize data collector
        polymarket_api_key = os.getenv('POLYMARKET_API_KEY')
        self.collector = DataCollector(self.db_manager, polymarket_api_key)

        # Create scheduler
        self.scheduler = BlockingScheduler()

        # Get configuration from environment
        self.scraper_interval_minutes = float(os.getenv('SCRAPER_INTERVAL_MINUTES', 10))

        # Validate interval (minimum 0.1 minutes = 6 seconds to prevent API/DB issues)
        if self.scraper_interval_minutes < 0.1:
            logger.warning(f"SCRAPER_INTERVAL_MINUTES={self.scraper_interval_minutes} is too low. Using minimum of 0.1 minutes (6 seconds)")
            self.scraper_interval_minutes = 0.1

        self.enable_auto_trading = enable_auto_trading
        self.auto_discover_markets = os.getenv('AUTO_DISCOVER_MARKETS', 'true').lower() == 'true'

        # Auto-trading thresholds
        self.min_edge_for_trade = float(os.getenv('MIN_EDGE_FOR_TRADE', 3.0))  # 3% minimum edge
        self.min_kelly_for_trade = float(os.getenv('MIN_KELLY_FOR_TRADE', 0.05))  # 5% of max Kelly

        logger.info(f"Auto-discover markets: {self.auto_discover_markets}")

    def setup_jobs(self):
        """
        Set up scheduled jobs.
        """
        logger.info("Setting up scheduled jobs...")

        # Job 1: Collect tweet data every N minutes
        self.scheduler.add_job(
            func=self.collect_tweets_job,
            trigger=IntervalTrigger(minutes=self.scraper_interval_minutes),
            id='collect_tweets',
            name='Collect tweet data',
            replace_existing=True,
        )
        logger.info(f"✓ Tweet collection job scheduled (every {self.scraper_interval_minutes} minutes)")

        # Job 2: Collect market data every 15 minutes
        self.scheduler.add_job(
            func=self.collect_markets_job,
            trigger=IntervalTrigger(minutes=15),
            id='collect_markets',
            name='Collect market data',
            replace_existing=True,
        )
        logger.info("✓ Market collection job scheduled (every 15 minutes)")

        # Job 3: Check pending order fills every 1 minute
        self.scheduler.add_job(
            func=self.check_order_fills_job,
            trigger=IntervalTrigger(minutes=1),
            id='check_order_fills',
            name='Check order fills',
            replace_existing=True,
        )
        logger.info("✓ Order fill checking job scheduled (every 1 minute)")

        # Job 4: Run full collection cycle immediately on start
        self.scheduler.add_job(
            func=self.initial_collection,
            trigger=None,  # Run once
            id='initial_collection',
            name='Initial data collection',
        )
        logger.info("✓ Initial collection job scheduled")

    def collect_tweets_job(self):
        """Job to collect tweet data."""
        logger.info("Running tweet collection job...")
        try:
            # Run market discovery (auto-limited to once per hour)
            if self.auto_discover_markets:
                self.collector.polymarket.discover_new_markets()

            success = self.collector.collect_tweet_data()
            if success:
                logger.info("✓ Tweet collection completed successfully")
            else:
                logger.warning("⚠ Tweet collection completed with issues")
        except Exception as e:
            logger.error(f"✗ Tweet collection failed: {e}", exc_info=True)

    def collect_markets_job(self):
        """Job to collect market data."""
        logger.info("Running market collection job...")
        try:
            success = self.collector.collect_market_data()
            if success:
                logger.info("✓ Market collection completed successfully")
                # Update paper trading positions after getting new prices
                self.update_paper_trading()
            else:
                logger.warning("⚠ Market collection completed with issues")
        except Exception as e:
            logger.error(f"✗ Market collection failed: {e}", exc_info=True)

    def check_order_fills_job(self):
        """Job to poll pending orders and update when filled."""
        try:
            self.trading_engine.update_pending_orders()
        except Exception as e:
            logger.error(f"✗ Order fill check failed: {e}", exc_info=True)

    def update_paper_trading(self):
        """Update paper trading positions and optionally execute trades."""
        try:
            from trading.paper_trader import PaperTradingTrader
            from analysis.monitor import BotMonitor
            from database import PriceSnapshot

            paper_trader = PaperTradingTrader(self.db_manager)

            # Update position prices with latest market data
            with self.db_manager.get_session() as session:
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

            # Auto-execute trades if enabled
            if self.enable_auto_trading:
                self.auto_execute_trades(paper_trader)

        except Exception as e:
            logger.warning(f"Could not update paper trading: {e}")

    def auto_execute_trades(self, paper_trader):
        """
        Automatically execute trades when there's significant edge.

        Only trades when:
        - Kelly fraction > min_kelly_for_trade (default 5% of max)
        - Edge > min_edge_for_trade (default 3%)
        - Timing is NOW or EARLY
        """
        try:
            from analysis.monitor import BotMonitor

            monitor = BotMonitor(self.db_manager)
            balance = paper_trader.get_balance()
            positions_dict = paper_trader.get_positions_dict()

            # Get recommendations
            recommendations = monitor.get_recommendations(
                bankroll=balance['total_value'],
                max_kelly=0.25,
                current_positions=positions_dict
            )

            if not recommendations:
                logger.info("No trading opportunities")
                return

            # Filter to only high-value trades
            # IMPORTANT: Always execute STOP LOSS trades regardless of edge (risk management!)
            sells = [r for r in recommendations
                    if r.get('is_sell', False)
                    and r['position_size'] > 0
                    and r.get('timing') == 'NOW'
                    and (abs(r['edge']) > self.min_edge_for_trade or 'STOP LOSS' in r.get('action', ''))]

            buys = [r for r in recommendations
                   if not r.get('is_sell', False)
                   and r['position_size'] > 0
                   and r['edge'] > self.min_edge_for_trade
                   and r['kelly_fraction'] > self.min_kelly_for_trade
                   and r.get('timing') in ['NOW', 'EARLY']]

            if not sells and not buys:
                logger.info("No trades meet minimum edge criteria")
                return

            logger.info(f"Found {len(buys)} buy opportunities, {len(sells)} sell opportunities")

            # Execute sells first
            for rec in sells:
                paper_trader.execute_sell(
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

            # Execute buys
            for rec in buys[:5]:  # Limit to top 5 buys per cycle
                paper_trader.execute_buy(
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

            # Create snapshot
            prediction = monitor.calculate_prediction()
            if 'error' not in prediction:
                paper_trader.create_snapshot(
                    week_progress=prediction.get('week_progress', 0),
                    predicted_count=prediction.get('predicted_total', 0),
                    actual_count=prediction.get('current_count', 0),
                )

        except Exception as e:
            logger.error(f"Auto-trading failed: {e}", exc_info=True)

    def initial_collection(self):
        """Run initial data collection on startup."""
        logger.info("Running initial data collection...")
        try:
            results = self.collector.run_collection_cycle(
                collect_tweets=True,
                collect_markets=True,
                discover_markets=self.auto_discover_markets
            )
            logger.info(f"✓ Initial collection completed: {results}")
        except Exception as e:
            logger.error(f"✗ Initial collection failed: {e}", exc_info=True)

    def start(self):
        """
        Start the scheduler.
        """
        logger.info("=" * 60)
        logger.info("Starting Polymarket Data Collection Scheduler")
        logger.info("=" * 60)

        try:
            # Ensure log directory exists
            os.makedirs('logs', exist_ok=True)

            # Ensure database is set up
            self.db_manager.create_tables()

            # Set up jobs
            self.setup_jobs()

            # Start scheduler
            logger.info("Scheduler started. Press Ctrl+C to exit.")
            self.scheduler.start()

        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
            self.scheduler.shutdown()

        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
            self.scheduler.shutdown()


def main():
    """Main entry point."""
    scheduler = DataCollectionScheduler()
    scheduler.start()


if __name__ == '__main__':
    main()
