"""
Tweet estimator wrapping the existing TimeAwareTweetModel into the BaseEstimator interface.

Preserves all existing velocity-based forecasting logic from the original bot.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from .base_estimator import BaseEstimator, FairValueEstimate, MarketEstimate

logger = logging.getLogger(__name__)


class TweetEstimator(BaseEstimator):
    """
    Wraps the existing TimeAwareTweetModel into the BaseEstimator interface.

    This estimator handles Elon Musk tweet count markets and any similar
    social media posting frequency markets.
    """

    def __init__(self):
        # Lazy import to avoid circular dependencies
        self._model = None
        self._db = None

    def _ensure_model(self):
        if self._model is None:
            from services.time_aware_model import TimeAwareTweetModel
            from database import DatabaseManager
            self._model = TimeAwareTweetModel()
            self._db = DatabaseManager()

    def category(self) -> str:
        return "tweets"

    def confidence_threshold(self) -> float:
        return 0.25

    def can_estimate(self, title: str, description: str = "") -> bool:
        text = f"{title} {description}".lower()
        tweet_terms = [
            "tweets", "tweet", "elon musk", "x posts",
            "number of tweets", "tweet count", "how many tweets",
        ]
        return any(term in text for term in tweet_terms)

    def estimate(self, market_data: Dict) -> Optional[MarketEstimate]:
        """
        Use the existing TimeAwareTweetModel to estimate tweet bracket probabilities.

        This bridges the scanner world (market_data dict) with the existing
        model world (SQLAlchemy objects + velocity snapshots).
        """
        self._ensure_model()

        market_id = market_data.get("market_id", "")

        try:
            from database import TweetData, PolymarketMarket, MarketOutcome, PriceSnapshot
            from sqlalchemy import desc

            with self._db.get_session() as session:
                # Find the market in the DB
                market = (
                    session.query(PolymarketMarket)
                    .filter_by(market_id=market_id)
                    .first()
                )

                if not market:
                    logger.debug("Tweet market %s not found in DB", market_id)
                    return None

                now = datetime.utcnow()

                # Get tweet data for this market's time window
                tweets = (
                    session.query(TweetData)
                    .filter(
                        TweetData.week_start == market.week_start,
                        TweetData.timestamp >= market.week_start,
                    )
                    .order_by(TweetData.timestamp)
                    .all()
                )

                # Get global velocity
                latest_point = (
                    session.query(TweetData)
                    .order_by(TweetData.timestamp.desc())
                    .first()
                )

                velocity_snapshot = {
                    "velocity_5m": latest_point.velocity_5m if latest_point else None,
                    "velocity_20m": latest_point.velocity_20m if latest_point else None,
                    "velocity_1h": (latest_point.velocity_1h or latest_point.tweets_per_hour) if latest_point else None,
                    "velocity_6h": latest_point.velocity_6h if latest_point else None,
                    "velocity_24h": latest_point.velocity_24h if latest_point else None,
                    "acceleration": latest_point.acceleration if latest_point else None,
                }

                # Build distribution
                distribution = self._model.build(
                    market=market,
                    tweet_points=tweets if tweets else [],
                    velocity_snapshot=velocity_snapshot,
                    now=now,
                )

                if not distribution:
                    return None

                # Get outcomes for this market
                outcomes = (
                    session.query(MarketOutcome)
                    .filter_by(market_id=market.id)
                    .all()
                )

                # Calculate confidence based on data availability
                if not tweets:
                    confidence = 0.3  # Pre-market
                else:
                    total_duration = (market.week_end - market.week_start).total_seconds() / 3600
                    elapsed = (now - market.week_start).total_seconds() / 3600
                    progress = elapsed / total_duration if total_duration > 0 else 0
                    data_conf = min(len(tweets) / 50, 1.0)
                    confidence = (data_conf + progress) / 2

                # Build estimates for each outcome/bracket
                estimates = []
                for outcome in outcomes:
                    prob = self._model.bracket_probability(
                        distribution, outcome.min_tweets, outcome.max_tweets,
                    )
                    prob = max(0.001, min(0.999, prob))

                    estimates.append(FairValueEstimate(
                        outcome_id=outcome.outcome_id,
                        outcome_text=outcome.outcome_text,
                        fair_probability=prob,
                        confidence=confidence,
                        reasoning=f"TimeAwareModel: mean={distribution.mean:.0f}, "
                                  f"sigma={distribution.sigma:.1f}, "
                                  f"count={distribution.current_count}, "
                                  f"v={distribution.velocity_used:.1f}/hr. "
                                  f"P({outcome.outcome_text})={prob:.3f}",
                        data_sources=["TimeAwareTweetModel", "xtracker"],
                    ))

                # Also map estimates for outcomes from scanner data (if not in DB)
                scanner_outcomes = market_data.get("outcomes", [])
                existing_ids = {e.outcome_id for e in estimates}
                for outcome in scanner_outcomes:
                    oid = outcome.get("outcome_id", "")
                    if oid in existing_ids:
                        continue

                    outcome_text = outcome.get("outcome_text", "")
                    from scrapers.polymarket_scraper import PolymarketScraper
                    scraper = PolymarketScraper()
                    min_t, max_t = scraper.parse_tweet_range_from_outcome(outcome_text)

                    prob = self._model.bracket_probability(distribution, min_t, max_t)
                    prob = max(0.001, min(0.999, prob))

                    estimates.append(FairValueEstimate(
                        outcome_id=oid,
                        outcome_text=outcome_text,
                        fair_probability=prob,
                        confidence=confidence,
                        reasoning=f"TimeAwareModel: mean={distribution.mean:.0f}, "
                                  f"sigma={distribution.sigma:.1f}. "
                                  f"P({outcome_text})={prob:.3f}",
                        data_sources=["TimeAwareTweetModel"],
                    ))

                if not estimates:
                    return None

                return MarketEstimate(
                    market_id=market_id,
                    category="tweets",
                    estimates=estimates,
                    external_data={
                        "mean": distribution.mean,
                        "sigma": distribution.sigma,
                        "current_count": distribution.current_count,
                        "velocity": distribution.velocity_used,
                        "remaining_hours": distribution.remaining_hours,
                        "confidence_factor": distribution.confidence_factor,
                    },
                )

        except Exception as e:
            logger.error("Tweet estimator error for %s: %s", market_id, e, exc_info=True)
            return None
