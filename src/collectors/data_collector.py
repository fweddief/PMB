"""
Main data collector that orchestrates scraping and database storage.
"""

import logging
import os
import csv
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from sqlalchemy.orm import Session

from database import DatabaseManager, TweetData, PolymarketMarket, MarketOutcome, PriceSnapshot
from services.velocity import VelocityCalculator
from scrapers import XTrackerScraper, PolymarketScraper


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ConditionalAllowanceManager:
    """Ensures conditional tokens are approved for future sells."""

    def __init__(self):
        self.enabled = os.getenv('AUTO_APPROVE_TOKENS', 'true').lower() == 'true'
        self.trader = None
        if self.enabled:
            try:
                from trading.polymarket_trader import PolymarketTrader
                self.trader = PolymarketTrader()
                logger.info("✓ Token allowance manager ready")
            except Exception as exc:
                logger.warning(f"Could not initialize allowance manager: {exc}")
                self.trader = None

    def ensure(self, token_id: str):
        if self.trader and token_id:
            try:
                self.trader.ensure_token_allowance(token_id)
            except Exception as exc:
                logger.warning(f"Unable to auto-approve token {token_id}: {exc}")


class DataCollector:
    """
    Orchestrates data collection from xtracker and Polymarket,
    storing results in the database.
    """

    def __init__(self, db_manager: DatabaseManager, polymarket_api_key: str = None):
        """
        Initialize data collector.

        Args:
            db_manager: Database manager instance
            polymarket_api_key: Polymarket API key (optional)
        """
        self.db = db_manager
        self.xtracker = XTrackerScraper()
        self.polymarket = PolymarketScraper(api_key=polymarket_api_key)
        self.velocity_calculator = VelocityCalculator()
        self.allowance_manager = ConditionalAllowanceManager()
        history_path = os.getenv('TWEET_HISTORY_DIR', 'data/history')
        self.history_dir = Path(history_path)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def collect_tweet_data(self, method: str = 'polymarket') -> bool:
        """
        Collect current tweet count and store for ALL active markets.

        For Polymarket API: Each market has its own tweet count for its time period.
        For xtracker: One global count, normalized per market.

        Args:
            method: Data source ('polymarket' for live API, 'browser' for xtracker scraping)

        Returns:
            True if successful, False otherwise
        """
        logger.info("Collecting tweet data for all active markets...")

        # Get ALL currently active markets
        with self.db.get_session() as session:
            from database import PolymarketMarket
            from datetime import datetime

            # Get all currently active markets based on date
            now = datetime.utcnow()
            active_markets_query = (
                session.query(PolymarketMarket)
                .filter(
                    PolymarketMarket.week_start <= now,
                    PolymarketMarket.week_end >= now,
                )
                .order_by(PolymarketMarket.week_end.asc())
                .all()
            )

            if not active_markets_query:
                logger.warning("No active markets found")
                return False

            logger.info(f"Found {len(active_markets_query)} active markets to track")

            # Extract data we need before session closes (avoid detached instance errors)
            active_markets = [
                {
                    'market_id': market.market_id,
                    'week_start': market.week_start,
                    'week_end': market.week_end,
                }
                for market in active_markets_query
            ]

        # For Polymarket API, fetch per-market counts
        if method == 'polymarket':
            return self._collect_polymarket_counts(active_markets)
        else:
            # For xtracker, use global count and normalize
            return self._collect_xtracker_counts(active_markets, method)

    def _collect_polymarket_counts(self, active_markets: list) -> bool:
        """Collect tweet counts from Polymarket API (per-market)."""
        try:
            timestamp = datetime.utcnow()
            with self.db.get_session() as session:
                records_created = 0

                for market in active_markets:
                    # Get tweet count for THIS specific market
                    tweet_count = self.polymarket.get_tweet_count_for_market(market['market_id'])

                    if tweet_count is None:
                        logger.warning(f"Failed to get tweet count for market {market['market_id']}")
                        continue

                    # Calculate velocity for this market
                    velocity_snapshot = self.velocity_calculator.calculate(
                        session=session,
                        week_start=market['week_start'],
                        latest_timestamp=timestamp,
                        latest_count=tweet_count,
                    )

                    tweet_record = TweetData(
                        timestamp=timestamp,
                        cumulative_count=tweet_count,  # Already normalized to market period
                        week_start=market['week_start'],
                        week_end=market['week_end'],
                        source='polymarket_api',
                        velocity_5m=velocity_snapshot.velocity_5m,
                        velocity_20m=velocity_snapshot.velocity_20m,
                        velocity_1h=velocity_snapshot.velocity_1h,
                        velocity_6h=velocity_snapshot.velocity_6h,
                        velocity_24h=velocity_snapshot.velocity_24h,
                        acceleration=velocity_snapshot.acceleration,
                    )

                    # Maintain backwards-compatible metrics for dashboards
                    simple_velocity = self._calculate_velocity_metrics(session, market['week_start'], market['week_end'])
                    if simple_velocity:
                        tweet_record.tweets_per_hour = simple_velocity['tweets_per_hour']
                        tweet_record.tweets_per_day = simple_velocity['tweets_per_day']

                    session.add(tweet_record)
                    records_created += 1
                    self._archive_tweet_point(
                        market_id=market['market_id'],
                        timestamp=timestamp,
                        count=tweet_count,
                        source='polymarket_api',
                    )

                session.commit()

                logger.info(f"✓ Saved tweet data for {records_created} markets at {timestamp}")
                return True

        except Exception as e:
            logger.error(f"Failed to save polymarket tweet data: {e}", exc_info=True)
            return False

    def _collect_xtracker_counts(self, active_markets: list, method: str) -> bool:
        """Collect global tweet count from xtracker and normalize per market."""
        # Get global tweet count
        data = self.xtracker.scrape_current_count(method=method)

        if not data:
            logger.error("Failed to scrape tweet data from xtracker")
            return False

        # Store normalized data for ALL active markets
        try:
            with self.db.get_session() as session:
                # Calculate GLOBAL velocity (same for all markets)
                velocity_snapshot = self.velocity_calculator.calculate(
                    session=session,
                    week_start=active_markets[0]['week_start'],  # Use any market for global calc
                    latest_timestamp=data['timestamp'],
                    latest_count=data['cumulative_count'],
                )

                # Create data point for EACH active market
                records_created = 0
                for market in active_markets:
                    # Normalize count for this market's start time
                    normalized_count = self._normalize_week_count(
                        session=session,
                        week_start=market['week_start'],
                        raw_count=data['cumulative_count'],
                        source=data['source'],
                    )

                    tweet_record = TweetData(
                        timestamp=data['timestamp'],
                        cumulative_count=normalized_count,
                        week_start=market['week_start'],
                        week_end=market['week_end'],
                        source=data['source'],
                        velocity_5m=velocity_snapshot.velocity_5m,
                        velocity_20m=velocity_snapshot.velocity_20m,
                        velocity_1h=velocity_snapshot.velocity_1h,
                        velocity_6h=velocity_snapshot.velocity_6h,
                        velocity_24h=velocity_snapshot.velocity_24h,
                        acceleration=velocity_snapshot.acceleration,
                    )

                    # Maintain backwards-compatible metrics for dashboards
                    simple_velocity = self._calculate_velocity_metrics(session, market['week_start'], market['week_end'])
                    if simple_velocity:
                        tweet_record.tweets_per_hour = simple_velocity['tweets_per_hour']
                        tweet_record.tweets_per_day = simple_velocity['tweets_per_day']

                    session.add(tweet_record)
                    records_created += 1
                    self._archive_tweet_point(
                        market_id=market['market_id'],
                        timestamp=data['timestamp'],
                        count=normalized_count,
                        source=data['source'],
                    )

                session.commit()

                logger.info(f"✓ Saved tweet data for {records_created} markets: {data['cumulative_count']} tweets at {data['timestamp']}")
                logger.debug(f"Velocity snapshot: {velocity_snapshot.as_dict()}")
                return True

        except Exception as e:
            logger.error(f"Failed to save tweet data: {e}", exc_info=True)
            return False

    def _archive_tweet_point(self, market_id: str, timestamp: datetime, count: float, source: str) -> None:
        """
        Persist a lightweight CSV record for historical analysis so restarts retain history.
        """
        try:
            file_path = self.history_dir / f"{market_id}.csv"
            is_new_file = not file_path.exists()
            with file_path.open("a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if is_new_file:
                    writer.writerow(["timestamp", "market_id", "count", "source"])
                writer.writerow([timestamp.isoformat(), market_id, f"{count:.6f}", source])
        except Exception as exc:
            logger.warning(f"Could not archive tweet snapshot for {market_id}: {exc}")

    def _calculate_velocity_metrics(self, session: Session, week_start: datetime, week_end: datetime) -> Optional[dict]:
        """
        Calculate tweet velocity metrics based on recent data.

        Args:
            session: Database session
            week_start: Start of current week
            week_end: End of current week

        Returns:
            Dictionary with velocity metrics or None
        """
        from datetime import timedelta

        # Get recent data from this week
        recent_data = session.query(TweetData).filter(
            TweetData.week_start == week_start,
            TweetData.week_end == week_end
        ).order_by(TweetData.timestamp.desc()).limit(12).all()  # Last hour if polling every 5 min

        if len(recent_data) < 2:
            return None

        # Calculate tweets per hour
        oldest = recent_data[-1]
        newest = recent_data[0]

        time_diff = (newest.timestamp - oldest.timestamp).total_seconds() / 3600  # hours
        tweet_diff = newest.cumulative_count - oldest.cumulative_count

        if time_diff > 0:
            tweets_per_hour = tweet_diff / time_diff
            tweets_per_day = tweets_per_hour * 24

            return {
                'tweets_per_hour': round(tweets_per_hour, 2),
                'tweets_per_day': round(tweets_per_day, 2),
            }

        return None

    def _normalize_week_count(
        self,
        session: Session,
        week_start: datetime,
        raw_count: int,
        source: str,
    ) -> int:
        """
        Convert cumulative counts to market-week counts.

        For Polymarket API data, the count is ALREADY normalized to the market's
        time period, so we return it as-is.

        For browser/xtracker data, we normalize by subtracting the baseline count
        from the first data point collected for that market.
        """
        # Polymarket API already returns normalized counts for each market period
        if source == 'polymarket_api':
            return raw_count

        # For xtracker data, normalize against the first data point
        first_point = (
            session.query(TweetData)
            .filter(TweetData.week_start == week_start)
            .order_by(TweetData.timestamp.asc())
            .first()
        )

        baseline = first_point.cumulative_count if first_point else raw_count
        return max(0, raw_count - baseline)

    def collect_market_data(self) -> bool:
        """
        Collect Polymarket market data and prices.

        Returns:
            True if successful, False otherwise
        """
        logger.info("Collecting Polymarket market data...")

        # Get active Elon tweet markets
        markets = self.polymarket.get_active_elon_tweet_markets()

        if not markets:
            logger.warning("No active Elon tweet markets found")
            return False

        logger.info(f"Found {len(markets)} active markets")

        try:
            with self.db.get_session() as session:
                for market_data in markets:
                    self._store_market_data(session, market_data)

                session.commit()
                logger.info(f"✓ Saved data for {len(markets)} markets")
                return True

        except Exception as e:
            logger.error(f"Failed to save market data: {e}", exc_info=True)
            return False

    def _store_market_data(self, session: Session, market_data: dict):
        """
        Store or update market data in database.

        Args:
            session: Database session
            market_data: Market data dictionary from Polymarket API
        """
        market_id = market_data.get('id') or market_data.get('condition_id')

        if not market_id:
            logger.warning(f"Market missing ID: {market_data.get('question', 'Unknown')}")
            return

        # Check if market already exists
        existing_market = session.query(PolymarketMarket).filter_by(market_id=market_id).first()

        if not existing_market:
            # Parse week bounds from market title/description
            # This is a simplified version - you may need more sophisticated parsing
            week_start, week_end = self._parse_week_from_market(market_data)

            # Create new market
            market = PolymarketMarket(
                market_id=market_id,
                title=market_data.get('title', market_data.get('question', '')),
                week_start=week_start,
                week_end=week_end,
                is_active=market_data.get('active', True),
            )
            session.add(market)
            session.flush()  # Get the ID

            # Store outcomes (in events API, these are called 'markets')
            outcomes = market_data.get('markets', market_data.get('outcomes', []))
            for outcome_data in outcomes:
                outcome_text = outcome_data.get('groupItemTitle', outcome_data.get('name', outcome_data.get('question', '')))
                min_tweets, max_tweets = self.polymarket.parse_tweet_range_from_outcome(outcome_text)

                # Use CLOB token ID for trading
                # For bracket markets, use the first CLOB token
                clob_token_ids_str = outcome_data.get('clobTokenIds', '[]')

                try:
                    clob_token_ids = eval(clob_token_ids_str)
                    token_id = clob_token_ids[0] if clob_token_ids else outcome_data.get('id')
                except:
                    token_id = outcome_data.get('id')

                outcome = MarketOutcome(
                    market_id=market.id,
                    outcome_id=str(token_id),  # Use CLOB token ID instead of market ID
                    outcome_text=outcome_text,
                    min_tweets=min_tweets,
                    max_tweets=max_tweets,
                )
                session.add(outcome)
                self.allowance_manager.ensure(str(token_id))

            logger.info(f"Created new market: {market.title}")

        else:
            market = existing_market
            # Update market status
            market.is_active = market_data.get('active', True)
            market.updated_at = datetime.utcnow()

        # Store price snapshot for all outcomes
        self._store_price_snapshot(session, market, market_data)

    def _store_price_snapshot(self, session: Session, market: PolymarketMarket, market_data: dict):
        """
        Store current prices for all outcomes with bid/ask from order book.

        Args:
            session: Database session
            market: PolymarketMarket instance
            market_data: Market data from API
        """
        # In events API, outcomes are in 'markets' array
        outcomes = market_data.get('markets', market_data.get('outcomes', []))

        for outcome_data in outcomes:
            # Use CLOB token ID to match MarketOutcome.outcome_id
            clob_token_ids_str = outcome_data.get('clobTokenIds', '[]')
            try:
                clob_token_ids = eval(clob_token_ids_str)
                outcome_id = str(clob_token_ids[0]) if clob_token_ids else str(outcome_data.get('id'))
            except:
                outcome_id = str(outcome_data.get('id'))
            self.allowance_manager.ensure(outcome_id)

            # Parse midpoint price from outcomePrices string
            outcome_prices_str = outcome_data.get('outcomePrices', '["0.5", "0.5"]')
            try:
                outcome_prices = eval(outcome_prices_str)
                midpoint_price = float(outcome_prices[0]) if outcome_prices else 0.0
            except:
                midpoint_price = outcome_data.get('price', 0.0)
                if isinstance(midpoint_price, str):
                    midpoint_price = float(midpoint_price)

            # For bracket markets, DON'T use order books - they're often inverted or missing
            # The order books trade the NO side for low-probability outcomes
            # Instead, use the midpoint price from outcomePrices and estimate spreads
            bid_price = None
            ask_price = None

            # Fallback: If no order book data, estimate bid/ask from midpoint
            # Assume ~2% spread for illiquid markets, 0.5% for liquid
            if bid_price is None or ask_price is None:
                liquidity_val = outcome_data.get('liquidity', outcome_data.get('liquidityNum', 0))

                # Convert liquidity to float (API sometimes returns string)
                try:
                    liquidity_val = float(liquidity_val) if liquidity_val else 0
                except (ValueError, TypeError):
                    liquidity_val = 0

                # Smart spread estimation based on liquidity
                if liquidity_val > 10000:
                    spread_estimate = 0.005  # 0.5% for high liquidity
                elif liquidity_val > 1000:
                    spread_estimate = 0.01   # 1% for medium liquidity
                else:
                    spread_estimate = 0.015  # 1.5% for low liquidity

                # Add extra spread for very low probability outcomes (more volatile)
                if midpoint_price < 0.05:
                    spread_estimate += 0.005

                if bid_price is None:
                    bid_price = max(0.001, midpoint_price * (1 - spread_estimate))
                if ask_price is None:
                    ask_price = min(0.999, midpoint_price * (1 + spread_estimate))

            snapshot = PriceSnapshot(
                market_id=market.id,
                outcome_id=outcome_id,
                timestamp=datetime.utcnow(),
                price=midpoint_price,  # Keep midpoint for reference
                bid=bid_price,         # What you'd get when selling
                ask=ask_price,         # What you'd pay when buying
                volume_24h=outcome_data.get('volume24hr', outcome_data.get('volume_24h')),
                liquidity=outcome_data.get('liquidity', outcome_data.get('liquidityNum')),
            )
            session.add(snapshot)

    def _parse_week_from_market(self, market_data: dict) -> tuple:
        """
        Parse contract start/end from market data. Works for weekly, monthly, or custom spans.

        IMPORTANT: Parse from title FIRST since API fields (startDate, closesAt, etc.) often don't
        match the actual market period shown to users. The title is the source of truth.
        """
        from dateutil import parser
        import re

        # PRIORITY 1: Parse from title (e.g. "January 20 - January 27, 2026")
        # This is what users see and should be the source of truth
        title = market_data.get('title', market_data.get('question', ''))
        current_year = datetime.utcnow().year
        date_pattern = r'([A-Z][a-z]+)\s+(\d{1,2})'
        matches = re.findall(date_pattern, title)

        if len(matches) >= 2:
            try:
                import pytz

                start_str = f"{matches[0][0]} {matches[0][1]}, {current_year}"
                end_month = matches[1][0]
                # If the second month name indicates a wrap into next year (e.g., Dec-Jan)
                end_year = current_year + 1 if end_month.lower() == 'january' and matches[0][0].lower() == 'december' else current_year
                end_str = f"{matches[1][0]} {matches[1][1]}, {end_year}"

                # Parse dates (will be midnight by default)
                week_start = parser.parse(start_str)
                week_end = parser.parse(end_str)

                # CRITICAL: Polymarket markets start/end at 12 PM EST (noon Eastern Time)
                # Set time to 12:00 PM EST = 17:00 UTC (EST is UTC-5)
                eastern = pytz.timezone('America/New_York')

                # Create timezone-aware datetimes at noon Eastern
                week_start = eastern.localize(week_start.replace(hour=12, minute=0, second=0, microsecond=0))
                week_end = eastern.localize(week_end.replace(hour=12, minute=0, second=0, microsecond=0))

                # Convert to UTC for database storage
                week_start = week_start.astimezone(pytz.UTC).replace(tzinfo=None)
                week_end = week_end.astimezone(pytz.UTC).replace(tzinfo=None)

                logger.info(f"Parsed market dates from title: {week_start} to {week_end} (12 PM EST)")
                return week_start, week_end
            except Exception as e:
                logger.warning(f"Failed to parse dates from title '{title}': {e}")

        # PRIORITY 2: Try explicit API fields (often incorrect/misleading)
        date_fields = [
            ('startDate', 'endDate'),
            ('start_date', 'end_date'),
            ('startTime', 'endTime'),
            ('closesAt', 'resolveTime'),
        ]

        for start_key, end_key in date_fields:
            start_val = market_data.get(start_key)
            end_val = market_data.get(end_key)
            if start_val and end_val:
                try:
                    start_dt = parser.parse(start_val)
                    end_dt = parser.parse(end_val)
                    logger.warning(f"Using API fields {start_key}/{end_key} for market dates (title parsing failed)")
                    return start_dt, end_dt
                except Exception:
                    continue

        # Ultimate fallback: use xtracker calendar week
        logger.warning("Could not parse market dates, using current calendar week")
        return self.xtracker.get_week_bounds()

    def run_collection_cycle(self, collect_tweets: bool = True, collect_markets: bool = True, discover_markets: bool = True) -> dict:
        """
        Run a complete data collection cycle.

        Args:
            collect_tweets: Whether to collect tweet data
            collect_markets: Whether to collect market data
            discover_markets: Whether to run automatic market discovery

        Returns:
            Dictionary with collection results
        """
        logger.info("=" * 60)
        logger.info("Starting data collection cycle")
        logger.info("=" * 60)

        results = {
            'timestamp': datetime.utcnow(),
            'market_discovery_run': False,
            'new_markets_found': 0,
            'tweets_collected': False,
            'markets_collected': False,
            'errors': [],
        }

        # Step 1: Auto-discover new markets (runs max once per hour)
        if discover_markets:
            try:
                logger.info("Running automatic market discovery...")
                discovered = self.polymarket.discover_new_markets()
                results['market_discovery_run'] = True
                results['new_markets_found'] = len(discovered)
                if discovered:
                    logger.info(f"✓ Discovered {len(discovered)} new markets!")
                    for market in discovered:
                        logger.info(f"  - {market['id']}: {market['title']}")
            except Exception as e:
                logger.error(f"Market discovery failed: {e}", exc_info=True)
                results['errors'].append(f"Market discovery: {str(e)}")

        # Step 2: Collect tweet data
        if collect_tweets:
            try:
                results['tweets_collected'] = self.collect_tweet_data()
            except Exception as e:
                logger.error(f"Tweet collection failed: {e}", exc_info=True)
                results['errors'].append(f"Tweet collection: {str(e)}")

        # Step 3: Collect market prices and liquidity
        if collect_markets:
            try:
                results['markets_collected'] = self.collect_market_data()
            except Exception as e:
                logger.error(f"Market collection failed: {e}", exc_info=True)
                results['errors'].append(f"Market collection: {str(e)}")

        logger.info("=" * 60)
        logger.info(f"Collection cycle complete: {results}")
        logger.info("=" * 60)

        return results
