"""
Real-time monitoring and analysis of bot performance.
"""

import sys
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from sqlalchemy import desc

sys.path.insert(0, 'src')
from database import DatabaseManager, TweetData, PolymarketMarket, MarketOutcome, PriceSnapshot
from services.probabilities import TweetDistributionModel
from services.time_aware_model import TimeAwareTweetModel
from analysis.trading_rules import EnhancedTradingLogic, TradingThresholds

logger = logging.getLogger(__name__)


class BotMonitor:
    """Monitor bot status and provide trading recommendations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.distribution_model = TimeAwareTweetModel()
        self.trading_logic = EnhancedTradingLogic()

    def check_status(self) -> Dict:
        """
        Check if data collection is working.

        Returns:
            Status dictionary
        """
        with self.db.get_session() as session:
            # Check latest tweet data
            latest_tweet = session.query(TweetData).order_by(desc(TweetData.timestamp)).first()

            # Check latest market data (get currently active market based on date)
            now = datetime.utcnow()
            latest_market = (
                session.query(PolymarketMarket)
                .filter(
                    PolymarketMarket.week_start <= now,
                    PolymarketMarket.week_end >= now,
                )
                .order_by(PolymarketMarket.week_end.asc())
                .first()
            )

            # Check latest price snapshot
            latest_price = session.query(PriceSnapshot).order_by(desc(PriceSnapshot.timestamp)).first()

            # Calculate time since last update
            now = datetime.utcnow()

            status = {
                'working': False,
                'tweet_data': None,
                'market_data': None,
                'price_data': None,
                'warnings': []
            }

            if latest_tweet:
                minutes_ago = (now - latest_tweet.timestamp).total_seconds() / 60
                status['tweet_data'] = {
                    'last_update': latest_tweet.timestamp,
                    'minutes_ago': round(minutes_ago, 1),
                    'count': latest_tweet.cumulative_count,
                    'velocity': latest_tweet.tweets_per_hour,
                }
                if minutes_ago > 30:
                    status['warnings'].append(f"Tweet data is {round(minutes_ago)} minutes old")
            else:
                status['warnings'].append("No tweet data collected yet")

            if latest_market:
                status['market_data'] = {
                    'title': latest_market.title,
                    'market_id': latest_market.market_id,
                    'week_start': latest_market.week_start,
                    'week_end': latest_market.week_end,
                }
            else:
                status['warnings'].append("No market data found")

            if latest_price:
                minutes_ago = (now - latest_price.timestamp).total_seconds() / 60
                status['price_data'] = {
                    'last_update': latest_price.timestamp,
                    'minutes_ago': round(minutes_ago, 1),
                }
                if minutes_ago > 30:
                    status['warnings'].append(f"Price data is {round(minutes_ago)} minutes old")
            else:
                status['warnings'].append("No price data collected yet")

            # Overall status
            status['working'] = (latest_tweet is not None and
                               latest_market is not None and
                               latest_price is not None)

            return status

    def get_market_bell_curve(self, market_id: str = None) -> List[Dict]:
        """
        Get current market probability distribution (bell curve).

        Args:
            market_id: Market ID (uses most recent if None)

        Returns:
            List of {bracket, min, max, price, implied_prob}
        """
        with self.db.get_session() as session:
            market = self._get_market(session, market_id)

            if not market:
                logger.warning(f"No market found for market_id={market_id}")
                return []

            logger.debug(f"get_market_bell_curve: market_id={market.market_id}, internal_id={market.id}")

            # Get latest prices
            latest_prices = (
                session.query(PriceSnapshot)
                .filter_by(market_id=market.id)
                .order_by(desc(PriceSnapshot.timestamp))
                .limit(50)  # Get recent snapshots
                .all()
            )

            logger.debug(f"  Found {len(latest_prices)} price snapshots for market.id={market.id}")

            if not latest_prices:
                logger.warning(f"  No price snapshots found for market {market.market_id} (internal id={market.id})")
                # Debug: check if there are any price snapshots at all
                total_prices = session.query(PriceSnapshot).count()
                logger.warning(f"  Total price snapshots in DB: {total_prices}")
                if total_prices > 0:
                    sample_price = session.query(PriceSnapshot).first()
                    logger.warning(f"  Sample price: market_id={sample_price.market_id}, outcome_id={sample_price.outcome_id}")
                return []

            # Group by outcome_id and get most recent
            price_by_outcome = {}
            for price in latest_prices:
                if price.outcome_id not in price_by_outcome:
                    price_by_outcome[price.outcome_id] = price

            logger.debug(f"  Grouped into {len(price_by_outcome)} unique outcomes")

            # Get outcomes
            outcomes = session.query(MarketOutcome).filter_by(market_id=market.id).all()

            logger.debug(f"  Found {len(outcomes)} market outcomes")
            if outcomes and len(outcomes) > 0:
                logger.debug(f"  Sample outcome: outcome_id={outcomes[0].outcome_id}, text={outcomes[0].outcome_text}")

            # Build bell curve data with bid/ask prices
            curve = []
            matched = 0
            unmatched = 0
            for outcome in outcomes:
                price_snapshot = price_by_outcome.get(str(outcome.outcome_id))
                if price_snapshot:
                    matched += 1
                    # Use bid/ask if available, otherwise use midpoint
                    bid_price = price_snapshot.bid if price_snapshot.bid is not None else price_snapshot.price
                    ask_price = price_snapshot.ask if price_snapshot.ask is not None else price_snapshot.price
                    midpoint = price_snapshot.price

                    curve.append({
                        'bracket': outcome.outcome_text,
                        'outcome_id': outcome.outcome_id,  # Token ID for trading
                        'min': outcome.min_tweets,
                        'max': outcome.max_tweets,
                        'price': midpoint,          # Midpoint for reference
                        'bid': bid_price,           # What you get when selling
                        'ask': ask_price,           # What you pay when buying
                        'spread': ask_price - bid_price,  # Transaction cost
                        'implied_prob': midpoint * 100,  # Convert to percentage
                        'market_title': market.title,
                        'market_id': market.market_id,
                    })
                else:
                    unmatched += 1
                    logger.debug(f"  No price for outcome_id={outcome.outcome_id} ({outcome.outcome_text})")

            logger.debug(f"  Matched {matched} outcomes with prices, {unmatched} unmatched")

            # Sort by min_tweets
            curve.sort(key=lambda x: x['min'] if x['min'] is not None else 0)

            logger.debug(f"  Returning {len(curve)} brackets for market {market.market_id}")
            return curve

    def calculate_prediction(self, market_id: str | None = None) -> Dict:
        """Return current distribution stats and summary metrics."""
        result = self._build_distribution(market_id=market_id)
        if result.get('error'):
            return result

        market = result['market']  # This is a dict
        distribution = result['distribution']
        velocity_snapshot = result['velocity']
        latest_point = result['latest_tweet']

        now = latest_point['timestamp']
        week_duration = (market['week_end'] - market['week_start']).total_seconds()
        elapsed = (now - market['week_start']).total_seconds()

        # Market progress: 0% if market hasn't started or we have no data yet
        # Only advance progress once the market period has actually begun
        current_count = distribution.current_count if distribution else 0
        if now < market['week_start'] or current_count == 0:
            market_progress = 0.0
        else:
            market_progress = min(max(elapsed / week_duration, 0), 1) if week_duration > 0 else 0

        tweets_per_hour = distribution.velocity_used
        days_elapsed = max(elapsed / 86400.0, 1e-3)
        tweets_per_day = distribution.current_count / days_elapsed
        days_remaining = distribution.remaining_hours / 24
        rolling_velocity = self._calculate_rolling_velocity(result.get('tweets'))

        ci_low, ci_high = distribution.intervals.get('90', (distribution.mean - distribution.sigma, distribution.mean + distribution.sigma))
        confidence_range = (ci_high - ci_low) / 2
        confidence_pct = (confidence_range / distribution.mean) if distribution.mean > 0 else 0

        return {
            'market_id': market['market_id'],
            'market_title': market['title'],
            'current_count': int(distribution.current_count),
            'current_time': latest_point['timestamp'],
            'market_progress': round(market_progress * 100, 1),  # % complete (0% if not started)
            'tweets_per_day': round(tweets_per_day, 2),
            'tweets_per_hour': round(tweets_per_hour, 2),
            'elapsed_days': round(elapsed / 86400, 2),
            'days_remaining': round(days_remaining, 2),
            'week_start': market['week_start'].isoformat() if hasattr(market['week_start'], 'isoformat') else str(market['week_start']),
            'week_end': market['week_end'].isoformat() if hasattr(market['week_end'], 'isoformat') else str(market['week_end']),
            'predicted_total': round(distribution.mean),
            'confidence_lower': round(ci_low),
            'confidence_upper': round(ci_high),
            'confidence_pct': round(confidence_pct * 100, 1),
            'mu': round(distribution.mean, 2),
            'sigma': round(distribution.sigma, 2),
            'velocity': rolling_velocity,
            'rolling_velocity': rolling_velocity,
            'velocity_snapshot': velocity_snapshot,
            'distribution': distribution.to_dict(),
        }

    def get_recommendations(self, bankroll: float = 1000.0, max_kelly: float = 0.25, current_positions: Dict = None, market_id: str | None = None) -> List[Dict]:
        """
        Build trade recommendations using enhanced trading logic.

        New rules:
        - Buy when edge > 6.7%
        - Sell when edge < -8%
        - Early week: 0.05% in lottery tickets (200-300 or 650+ tweets)
        - Hold likely winners even with small negative edge
        """
        if current_positions is None:
            current_positions = {}

        context = self._build_distribution(market_id=market_id)
        if context.get('error'):
            return []

        market = context['market']
        distribution = context['distribution']
        latest_point = context['latest_tweet']
        rolling_velocity = self._calculate_rolling_velocity(context.get('tweets', []))

        curve = self.get_market_bell_curve(market_id=market['market_id'])
        if not curve:
            logger.info(f"No market curve data for market {market['market_id']}")
            return []

        logger.debug(f"Evaluating {len(curve)} brackets for market {market['market_id']}")

        # Calculate market progress (0% if not started or no data)
        current_count = distribution.current_count
        now = latest_point['timestamp']
        if now < market['week_start'] or current_count == 0:
            market_progress = 0.0
        else:
            market_duration = (market['week_end'] - market['week_start']).total_seconds()
            elapsed = (now - market['week_start']).total_seconds()
            market_progress = min(max(elapsed / market_duration, 0), 1) if market_duration > 0 else 0
        predicted_total = distribution.mean
        std_dev = distribution.sigma

        logger.debug(
            "Market stats: progress=%s%%, predicted=%s, current=%s, sigma=%s",
            f"{market_progress*100:.1f}", f"{predicted_total:.0f}",
            current_count, f"{std_dev:.1f}"
        )

        recommendations: List[Dict] = []
        lottery_spend = 0.0

        duration_days = (market['week_end'] - market['week_start']).total_seconds() / 86400
        duration_ratio = duration_days / 7.0 if duration_days > 0 else 1.0
        velocity_spike = self._velocity_spike(distribution)

        for bracket in curve:
            min_t = bracket['min'] if bracket['min'] is not None else 0
            max_t = bracket['max'] if bracket['max'] is not None else 10000
            midpoint_price = bracket['price']
            bid_price = bracket['bid']  # Price when selling
            ask_price = bracket['ask']  # Price when buying
            spread = bracket['spread']
            outcome_id_str = str(bracket['outcome_id'])
            position_info = current_positions.get(outcome_id_str)
            shares_owned = position_info.get('shares', 0) if position_info else 0
            average_cost = position_info.get('average_cost') if position_info else None

            # Calculate probabilities
            our_prob = self.distribution_model.bracket_probability(distribution, bracket['min'], bracket['max'])

            # Use appropriate price for edge calculation:
            # - For sells: compare against bid (what you'd actually receive)
            # - For buys: compare against ask (what you'd actually pay)
            # Start by assuming we might buy (use ask)
            price_for_calculation = ask_price
            market_prob = price_for_calculation
            edge = our_prob - market_prob

            logger.debug(
                "  [%s-%s]: our_prob=%.2f%%, ask=%.2f%%, edge=%.2f%%, shares_owned=%s",
                min_t, max_t, our_prob*100, ask_price*100, edge*100, shares_owned
            )

            # Check if bracket is impossible (already passed)
            is_impossible = (max_t is not None and current_count > max_t) or (our_prob < 0.01 and midpoint_price < 0.01)
            if is_impossible and shares_owned > 0:
                logger.debug("    -> STOP LOSS (impossible bracket)")
                recommendations.append(self._stop_loss_rec(bracket, shares_owned, bid_price))
                continue
            elif is_impossible:
                logger.debug("    -> SKIP (impossible bracket)")
                continue  # Skip impossible brackets

            # Calculate Kelly fraction (using ask price for buys)
            if ask_price >= 1.0 or our_prob >= 1.0:
                kelly_fraction = 0
            else:
                kelly_fraction = max(0, (our_prob - ask_price) / (1 - ask_price))
                kelly_fraction = min(kelly_fraction, max_kelly)

            # Check if likely winner
            bracket_center = (min_t + max_t) / 2 if max_t is not None else min_t + 50
            is_likely_winner = (
                (min_t <= predicted_total <= max_t) or
                (abs(predicted_total - bracket_center) < std_dev * 1.5)
            )

            is_low_bracket = max_t < predicted_total - std_dev if max_t is not None else False
            is_high_bracket = min_t > predicted_total + std_dev if min_t is not None else False

            # Market duration scaling for lottery thresholds
            # Check if this is a lottery ticket opportunity (use ask price since we'd be buying)
            is_lottery = self.trading_logic.is_lottery_ticket(
                min_t, max_t, ask_price, market_progress, predicted_total, duration_ratio
            )

            logger.debug(f"    is_lottery={is_lottery}, is_likely_winner={is_likely_winner}, "
                        f"kelly_fraction={kelly_fraction:.4f}")

            # Decide action based on new trading rules
            action = "PASS"
            timing = "WATCH"
            position_size_usd = 0
            shares_to_sell = 0
            is_sell = False

            # Sell logic for existing positions
            if shares_owned > 0:
                # Recalculate edge using bid price (what we'd actually receive)
                sell_edge = our_prob - bid_price
                price_for_calculation = bid_price  # Use bid for all sell calculations

                market_prob = bracket.get('market_prob')
                acceleration = rolling_velocity.get('acceleration', 0.0) if rolling_velocity else 0.0
                should_sell, sell_action, sell_timing, shares_to_sell = self.trading_logic.should_sell(
                    sell_edge,
                    our_prob,
                    shares_owned,
                    market_progress,
                    is_likely_winner,
                    price_for_calculation,
                    average_cost,
                    velocity_spike,
                    market_prob,
                    acceleration,
                    is_low_bracket,
                    is_high_bracket,
                )
                if should_sell:
                    action = sell_action
                    timing = sell_timing
                    position_size_usd = shares_to_sell * bid_price  # Use bid price
                    is_sell = True
                    edge = sell_edge  # Update edge for display

            # Buy logic (uses ask price - already set as price_for_calculation)
            # Allow buying even if we own shares, as long as we're not selling
            if edge > 0 and not is_sell:
                # Edge is already calculated with ask price above
                if is_lottery:
                    if shares_owned == 0:
                        position_size_usd = self.trading_logic.calculate_position_size(
                            edge, ask_price, kelly_fraction, bankroll, is_lottery=True
                        )
                        max_spend = bankroll * self.trading_logic.t.lottery_max_allocation_pct
                        if lottery_spend + position_size_usd > max_spend:
                            position_size_usd = max(0, max_spend - lottery_spend)
                        lottery_spend += position_size_usd
                        action = "LOTTERY"
                        timing = "EARLY"
                        logger.debug("    -> LOTTERY TICKET: $%.2f", position_size_usd)
                    else:
                        logger.debug("    -> SKIP LOTTERY: already holding shares")
                else:
                    # Normal buy
                    should_buy, buy_action, buy_timing = self.trading_logic.should_buy(
                        edge, our_prob, ask_price, market_progress
                    )
                    if should_buy:
                        position_size_usd = self.trading_logic.calculate_position_size(
                            edge, ask_price, kelly_fraction, bankroll, is_lottery=False
                        )
                        action = buy_action
                        timing = buy_timing
                        logger.debug("    -> BUY: %s $%.2f", action, position_size_usd)
                    else:
                        logger.debug("    -> should_buy=False (edge=%.2f%%)", edge*100)

            # If we're holding a position (not selling, but not buying more), still show it
            # This allows dashboard to display HOLD recommendations
            if action == "PASS" and position_size_usd == 0 and shares_owned == 0:
                logger.debug("    -> SKIP: action=PASS, no position")
                continue

            # Calculate shares and metrics using the appropriate price
            # price_for_calculation is set above (ask for buys, bid for sells)
            shares = position_size_usd / price_for_calculation if price_for_calculation > 0 and not is_sell else 0
            expected_value = (our_prob * 1.0) - price_for_calculation
            roi_if_win = ((1.0 - price_for_calculation) / price_for_calculation) * 100 if price_for_calculation > 0 else 0
            position_size_pct = (position_size_usd / bankroll * 100) if bankroll > 0 else 0

            recommendation = {
                'market_id': market['market_id'],
                'market_title': market['title'],
                'bracket': bracket['bracket'],
                'outcome_id': bracket['outcome_id'],
                'min': min_t,
                'max': max_t,
                'market_price': price_for_calculation,  # Actual execution price (bid or ask)
                'midpoint_price': midpoint_price,       # Reference midpoint
                'bid': bid_price,                       # Best bid
                'ask': ask_price,                       # Best ask
                'spread': round(spread * 100, 3),       # Spread in percentage
                'market_prob': round(price_for_calculation * 100, 2),
                'our_prob': round(our_prob * 100, 2),
                'edge': round(edge * 100, 2),           # Edge after spread
                'ev': round(expected_value, 4),
                'kelly_fraction': round(kelly_fraction, 4),
                'kelly_pct': round(position_size_pct, 2),
                'position_size': round(position_size_usd, 2),
                'shares': round(shares if not is_sell else shares_to_sell, 2),
                'shares_owned': shares_owned,
                'average_cost': average_cost,
                'roi_if_win': round(roi_if_win, 1),
                'action': action,
                'timing': timing,
                'is_floor_share': is_lottery,
                'is_sell': is_sell,
                'distance_from_prediction': round(abs(predicted_total - bracket_center), 1),
                '_meta': {
                    'market_progress': round(market_progress * 100, 1),
                    'is_likely_winner': is_likely_winner,
                },
            }

            recommendations.append(recommendation)

        # Sort by edge (best opportunities first)
        recommendations.sort(key=lambda x: x['edge'], reverse=True)

        logger.debug("Generated %s recommendations for market %s", len(recommendations), market['market_id'])

        return recommendations

    def _get_global_velocity(self, session) -> Dict[str, Optional[float]]:
        """
        Get the most recent GLOBAL velocity snapshot.

        IMPORTANT: Velocity is a GLOBAL property - it's the same for all markets.
        This fetches the most recent velocity calculation across ALL data.
        """
        latest_point = (
            session.query(TweetData)
            .order_by(TweetData.timestamp.desc())
            .first()
        )

        if not latest_point:
            return {
                'velocity_1h': None,
                'velocity_6h': None,
                'velocity_24h': None,
                'acceleration': None,
            }

        return {
            'velocity_5m': latest_point.velocity_5m,
            'velocity_20m': latest_point.velocity_20m,
            'velocity_1h': latest_point.velocity_1h or latest_point.tweets_per_hour,
            'velocity_6h': latest_point.velocity_6h,
            'velocity_24h': latest_point.velocity_24h,
            'acceleration': latest_point.acceleration,
        }

    def _build_distribution(self, market_id: str | None = None) -> Dict:
        with self.db.get_session() as session:
            market = self._get_market(session, market_id)
            if not market:
                return {'error': 'No active market'}

            now = datetime.utcnow()

            # Check if market is in the future
            if now < market.week_start:
                # Pre-market: Use historical baselines only
                velocity_snapshot = {'velocity_1h': None, 'velocity_6h': None, 'velocity_24h': None}

                distribution = self.distribution_model.build(
                    market=market,
                    tweet_points=[],  # No data yet
                    velocity_snapshot=velocity_snapshot,
                    now=now,
                )

                if not distribution:
                    return {'error': 'Unable to build pre-market distribution'}

                market_meta = {
                    'market_id': market.market_id,
                    'title': market.title,
                    'week_start': market.week_start,
                    'week_end': market.week_end,
                }

                return {
                    'market': market_meta,
                    'tweets': [],
                    'latest_tweet': {
                        'timestamp': now,
                        'source': 'pre-market',
                        'cumulative_count': 0,
                    },
                    'velocity': velocity_snapshot,
                    'distribution': distribution,
                }

            # Get tweets for THIS market (to find current count for this market's week)
            tweets = (
                session.query(TweetData)
                .filter(
                    TweetData.week_start == market.week_start,
                    TweetData.timestamp >= market.week_start,
                )
                .order_by(TweetData.timestamp)
                .all()
            )

            if not tweets:
                return {'error': 'No tweet data collected yet'}

            if len(tweets) < 2:
                logger.warning(
                    "Market %s only has %s tweet data point(s); prediction will use limited history",
                    market.market_id,
                    len(tweets),
                )

            latest_point = tweets[-1]

            # IMPORTANT: Use GLOBAL velocity, not market-specific velocity
            # Velocity is a property of Elon's current tweeting rate, not the market
            velocity_snapshot = self._get_global_velocity(session)

            distribution = self.distribution_model.build(
                market=market,
                tweet_points=tweets,
                velocity_snapshot=velocity_snapshot,
                now=now,
            )

            if not distribution:
                return {'error': 'Unable to build distribution'}

            market_meta = {
                'market_id': market.market_id,
                'title': market.title,
                'week_start': market.week_start,
                'week_end': market.week_end,
            }

            tweet_snapshots = [
                {
                    'timestamp': t.timestamp,
                    'cumulative_count': t.cumulative_count,
                    'velocity_5m': t.velocity_5m,
                    'velocity_20m': t.velocity_20m,
                    'velocity_1h': t.velocity_1h,
                    'velocity_6h': t.velocity_6h,
                    'velocity_24h': t.velocity_24h,
                    'source': t.source,
                }
                for t in tweets
            ]

            return {
                'market': market_meta,
                'tweets': tweet_snapshots,
                'latest_tweet': {
                    'timestamp': latest_point.timestamp,
                    'source': latest_point.source,
                    'cumulative_count': latest_point.cumulative_count,
                },
                'velocity': velocity_snapshot,
                'distribution': distribution,
            }

    def _get_market(self, session, market_id: str | None):
        """
        Get a market by ID or select the most relevant currently active market.

        For multi-market support, we prioritize:
        1. Markets that are currently active (now is between week_start and week_end)
        2. Among active markets, pick the one ending soonest (most urgent)
        """
        if market_id:
            return session.query(PolymarketMarket).filter_by(market_id=market_id).first()

        # Get all currently active markets based on date
        now = datetime.utcnow()
        active_markets = (
            session.query(PolymarketMarket)
            .filter(
                PolymarketMarket.week_start <= now,
                PolymarketMarket.week_end >= now,
            )
            .order_by(PolymarketMarket.week_end.asc())  # Ending soonest first
            .all()
        )

        if active_markets:
            # Return the market ending soonest
            return active_markets[0]

        # Fallback: if no markets are currently active, get the most recent one
        return (
            session.query(PolymarketMarket)
            .filter_by(is_active=True)
            .order_by(PolymarketMarket.week_start.desc())
            .first()
        )

    def _stop_loss_rec(self, bracket: Dict, shares_owned: float, price: float) -> Dict:
        return {
            'bracket': bracket['bracket'],
            'market_title': bracket.get('market_title'),
            'market_id': bracket.get('market_id'),
            'outcome_id': bracket['outcome_id'],
            'min': bracket['min'],
            'max': bracket['max'],
            'market_price': price,
            'market_prob': round(price * 100, 2),
            'our_prob': 0.0,
            'edge': round(-price * 100, 2),
            'ev': -price,
            'kelly_fraction': 0,
            'kelly_pct': 0,
            'position_size': round(shares_owned * price, 2),
            'shares': shares_owned,
            'roi_if_win': 0,
            'action': 'STOP LOSS',
            'timing': 'NOW',
            'is_floor_share': False,
            'is_sell': True,
            'distance_from_prediction': 0,
            '_meta': {},
        }

    def _sell_logic(self, edge: float, shares_owned: float, price: float, market_progress: float, is_likely_winner: bool):
        sell_edge_pct = abs(edge) * 100
        if is_likely_winner and market_progress < 0.90:
            if price > 0.75:
                return 'TAKE PROFIT', 'NOW', shares_owned * 0.5
            return 'HOLD (LIKELY WINNER)', 'WATCH', 0
        if is_likely_winner and market_progress >= 0.90:
            if price > 0.50:
                return 'HOLD (FINAL STRETCH)', 'WATCH', 0
            return 'SELL (REASSESS)', 'NOW', shares_owned * 0.5

        if sell_edge_pct > 10:
            action = 'STRONG SELL'
            sell_fraction = 1.0
        elif sell_edge_pct > 5:
            action = 'SELL'
            sell_fraction = 0.75
        elif sell_edge_pct > 2:
            action = 'SMALL SELL'
            sell_fraction = 0.5
        else:
            action = 'HOLD'
            sell_fraction = 0

        timing = 'NOW' if (market_progress > 0.85 and sell_fraction > 0) or sell_edge_pct > 5 else 'WATCH'
        return action, timing, shares_owned * sell_fraction

    def _calculate_rolling_velocity(self, tweets: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Compute rolling tweets/hour over the last 1h/6h/24h using detached snapshots.

        When data collection skips a window (e.g., only one sample inside 24h),
        we fall back to the closest sample before that window boundary so
        the dashboard still reflects a reasonable average instead of zero.
        """
        windows = {
            'velocity_1h': 1,
            'velocity_6h': 6,
            'velocity_24h': 24,
        }
        result = {k: 0.0 for k in windows}
        if not tweets:
            result['acceleration'] = 0.0
            return result

        latest = tweets[-1]
        latest_ts = latest['timestamp']
        result['velocity_5m'] = latest.get('velocity_5m')
        result['velocity_20m'] = latest.get('velocity_20m')

        for label, hours in windows.items():
            cutoff = latest_ts - timedelta(hours=hours)
            candidates = [t for t in tweets if t['timestamp'] >= cutoff]

            if len(candidates) < 2:
                # Fall back to the closest point before the cutoff so we still
                # capture the best available average over a slightly larger window.
                anchor = next((t for t in reversed(tweets) if t['timestamp'] < cutoff), None)
                if anchor:
                    candidates = [anchor, tweets[-1]]
                else:
                    continue

            start = candidates[0]
            end = candidates[-1]
            elapsed = (end['timestamp'] - start['timestamp']).total_seconds() / 3600
            if elapsed <= 0:
                continue

            delta = end['cumulative_count'] - start['cumulative_count']
            if delta < 0:
                continue

            result[label] = round(delta / elapsed, 2)

            if label == 'velocity_24h':
                # Provide total tweets over (approximately) the last 24h for context.
                result['tweets_24h_total'] = round(delta, 1)

        def compute_minute_window(window_minutes: int) -> Optional[float]:
            cutoff = latest_ts - timedelta(minutes=window_minutes)
            candidates = [t for t in tweets if t['timestamp'] >= cutoff]
            if len(candidates) < 2:
                anchor = next((t for t in reversed(tweets) if t['timestamp'] < cutoff), None)
                if anchor:
                    candidates = [anchor, tweets[-1]]
                else:
                    return None
            start = candidates[0]
            end = candidates[-1]
            elapsed = (end['timestamp'] - start['timestamp']).total_seconds() / 3600
            if elapsed <= 0:
                return None
            delta = end['cumulative_count'] - start['cumulative_count']
            if delta < 0:
                return None
            return delta / elapsed

        for key, minutes in (('velocity_5m', 5), ('velocity_20m', 20)):
            if result.get(key) is None:
                computed = compute_minute_window(minutes)
                if computed is not None:
                    result[key] = round(computed, 2)

        result['acceleration'] = round(result['velocity_1h'] - result['velocity_6h'], 2)
        return result

    def _velocity_spike(self, distribution) -> bool:
        velocity = distribution.velocity_used
        return velocity > 2 * max(distribution.velocity_used, 1)
