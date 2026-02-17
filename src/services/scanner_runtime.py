"""
Scanner runtime: scheduling and orchestration for the multi-market scanner.

Schedules:
- Every 5 min: Full scan -> classify -> estimate -> store opportunities
- Every 1 min: Price refresh for tracked opportunities -> recalculate edge -> execute
- Every 1 hour: Update CategoryPerformance, check resolved markets, log summary
"""

import logging
import os
import sys
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Ensure src is on path
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from database import DatabaseManager
from database.scanner_schema import ScannedMarket, Opportunity, CategoryPerformance, Base as ScannerBase
from database.schema import Base as MainBase
from scanner.rate_limiter import RateLimiter
from scanner.market_scanner import MarketScanner
from scanner.market_classifier import MarketClassifier
from scanner.opportunity_ranker import OpportunityRanker
from analysis.multi_market_monitor import MultiMarketMonitor
from services.multi_market_engine import MultiMarketEngine

logger = logging.getLogger(__name__)

# Configuration
SCANNER_INTERVAL_MINUTES = int(os.getenv("SCANNER_INTERVAL_MINUTES", "5"))
PRICE_REFRESH_SECONDS = int(os.getenv("PRICE_REFRESH_SECONDS", "60"))
PERFORMANCE_UPDATE_MINUTES = 60
TRADING_MODE = os.getenv("TRADING_MODE", "paper")
AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"


class ScannerRuntime:
    """
    Orchestrates the multi-market scanning, estimation, and trading pipeline.

    Can be run as a standalone process or integrated into the existing bot.
    """

    def __init__(
        self,
        scheduler_type: str = "blocking",
        trading_mode: str = None,
        enable_auto_trading: bool = None,
    ):
        self.trading_mode = trading_mode or TRADING_MODE
        self.auto_trade = enable_auto_trading if enable_auto_trading is not None else AUTO_TRADE
        self.db = DatabaseManager()

        # Create scanner tables
        self._init_scanner_tables()

        # Core components
        self.rate_limiter = RateLimiter()
        self.scanner = MarketScanner(rate_limiter=self.rate_limiter)
        self.classifier = MarketClassifier()
        self.ranker = OpportunityRanker()
        self.monitor = MultiMarketMonitor(db_manager=self.db, rate_limiter=self.rate_limiter)

        # Trading engine
        self.trader = self._init_trader()
        bankroll = float(os.getenv("PAPER_TRADING_START_BALANCE", "50"))
        self.engine = MultiMarketEngine(
            bankroll=bankroll,
            trader=self.trader,
            db_manager=self.db,
        )

        # State tracking
        self._tracked_markets: List[Dict] = []
        self._last_scan_time: Optional[datetime] = None
        self._scan_count = 0
        self._trade_count = 0

        # Scheduler
        if scheduler_type == "blocking":
            self.scheduler = BlockingScheduler()
        else:
            self.scheduler = BackgroundScheduler()

        self._setup_jobs()

    def _init_scanner_tables(self):
        """Create all database tables (scanner + paper trading + main)."""
        try:
            from sqlalchemy import create_engine
            db_url = os.getenv("DATABASE_URL", "sqlite:///data/polymarket_bot.db")
            engine = create_engine(db_url)
            # Create main schema tables
            MainBase.metadata.create_all(engine, checkfirst=True)
            # Create scanner tables
            ScannerBase.metadata.create_all(engine, checkfirst=True)
            # Create paper trading tables
            from database.paper_trading_schema import Base as PaperBase
            PaperBase.metadata.create_all(engine, checkfirst=True)
            logger.info("All database tables ready")
        except Exception as e:
            logger.error("Failed to create database tables: %s", e)

    def _init_trader(self):
        """Initialize the appropriate trader based on trading mode."""
        try:
            if self.trading_mode == "live":
                from trading.polymarket_trader import PolymarketTrader
                return PolymarketTrader()
            else:
                from trading.paper_trader import PaperTradingTrader
                return PaperTradingTrader(self.db)
        except Exception as e:
            logger.error("Failed to init trader: %s", e)
            return None

    def _setup_jobs(self):
        """Register scheduled jobs."""
        # Full scan every 5 minutes
        self.scheduler.add_job(
            self.run_full_scan,
            "interval",
            minutes=SCANNER_INTERVAL_MINUTES,
            id="full_scan",
            name="Full Market Scan",
            max_instances=1,
            next_run_time=datetime.now(timezone.utc),  # Run immediately on start
            misfire_grace_time=120,
        )

        # Price refresh every 1 minute
        self.scheduler.add_job(
            self.run_price_refresh,
            "interval",
            seconds=PRICE_REFRESH_SECONDS,
            id="price_refresh",
            name="Price Refresh",
            max_instances=1,
        )

        # Performance update every hour
        self.scheduler.add_job(
            self.run_performance_update,
            "interval",
            minutes=PERFORMANCE_UPDATE_MINUTES,
            id="performance_update",
            name="Performance Update",
            max_instances=1,
        )

    def start(self):
        """Start the scanner runtime."""
        logger.info("=" * 60)
        logger.info("Multi-Market Scanner Starting")
        logger.info("  Mode: %s", self.trading_mode)
        logger.info("  Auto-trade: %s", self.auto_trade)
        logger.info("  Scan interval: %d min", SCANNER_INTERVAL_MINUTES)
        logger.info("  Price refresh: %d sec", PRICE_REFRESH_SECONDS)
        logger.info("  Bankroll: $%.2f", self.engine.bankroll)
        logger.info("=" * 60)

        # Run first scan immediately before starting scheduler
        logger.info("Running initial scan...")
        self.run_full_scan()

        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scanner shutting down...")
            self.shutdown()

    def shutdown(self):
        """Gracefully shut down the scanner."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        logger.info("Scanner shut down complete")

    def run_full_scan(self):
        """
        Full scan cycle: discover markets -> classify -> estimate -> rank -> trade.
        """
        try:
            scan_start = datetime.utcnow()
            self._scan_count += 1
            logger.info("--- Full Scan #%d starting ---", self._scan_count)

            # Step 1: Scan all active markets
            markets = self.scanner.scan_all_markets()
            logger.info("Scanned %d qualifying markets", len(markets))

            if not markets:
                logger.warning("No markets found in scan")
                return

            # Step 2: Classify + Estimate + Filter
            opportunities = self.monitor.process_markets(markets)
            logger.info("Found %d opportunities above threshold", len(opportunities))

            # Step 3: Rank
            if opportunities:
                self.ranker.rank(opportunities)

            # Step 4: Track markets with opportunities
            self._tracked_markets = [
                m for m in markets
                if any(o["market_id"] == m["market_id"] for o in opportunities)
            ]

            # Step 5: Execute trades (if auto-trading enabled)
            if self.auto_trade and opportunities:
                dry_run = self.trading_mode != "live"
                results = self.engine.execute_opportunities(opportunities, dry_run=dry_run)
                trades_executed = sum(1 for r in results if r.get("success"))
                self._trade_count += trades_executed
                logger.info("Executed %d trades (dry_run=%s)", trades_executed, dry_run)

            # Log summary
            elapsed = (datetime.utcnow() - scan_start).total_seconds()
            categories = {}
            for m in markets:
                cat = m.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1

            logger.info(
                "Scan #%d complete in %.1fs: %d markets, %d opportunities, %d trades total",
                self._scan_count, elapsed, len(markets), len(opportunities), self._trade_count,
            )
            logger.info("Category breakdown: %s", categories)

            self._last_scan_time = datetime.utcnow()

        except Exception as e:
            logger.error("Full scan failed: %s", e, exc_info=True)

    def run_price_refresh(self):
        """
        Quick price refresh for tracked markets.
        Recalculates edge and executes if thresholds are met.
        Also checks open positions for profitable exit or edge decay.
        """
        try:
            # --- Check open positions for exits ---
            if self.auto_trade and self.trader:
                self._check_position_exits()

            if not self._tracked_markets:
                return

            market_ids = [m["market_id"] for m in self._tracked_markets]
            price_updates = self.scanner.refresh_prices(market_ids)

            if not price_updates:
                return

            # Recalculate opportunities with fresh prices
            updated_opps = self.monitor.refresh_opportunities(
                self._tracked_markets, price_updates,
            )

            if updated_opps and self.auto_trade:
                dry_run = self.trading_mode != "live"
                self.engine.execute_opportunities(updated_opps, dry_run=dry_run)

        except Exception as e:
            logger.error("Price refresh failed: %s", e, exc_info=True)

    def _check_position_exits(self):
        """
        Check all open positions and sell if:
        - Position is profitable (current_price >= avg_cost * 1.15 = 15% gain)
        - Edge has decayed (current_price moved against us significantly)
        - Position is near expiry with low probability of winning
        """
        try:
            positions = self.trader.get_open_positions()
            if not positions:
                return

            dry_run = self.trading_mode != "live"
            min_edge_exit = float(os.getenv("MIN_EDGE_EXIT", "3")) / 100
            take_profit_pct = 0.15  # Take profit at 15% gain

            for pos in positions:
                shares = pos.get("shares", 0)
                if shares <= 0:
                    continue

                avg_cost = pos.get("average_cost", 0)
                current_price = pos.get("current_price", 0)
                outcome_id = pos.get("outcome_id", "")
                market_title = pos.get("market_title", "")

                if avg_cost <= 0 or current_price <= 0 or not outcome_id:
                    continue

                pnl_pct = (current_price - avg_cost) / avg_cost
                should_sell = False
                reason = ""

                # Take profit: price moved up enough
                if pnl_pct >= take_profit_pct:
                    should_sell = True
                    reason = f"TAKE PROFIT ({pnl_pct:+.1%})"

                # Stop loss: price dropped significantly (> 30% loss)
                elif pnl_pct <= -0.30:
                    should_sell = True
                    reason = f"STOP LOSS ({pnl_pct:+.1%})"

                # Edge decay: price moved to near 0 or near 1 (outcome decided)
                elif current_price >= 0.95:
                    should_sell = True
                    reason = f"NEAR RESOLUTION (price={current_price:.3f})"

                if should_sell:
                    logger.info(
                        "EXIT %s: %s — %d shares @ $%.4f (avg $%.4f, %+.1f%%)",
                        reason, market_title[:40], shares,
                        current_price, avg_cost, pnl_pct * 100,
                    )

                    rec = {
                        "outcome_id": outcome_id,
                        "bracket": market_title,
                        "market_price": current_price,
                        "position_size": current_price * shares,
                        "shares": int(shares),
                        "action": f"SELL ({reason})",
                        "is_sell": True,
                    }

                    result = self.trader.execute_recommendation(rec, dry_run=dry_run)
                    if result.get("success"):
                        pnl = (current_price - avg_cost) * shares
                        self.engine.record_trade_result(pnl)
                        logger.info(
                            "Sold %s for $%.2f P&L",
                            market_title[:40], pnl,
                        )

        except Exception as e:
            logger.error("Position exit check failed: %s", e, exc_info=True)

    def run_performance_update(self):
        """Update performance stats and log summary."""
        try:
            self.engine.update_category_performance()

            # Log summary
            stats = self.monitor.get_category_stats()
            if stats:
                logger.info("--- Performance Summary ---")
                for s in stats:
                    logger.info(
                        "  %s: %d trades, %.1f%% hit rate, $%.2f P&L, %.1f%% ROI",
                        s["category"], s["total_trades"], s["hit_rate"] * 100,
                        s["total_pnl"], s["roi_pct"],
                    )

            # Log bankroll status
            if self.trader:
                try:
                    balance = self.trader.get_balance()
                    total = balance.get("total_value", 0)
                    if total > 0:
                        self.engine.update_bankroll(total)
                        logger.info("Current bankroll: $%.2f", total)
                except Exception:
                    pass

        except Exception as e:
            logger.error("Performance update failed: %s", e, exc_info=True)

    def health(self) -> Dict:
        """Return health status."""
        return {
            "status": "running" if self.scheduler.running else "stopped",
            "trading_mode": self.trading_mode,
            "auto_trade": self.auto_trade,
            "scan_count": self._scan_count,
            "trade_count": self._trade_count,
            "last_scan": self._last_scan_time.isoformat() if self._last_scan_time else None,
            "tracked_markets": len(self._tracked_markets),
            "bankroll": self.engine.bankroll,
            "paused": self.engine.is_paused(),
        }

    def status(self) -> Dict:
        """Return detailed status."""
        health = self.health()
        health["categories"] = self.monitor.get_category_stats()
        health["pending_opportunities"] = len(self.monitor.get_all_opportunities(status="pending"))
        return health


def main():
    """Main entry point for standalone scanner runtime."""
    # Load .env FIRST so env vars are available to module-level reads
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Re-read env vars after dotenv loads (module-level constants are stale)
    trading_mode = os.getenv("TRADING_MODE", "paper")
    auto_trade = os.getenv("AUTO_TRADE", "false").lower() == "true"

    logger.info("Initializing Multi-Market Scanner...")
    runtime = ScannerRuntime(
        scheduler_type="blocking",
        trading_mode=trading_mode,
        enable_auto_trading=auto_trade,
    )

    # Handle graceful shutdown
    def handle_signal(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        runtime.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    runtime.start()


if __name__ == "__main__":
    main()
