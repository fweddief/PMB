"""
Bell-curve probabilistic modeling for tweet distributions.

The implementation plan calls for a Normal distribution whose mean is
`current_count + velocity * remaining_hours` with a dynamic standard
deviation derived from historical volatility and tweet storm detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import stats

from database import TweetData, PolymarketMarket


@dataclass
class DistributionResult:
    """Structured output for downstream consumers."""

    mean: float
    sigma: float
    remaining_hours: float
    current_count: int
    velocity_used: float
    intervals: Dict[str, Tuple[float, float]]

    def to_dict(self) -> Dict:
        return {
            "mean": self.mean,
            "sigma": self.sigma,
            "remaining_hours": self.remaining_hours,
            "current_count": self.current_count,
            "velocity_used": self.velocity_used,
            "intervals": self.intervals,
        }


class TweetDistributionModel:
    """Fits a Normal distribution to tweet activity."""

    def __init__(self, minimum_sigma: float = 5.0):
        self.minimum_sigma = minimum_sigma

    def build(
        self,
        market: PolymarketMarket,
        tweet_points: List[TweetData],
        velocity_snapshot: Dict[str, Optional[float]],
        now: Optional[datetime] = None,
    ) -> Optional[DistributionResult]:
        if not tweet_points:
            return None

        latest_point = tweet_points[-1]
        now = now or datetime.utcnow()
        remaining_seconds = max((market.week_end - now).total_seconds(), 0)
        remaining_hours = remaining_seconds / 3600

        velocity = (
            velocity_snapshot.get("velocity_6h")
            or velocity_snapshot.get("velocity_24h")
            or velocity_snapshot.get("velocity_1h")
        )

        if velocity is None:
            # Fall back to simple average velocity if rolling windows missing
            elapsed = (latest_point.timestamp - tweet_points[0].timestamp).total_seconds() / 3600
            velocity = (latest_point.cumulative_count - tweet_points[0].cumulative_count) / elapsed if elapsed > 0 else 0

        # Project forward using velocity (don't clamp to 0 - velocity can be legitimately low/negative)
        # Use max(0, velocity) only if velocity is significantly negative (data error)
        safe_velocity = velocity if velocity > -5 else 0
        mean = latest_point.cumulative_count + safe_velocity * remaining_hours

        sigma = self._estimate_sigma(tweet_points, velocity_snapshot, remaining_hours)
        sigma = max(sigma, self.minimum_sigma)

        # Build key interval summaries
        distribution = stats.norm(loc=mean, scale=sigma)
        intervals = {}
        for confidence in (0.68, 0.9, 0.95):
            low, high = distribution.interval(confidence)
            intervals[f"{int(confidence*100)}"] = (float(low), float(high))

        return DistributionResult(
            mean=float(mean),
            sigma=float(sigma),
            remaining_hours=float(remaining_hours),
            current_count=int(latest_point.cumulative_count),
            velocity_used=float(velocity),
            intervals=intervals,
        )

    def bracket_probability(
        self,
        distribution: DistributionResult,
        min_tweets: Optional[int],
        max_tweets: Optional[int],
    ) -> float:
        """Probability mass for a single outcome bracket."""
        norm = stats.norm(loc=distribution.mean, scale=distribution.sigma)
        lower = min_tweets or 0
        upper = max_tweets

        if upper is None:
            return float(1 - norm.cdf(lower))

        return float(norm.cdf(upper) - norm.cdf(lower))

    def _estimate_sigma(
        self,
        tweets: Iterable[TweetData],
        velocity_snapshot: Dict[str, Optional[float]],
        remaining_hours: float,
    ) -> float:
        """Use historical hourly volatility + acceleration heuristics."""
        hourly_velocities: List[float] = []
        tweets = list(tweets)

        for prev, curr in zip(tweets, tweets[1:]):
            hours = (curr.timestamp - prev.timestamp).total_seconds() / 3600
            if hours <= 0:
                continue
            delta = curr.cumulative_count - prev.cumulative_count
            # Skip only extreme outliers (likely data errors, not real tweet rates)
            # Elon can tweet 30-50+/hr during very active periods
            if delta < -10 or delta / hours > 200:  # Allow high but filter impossible rates
                continue
            hourly_velocities.append(delta / hours)

        volatility = np.std(hourly_velocities) if hourly_velocities else 3.0

        # Translate volatility to standard deviation of remaining tweets
        sigma = volatility * np.sqrt(max(remaining_hours, 1))

        # Increase uncertainty during tweet storms (spikes in 1h velocity)
        v1 = velocity_snapshot.get("velocity_1h") or 0
        v6 = velocity_snapshot.get("velocity_6h") or v1
        acceleration = velocity_snapshot.get("acceleration") or 0

        if v1 > 1.5 * (v6 or 1) or acceleration > 5:
            sigma *= 1.35

        return float(sigma)
