"""
Multi-market monitor: routes scanned markets to appropriate estimators,
calculates edge, and filters for tradeable opportunities.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from database import DatabaseManager
from database.scanner_schema import ScannedMarket, EstimatorResult, Opportunity
from estimators.base_estimator import BaseEstimator, MarketEstimate
from estimators.weather_estimator import WeatherEstimator
from estimators.sports_estimator import SportsEstimator
from estimators.crypto_estimator import CryptoEstimator
from estimators.tweet_estimator import TweetEstimator
from scanner.market_classifier import MarketClassifier

logger = logging.getLogger(__name__)

# Minimum edge to generate an opportunity (configurable via env)
import os
MIN_EDGE_PERCENT = float(os.getenv("MIN_EDGE_PERCENT", "8")) / 100
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "500"))


class MultiMarketMonitor:
    """
    Takes scanned + classified markets, routes to appropriate estimators,
    calculates edge, and returns filtered opportunities.
    """

    def __init__(self, db_manager: DatabaseManager = None, rate_limiter=None):
        self.db = db_manager or DatabaseManager()
        self.classifier = MarketClassifier()

        # Initialize all estimators (pass rate limiter to those that need it)
        self._estimators: Dict[str, BaseEstimator] = {
            "weather": WeatherEstimator(),
            "sports": SportsEstimator(),
            "crypto": CryptoEstimator(rate_limiter=rate_limiter),
            "tweets": TweetEstimator(),
        }

    def process_markets(self, markets: List[Dict]) -> List[Dict]:
        """
        Process a batch of scanned markets:
        1. Classify each market
        2. Route to appropriate estimator
        3. Calculate edge for each outcome
        4. Filter by edge threshold, confidence, and liquidity
        5. Store results in DB
        6. Return opportunities sorted by edge

        Args:
            markets: List of market dicts from MarketScanner.scan_all_markets()

        Returns:
            List of opportunity dicts sorted by edge.
        """
        # Step 1: Classify
        self.classifier.classify_batch(markets)

        opportunities = []
        estimated_count = 0
        skipped_count = 0

        for market in markets:
            category = market.get("category", "unknown")
            estimator = self._estimators.get(category)

            if not estimator:
                skipped_count += 1
                continue

            title = market.get("title", "")
            description = market.get("description", "")

            if not estimator.can_estimate(title, description):
                skipped_count += 1
                continue

            # Step 2: Estimate
            try:
                estimate = estimator.estimate(market)
            except Exception as e:
                logger.warning("Estimator error for %s: %s", market.get("market_id"), e)
                continue

            if not estimate:
                skipped_count += 1
                continue

            estimated_count += 1

            # Step 3: Calculate edge and filter
            market_opps = self._evaluate_estimate(market, estimate)
            opportunities.extend(market_opps)

            # Store in DB
            self._store_results(market, estimate)

        logger.info(
            "Processed %d markets: %d estimated, %d skipped, %d opportunities found",
            len(markets), estimated_count, skipped_count, len(opportunities),
        )

        # Sort by absolute edge descending
        opportunities.sort(key=lambda x: abs(x.get("edge", 0)), reverse=True)
        return opportunities

    def refresh_opportunities(self, tracked_markets: List[Dict], price_updates: Dict[str, List[Dict]]) -> List[Dict]:
        """
        Quick price refresh for tracked markets. Recalculate edge using
        latest prices without re-running full estimation.

        Args:
            tracked_markets: Markets we're currently tracking.
            price_updates: Dict mapping market_id -> list of outcome price dicts.

        Returns:
            Updated opportunities with recalculated edges.
        """
        updated_opps = []

        with self.db.get_session() as session:
            for market in tracked_markets:
                market_id = market.get("market_id", "")
                prices = price_updates.get(market_id, [])
                if not prices:
                    continue

                # Get latest estimates from DB
                results = (
                    session.query(EstimatorResult)
                    .filter(EstimatorResult.market_id == market_id)
                    .order_by(EstimatorResult.timestamp.desc())
                    .all()
                )

                # Build lookup by outcome_id (latest only)
                latest_estimates = {}
                for r in results:
                    if r.outcome_id not in latest_estimates:
                        latest_estimates[r.outcome_id] = r

                for price_info in prices:
                    outcome_id = price_info.get("outcome_id", "")
                    new_price = price_info.get("price", 0)

                    est = latest_estimates.get(outcome_id)
                    if not est:
                        continue

                    new_edge = est.fair_probability - new_price

                    if abs(new_edge) >= MIN_EDGE_PERCENT and est.confidence >= self._estimators.get(
                        market.get("category", ""), self._estimators.get("crypto", CryptoEstimator())
                    ).confidence_threshold():
                        updated_opps.append({
                            "market_id": market_id,
                            "outcome_id": outcome_id,
                            "market_title": market.get("title", ""),
                            "category": market.get("category", "unknown"),
                            "market_price": new_price,
                            "fair_value": est.fair_probability,
                            "edge": new_edge,
                            "confidence": est.confidence,
                            "liquidity": market.get("total_liquidity", 0),
                            "reasoning": est.reasoning,
                        })

        updated_opps.sort(key=lambda x: abs(x.get("edge", 0)), reverse=True)
        return updated_opps

    def _evaluate_estimate(self, market: Dict, estimate: MarketEstimate) -> List[Dict]:
        """Evaluate an estimate against current market prices and filter by thresholds."""
        opportunities = []
        category = estimate.category

        estimator = self._estimators.get(category)
        conf_threshold = estimator.confidence_threshold() if estimator else 0.3
        liquidity = market.get("total_liquidity", 0)

        for est in estimate.estimates:
            # Find matching outcome for current price
            outcome_price = 0.5
            for outcome in market.get("outcomes", []):
                if str(outcome.get("outcome_id")) == str(est.outcome_id):
                    outcome_price = outcome.get("price", 0.5)
                    break

            edge = est.fair_probability - outcome_price

            # Filter: edge threshold, confidence, liquidity
            if abs(edge) < MIN_EDGE_PERCENT:
                continue
            if est.confidence < conf_threshold:
                continue
            if liquidity < MIN_LIQUIDITY:
                continue

            opportunities.append({
                "market_id": estimate.market_id,
                "outcome_id": est.outcome_id,
                "outcome_text": est.outcome_text,
                "market_title": market.get("title", ""),
                "category": category,
                "market_price": outcome_price,
                "fair_value": est.fair_probability,
                "edge": edge,
                "confidence": est.confidence,
                "liquidity": liquidity,
                "reasoning": est.reasoning,
                "data_sources": est.data_sources,
            })

        return opportunities

    def _store_results(self, market: Dict, estimate: MarketEstimate) -> None:
        """Store scan results and estimates in the database."""
        try:
            with self.db.get_session() as session:
                # Upsert ScannedMarket
                scanned = session.query(ScannedMarket).filter_by(
                    market_id=market.get("market_id", "")
                ).first()

                best_edge = 0.0
                for est in estimate.estimates:
                    for outcome in market.get("outcomes", []):
                        if str(outcome.get("outcome_id")) == str(est.outcome_id):
                            edge = abs(est.fair_probability - outcome.get("price", 0.5))
                            best_edge = max(best_edge, edge)

                if scanned:
                    scanned.category = estimate.category
                    scanned.category_confidence = market.get("category_confidence")
                    scanned.liquidity = market.get("total_liquidity", 0)
                    scanned.volume_24h = market.get("total_volume_24h", 0)
                    scanned.best_edge = best_edge
                    scanned.last_scanned = datetime.utcnow()
                    scanned.last_estimated = datetime.utcnow()
                else:
                    scanned = ScannedMarket(
                        market_id=market.get("market_id", ""),
                        title=market.get("title", ""),
                        description=market.get("description", ""),
                        slug=market.get("slug", ""),
                        category=estimate.category,
                        category_confidence=market.get("category_confidence"),
                        liquidity=market.get("total_liquidity", 0),
                        volume_24h=market.get("total_volume_24h", 0),
                        best_edge=best_edge,
                        num_outcomes=len(market.get("outcomes", [])),
                        end_date=market.get("end_date", ""),
                        start_date=market.get("start_date", ""),
                    )
                    session.add(scanned)

                # Store estimator results
                for est in estimate.estimates:
                    outcome_price = 0.5
                    for outcome in market.get("outcomes", []):
                        if str(outcome.get("outcome_id")) == str(est.outcome_id):
                            outcome_price = outcome.get("price", 0.5)
                            break

                    result = EstimatorResult(
                        market_id=market.get("market_id", ""),
                        outcome_id=est.outcome_id,
                        estimator_name=estimate.category,
                        category=estimate.category,
                        fair_probability=est.fair_probability,
                        market_price=outcome_price,
                        edge=est.fair_probability - outcome_price,
                        confidence=est.confidence,
                        reasoning=est.reasoning,
                        external_data=json.dumps(estimate.external_data) if estimate.external_data else None,
                    )
                    session.add(result)

                session.commit()

        except Exception as e:
            logger.error("Failed to store results for %s: %s", market.get("market_id"), e)

    def get_all_opportunities(self, status: str = "pending") -> List[Dict]:
        """Get all stored opportunities with a given status."""
        with self.db.get_session() as session:
            opps = (
                session.query(Opportunity)
                .filter(Opportunity.status == status)
                .order_by(Opportunity.edge.desc())
                .all()
            )
            return [
                {
                    "id": o.id,
                    "market_id": o.market_id,
                    "outcome_id": o.outcome_id,
                    "market_title": o.market_title,
                    "category": o.category,
                    "market_price": o.market_price,
                    "fair_value": o.fair_value,
                    "edge": o.edge,
                    "confidence": o.confidence,
                    "kelly_fraction": o.kelly_fraction,
                    "position_size_usd": o.position_size_usd,
                    "score": o.score,
                    "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                }
                for o in opps
            ]

    def get_category_stats(self) -> List[Dict]:
        """Get performance stats for each category."""
        from database.scanner_schema import CategoryPerformance
        with self.db.get_session() as session:
            perfs = session.query(CategoryPerformance).all()
            return [
                {
                    "category": p.category,
                    "total_trades": p.total_trades,
                    "wins": p.wins,
                    "losses": p.losses,
                    "total_pnl": p.total_pnl,
                    "roi_pct": p.roi_pct,
                    "hit_rate": p.hit_rate,
                    "avg_edge": p.avg_edge,
                }
                for p in perfs
            ]
