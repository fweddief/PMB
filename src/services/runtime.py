"""
Background runtime that keeps ingestion, modeling, and trading running.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from collectors import DataCollector
from database import DatabaseManager

from .trading_engine import TradingEngine, TradingMode

logger = logging.getLogger(__name__)


class BotRuntime:
    """
    Coordinates the collector, probabilistic model, and trading engine.
    """

    def __init__(
        self,
        scheduler_type: str = "blocking",
        trading_mode: TradingMode = TradingMode.PAPER,
        enable_auto_trading: bool = False,
    ):
        self.db = DatabaseManager()
        self.collector = DataCollector(
            self.db,
            os.getenv("POLYMARKET_API_KEY"),
        )

        self.trading_mode = trading_mode
        self.enable_auto_trading = enable_auto_trading
        self.trading_engine = TradingEngine(
            self.db,
            mode=trading_mode,
            enable_auto_trading=enable_auto_trading,
        )

        scheduler_cls = BlockingScheduler if scheduler_type == "blocking" else BackgroundScheduler
        self.scheduler = scheduler_cls(timezone="UTC")

        self.scraper_interval_minutes = float(os.getenv("SCRAPER_INTERVAL_MINUTES", 10))

        # Validate interval (minimum 0.1 minutes = 6 seconds to prevent API/DB issues)
        if self.scraper_interval_minutes < 0.1:
            logger.warning(f"SCRAPER_INTERVAL_MINUTES={self.scraper_interval_minutes} is too low. Using minimum of 0.1 minutes (6 seconds)")
            self.scraper_interval_minutes = 0.1

    def start(self):
        """Start the runtime."""
        if self.scheduler.running:
            return

        self.db.create_tables()
        self._setup_jobs()
        self.scheduler.start()

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _setup_jobs(self):
        self.scheduler.add_job(
            self.collect_tweets_job,
            trigger=IntervalTrigger(minutes=self.scraper_interval_minutes),
            id="collect_tweets",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.collect_markets_job,
            trigger=IntervalTrigger(minutes=15),
            id="collect_markets",
            replace_existing=True,
        )

        # Run once on startup
        self.scheduler.add_job(
            self.initial_collection,
            trigger="date",
            run_date=datetime.utcnow(),
            id="initial_collection",
            replace_existing=True,
        )

    def collect_tweets_job(self):
        """Collect tweet data and trigger auto-trading."""
        try:
            self.collector.collect_tweet_data(method="polymarket")
            # Trigger auto-trading check after each tweet collection
            # This ensures trades execute promptly when conditions are met
            self.trading_engine.process_market_prices()
        except Exception as exc:
            logger.exception("Tweet collection job failed: %s", exc)

    def collect_markets_job(self):
        try:
            self.collector.collect_market_data()
            self.trading_engine.process_market_prices()
        except Exception as exc:
            logger.exception("Market collection job failed: %s", exc)

    def initial_collection(self):
        try:
            self.collector.run_collection_cycle(collect_tweets=True, collect_markets=True)
            self.trading_engine.process_market_prices()
        except Exception as exc:
            logger.exception("Initial collection failed: %s", exc)

    # API helpers ---------------------------------------------------------
    def health(self) -> Dict[str, str]:
        return {
            "scheduler_running": self.scheduler.running,
            "trading_mode": self.trading_mode.value,
            "auto_trading": self.enable_auto_trading,
        }

    def latest_prediction(self) -> Dict:
        return self.trading_engine.monitor.calculate_prediction()

    def status(self) -> Dict:
        return self.trading_engine.monitor.check_status()

    def recommendations(self) -> Dict:
        """Get trading recommendations for ALL currently active markets."""
        all_recommendations = []
        active_markets = self.trading_engine._get_active_markets()

        for market in active_markets:
            recs = self.trading_engine.monitor.get_recommendations(
                bankroll=self.trading_engine.bankroll(),
                current_positions=self.trading_engine._current_positions(),
                market_id=market["market_id"],
            )
            all_recommendations.extend(recs)

        # Sort by edge (highest first) for display
        all_recommendations.sort(key=lambda x: x.get("edge", 0), reverse=True)

        return {"recommendations": all_recommendations}
