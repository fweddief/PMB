"""
Time-aware tweet prediction model based on Elon's historical patterns.

Key insights from 30-day historical data:
- Strong time-of-day patterns (Texas CST/CDT timezone)
- High variance during active hours (9am-3pm)
- Low/zero activity during sleep hours (12am-6am)
- Sigma should decrease as week progresses (more data = more confidence)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pytz
from scipy import stats

from database import TweetData, PolymarketMarket, PriceSnapshot, MarketOutcome, DatabaseManager

logger = logging.getLogger(__name__)

# Day-of-week × hourly baselines (tweets per hour)
# Rows: Monday(0) ... Sunday(6)
# Columns: hour 0-23 in Texas time
DAILY_HOURLY_BASELINES = [
    [6.0, 4.0, 3.0, 2.0, 1.8, 1.5, 2.0, 3.5, 5.0, 7.0, 8.5, 6.5, 6.0, 5.0, 4.0, 4.2, 3.8, 4.5, 5.0, 3.5, 2.5, 2.0, 2.2, 3.0],  # Mon
    [7.0, 5.0, 3.2, 2.5, 2.0, 1.7, 2.1, 3.2, 5.8, 8.0, 9.0, 7.2, 6.8, 5.4, 4.3, 4.8, 4.0, 4.6, 5.6, 3.6, 2.6, 2.2, 2.4, 3.2],  # Tue
    [6.5, 4.5, 3.1, 2.3, 1.9, 1.6, 2.1, 3.6, 6.1, 8.2, 9.4, 7.6, 6.9, 5.1, 4.0, 4.3, 4.1, 5.0, 5.7, 3.7, 2.3, 2.1, 2.3, 3.1],  # Wed
    [6.3, 4.3, 3.0, 2.2, 1.8, 1.5, 2.0, 3.4, 5.7, 7.5, 8.8, 6.6, 6.0, 5.0, 4.2, 4.4, 4.0, 4.8, 5.5, 3.3, 2.2, 2.0, 2.2, 3.0],  # Thu
    [5.5, 3.8, 2.6, 1.9, 1.6, 1.4, 1.8, 3.0, 5.0, 6.5, 7.5, 5.8, 5.3, 4.2, 3.6, 3.8, 3.4, 4.1, 4.8, 3.0, 2.1, 2.0, 2.1, 2.7],  # Fri
    [8.5, 6.2, 4.5, 3.2, 2.5, 2.0, 2.5, 4.0, 6.5, 8.5, 10.0, 8.2, 7.8, 6.5, 5.5, 6.0, 5.4, 6.2, 6.8, 4.2, 3.0, 2.5, 2.7, 3.5],  # Sat
    [7.5, 5.5, 4.0, 3.1, 2.4, 1.9, 2.2, 3.8, 5.8, 7.4, 8.6, 6.8, 6.2, 5.0, 4.2, 4.5, 4.1, 4.9, 5.6, 3.6, 2.4, 2.1, 2.3, 3.0],  # Sun
]

FLATTENED_BASELINES = [hour for day in DAILY_HOURLY_BASELINES for hour in day]
AVERAGE_BASELINE = sum(FLATTENED_BASELINES) / len(FLATTENED_BASELINES)

# Variance multipliers by hour (higher during unpredictable active hours)
HOURLY_VARIANCE = {
    0: 1.5,   # Late night can vary
    1: 0.8,   # Predictably low
    2: 0.8,
    3: 0.6,
    4: 0.6,
    5: 0.6,
    6: 1.0,
    7: 1.2,
    8: 1.5,
    9: 2.5,   # Morning peak - very unpredictable (0-27 range!)
    10: 2.8,  # Highest variance
    11: 2.5,
    12: 2.2,
    13: 1.8,
    14: 1.5,
    15: 1.6,
    16: 1.4,
    17: 1.3,
    18: 1.7,
    19: 1.4,
    20: 1.2,
    21: 1.0,
    22: 1.1,
    23: 1.3,
}


@dataclass
class TimeAwareDistribution:
    """Enhanced distribution with time-awareness."""

    mean: float
    sigma: float
    remaining_hours: float
    current_count: int
    velocity_used: float
    confidence_factor: float  # 0-1, how confident are we (based on data collected)
    intervals: Dict[str, Tuple[float, float]]
    time_adjusted: bool = True

    def to_dict(self) -> Dict:
        return {
            "mean": self.mean,
            "sigma": self.sigma,
            "remaining_hours": self.remaining_hours,
            "current_count": self.current_count,
            "velocity_used": self.velocity_used,
            "confidence_factor": self.confidence_factor,
            "intervals": self.intervals,
            "time_adjusted": self.time_adjusted,
        }


class TimeAwareTweetModel:
    """
    Prediction model incorporating:
    1. Time-of-day patterns (Texas timezone)
    2. Historical hourly baselines
    3. Decreasing uncertainty as week progresses
    4. Velocity weighted by expected activity
    """

    def __init__(self, texas_tz: str = "America/Chicago"):
        self.texas_tz = pytz.timezone(texas_tz)
        self.minimum_sigma = 5.0
        # Blend weights tuned from Jan 22-24 replay: 20% recent, 80% historic by default
        recent = float(os.getenv("VELOCITY_RECENT_WEIGHT", 0.2))
        self.recent_velocity_weight = min(max(recent, 0.1), 0.7)
        self.historical_velocity_weight = 1 - self.recent_velocity_weight
        self.market_mean_clamp = float(os.getenv("MARKET_MEAN_CLAMP", 40))
        self.market_mean_blend = float(os.getenv("MARKET_MEAN_BLEND", 0.5))
        self._db = DatabaseManager()

    def build(
        self,
        market: PolymarketMarket,
        tweet_points: List[TweetData],
        velocity_snapshot: Dict[str, Optional[float]],
        now: Optional[datetime] = None,
    ) -> Optional[TimeAwareDistribution]:
        """Build time-aware distribution."""
        now = now or datetime.utcnow()

        # Check if market hasn't started yet
        if now < market.week_start:
            return self._build_pre_market_prediction(market, now)

        if not tweet_points:
            return None

        latest_point = tweet_points[-1]
        now = now or datetime.utcnow()

        # Calculate time metrics
        total_duration = (market.week_end - market.week_start).total_seconds() / 3600
        elapsed_hours = (now - market.week_start).total_seconds() / 3600
        remaining_hours = max((market.week_end - now).total_seconds() / 3600, 0)

        # Market progress: 0% if market hasn't started or no data yet
        if not tweet_points or tweet_points[-1].cumulative_count == 0:
            market_progress = 0.0
        else:
            market_progress = elapsed_hours / total_duration if total_duration > 0 else 0

        # Get current hour in Texas time
        current_hour = now.astimezone(self.texas_tz).hour

        # Recent production totals (tweets over each lookback window)
        total_24h, span_24h = self._recent_window(tweet_points, 24)
        total_6h, span_6h = self._recent_window(tweet_points, 6)
        total_1h, span_1h = self._recent_window(tweet_points, 1)
        total_20m, span_20m = self._recent_window(tweet_points, 20 / 60)

        def projected_from_window(total: float, window_hours: float) -> float:
            if window_hours <= 0:
                return latest_point.cumulative_count
            periods_remaining = remaining_hours / window_hours
            return latest_point.cumulative_count + total * periods_remaining

        # Weighted prediction components
        progress = max(market_progress, 0.01)
        component_trend = latest_point.cumulative_count / progress
        component_24h = projected_from_window(total_24h, 24)
        component_6h = projected_from_window(total_6h, 6)
        component_1h = projected_from_window(total_1h, 1)
        component_20m = projected_from_window(total_20m, (20 / 60))

        mean = (
            0.65 * component_trend +
            0.20 * component_24h +
            0.10 * component_6h +
            0.04 * component_1h +
            0.01 * component_20m
        )

        # Blend with market-implied mean to prevent runaway projections
        market_mean = self._market_implied_mean(market)
        if market_mean is not None:
            mean = (1 - self.market_mean_blend) * mean + self.market_mean_blend * market_mean

        # Ensure prediction never drops below current tally
        mean = max(mean, latest_point.cumulative_count)

        # Effective velocity for reporting uses most recent available rate
        def rate(total: float, span_hours: float) -> float:
            return total / span_hours if span_hours > 0 else 0.0

        expected_velocity = (
            rate(total_1h, span_1h)
            or rate(total_6h, span_6h)
            or rate(total_24h, span_24h)
        )

        # Calculate adaptive sigma
        sigma = self._calculate_adaptive_sigma(
            tweet_points=tweet_points,
            velocity_snapshot=velocity_snapshot,
            remaining_hours=remaining_hours,
            market_progress=market_progress,
            current_hour=current_hour,
        )

        # Confidence increases with data collected and decreases with time remaining
        data_confidence = min(len(tweet_points) / 50, 1.0)  # Max confidence at 50+ points
        time_confidence = market_progress  # More confident as week progresses
        confidence_factor = (data_confidence + time_confidence) / 2

        # Build intervals
        distribution = stats.norm(loc=mean, scale=sigma)
        intervals = {}
        for confidence in (0.68, 0.9, 0.95):
            low, high = distribution.interval(confidence)
            intervals[f"{int(confidence*100)}"] = (float(max(0, low)), float(high))

        return TimeAwareDistribution(
            mean=float(mean),
            sigma=float(sigma),
            remaining_hours=float(remaining_hours),
            current_count=int(latest_point.cumulative_count),
            velocity_used=float(expected_velocity),
            confidence_factor=float(confidence_factor),
            intervals=intervals,
            time_adjusted=True,
        )

    def _calculate_expected_remaining(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> float:
        """
        Calculate expected tweets from start_time to end_time using hourly baselines.
        """
        expected = 0.0
        current = start_time

        while current < end_time:
            dow = current.weekday()
            hour = current.hour
            # Add baseline for this hour (pro-rated if partial hour)
            next_hour = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            next_hour = min(next_hour, end_time)

            fraction = (next_hour - current).total_seconds() / 3600
            expected += DAILY_HOURLY_BASELINES[dow][hour] * fraction

            current = next_hour

        return expected

    def _get_weighted_velocity(
        self,
        velocity_snapshot: Dict[str, Optional[float]],
        tweet_points: List[TweetData],
        market_progress: float,
    ) -> float:
        """Get velocity, weighted by current activity level with emphasis on recent data."""
        v5m = velocity_snapshot.get("velocity_5m")
        v20m = velocity_snapshot.get("velocity_20m")
        v1 = velocity_snapshot.get("velocity_1h")
        v6 = velocity_snapshot.get("velocity_6h")
        v24 = velocity_snapshot.get("velocity_24h")
        acceleration = velocity_snapshot.get("acceleration") or 0

        # Detect deceleration (slowing down) using 5-minute vs 20-minute velocity
        # Only trigger if there was actual activity that's slowing down (not just zero activity)
        is_decelerating = False
        if v5m is not None and v20m is not None and v20m > 2:
            # Deceleration is when 5m velocity is significantly below 20m velocity
            # Only if there was meaningful activity (v20m > 2 tweets/hour)
            is_decelerating = v5m < 0.4 * v20m and acceleration < -8
        elif v20m is not None and v1 is not None and v1 > 3:
            # Fallback: compare 20m vs 1h if 5m not available
            # Only if there was meaningful activity (v1 > 3 tweets/hour)
            is_decelerating = v20m < 0.5 * v1 and acceleration < -5
        elif v1 is not None and v6 is not None and v6 > 4:
            # Fallback: compare 1h vs 6h if neither 5m nor 20m available
            # Only if there was meaningful activity (v6 > 4 tweets/hour)
            is_decelerating = v1 < 0.5 * v6 and acceleration < -4

        # When decelerating, prioritize most recent velocity (5m) for immediate reaction
        # When accelerating or stable, use balanced approach
        recent_velocity = None
        if is_decelerating and v5m is not None:
            # Ultra-strong preference for 5m velocity during deceleration
            recent_velocity = v5m
            logger.info(f"Deceleration detected: using 5m velocity={v5m:.2f} (20m={v20m if v20m is not None else 0:.2f}, accel={acceleration:.2f})")
        elif is_decelerating and v20m is not None:
            # Strong preference for 20m velocity during deceleration (if 5m not available)
            recent_velocity = v20m
            logger.info(f"Deceleration detected: using 20m velocity={v20m:.2f} (1h={v1 if v1 is not None else 0:.2f}, accel={acceleration:.2f})")
        elif is_decelerating and v1 is not None:
            # Fall back to 1h velocity during deceleration
            recent_velocity = v1
            logger.info(f"Deceleration detected: using 1h velocity={v1:.2f} (6h={v6 if v6 is not None else 0:.2f}, accel={acceleration:.2f})")
        elif v5m is not None and v20m is not None:
            # Weighted average heavily favoring recent data
            # 80% weight on 5m, 20% on 20m for ultra-fast responsiveness
            recent_velocity = 0.8 * v5m + 0.2 * v20m
        elif v20m is not None and v1 is not None:
            # Weighted average favoring recent data
            # 70% weight on 20m, 30% on 1h for fast responsiveness
            recent_velocity = 0.7 * v20m + 0.3 * v1
        elif v1 is not None and v6 is not None:
            # Weighted average favoring recent data
            # 60% weight on 1h, 40% on 6h for better responsiveness
            recent_velocity = 0.6 * v1 + 0.4 * v6
        else:
            # Fallback: prefer most recent available
            recent_velocity = v5m or v20m or v1 or v6 or v24

        if recent_velocity is None:
            # Fallback: calculate from data points
            if len(tweet_points) >= 2:
                latest = tweet_points[-1]
                earliest = tweet_points[0]
                hours = (latest.timestamp - earliest.timestamp).total_seconds() / 3600
                if hours > 0:
                    recent_velocity = (latest.cumulative_count - earliest.cumulative_count) / hours
                else:
                    recent_velocity = 5.0
            else:
                recent_velocity = 5.0

        # Historical baseline based on longer windows (6h/24h/entire week)
        historical_velocity = v6 or v24
        if historical_velocity is None and len(tweet_points) >= 4:
            earliest = tweet_points[max(0, len(tweet_points) - 4)]
            latest = tweet_points[-1]
            hours = (latest.timestamp - earliest.timestamp).total_seconds() / 3600
            if hours > 0:
                historical_velocity = (latest.cumulative_count - earliest.cumulative_count) / hours
        if historical_velocity is None:
            historical_velocity = recent_velocity

        # Dynamic weighting: once the market is well underway or we have dense data,
        # lean heavily on observed velocity instead of historical baselines.
        data_confidence = min(len(tweet_points) / 250.0, 1.0)
        progress_confidence = min(max(market_progress - 0.35, 0.0) / 0.65, 1.0)
        recent_weight = max(data_confidence, progress_confidence, 0.35)
        recent_weight = min(recent_weight + 0.2 if market_progress > 0.65 else recent_weight, 0.95)
        blended = recent_weight * recent_velocity + (1 - recent_weight) * historical_velocity
        return max(blended, 0.1)


    def _market_implied_mean(self, market: PolymarketMarket) -> Optional[float]:
        with self._db.get_session() as session:
            outcome_rows = (
                session.query(
                    MarketOutcome.outcome_id,
                    MarketOutcome.min_tweets,
                    MarketOutcome.max_tweets,
                )
                .filter(MarketOutcome.market_id == market.id)
                .all()
            )
            if not outcome_rows:
                return None

            price_rows = (
                session.query(PriceSnapshot.outcome_id, PriceSnapshot.price)
                .filter(PriceSnapshot.market_id == market.id)
                .order_by(PriceSnapshot.timestamp.desc())
                .all()
            )

        latest_prices = {}
        for outcome_id, price in price_rows:
            if price is None:
                continue
            latest_prices.setdefault(outcome_id, float(price))

        total = 0.0
        weight = 0.0
        for outcome_id, min_t, max_t in outcome_rows:
            price = latest_prices.get(outcome_id)
            if price is None or price <= 0:
                continue
            if min_t is None and max_t is None:
                continue
            if min_t is None:
                mid = max_t - 25
            elif max_t is None:
                mid = min_t + 25
            else:
                mid = (min_t + max_t) / 2
            total += price * mid
            weight += price

        if weight == 0:
            return None

        return total / weight

    def _calculate_adaptive_sigma(
        self,
        tweet_points: List[TweetData],
        velocity_snapshot: Dict[str, Optional[float]],
        remaining_hours: float,
        market_progress: float,
        current_hour: int,
    ) -> float:
        """
        Calculate sigma that decreases as week progresses and data accumulates.
        """
        # Base uncertainty from historical patterns
        hourly_variance_mult = HOURLY_VARIANCE.get(current_hour, 1.5)

        # Calculate observed volatility from recent data
        hourly_velocities = []
        for i in range(1, len(tweet_points)):
            prev, curr = tweet_points[i-1], tweet_points[i]
            hours = (curr.timestamp - prev.timestamp).total_seconds() / 3600
            if hours > 0:
                delta = curr.cumulative_count - prev.cumulative_count
                # Filter extreme outliers
                if -10 < delta < 200 * hours:
                    hourly_velocities.append(delta / hours)

        observed_volatility = np.std(hourly_velocities) if len(hourly_velocities) >= 3 else 8.0

        # Early in week: rely more on historical variance
        # Late in week: rely more on observed variance
        historical_volatility = 8.0 * hourly_variance_mult
        blended_volatility = (
            (1 - market_progress) * historical_volatility +
            market_progress * observed_volatility
        )

        # Sigma decreases as we have more data (sqrt scaling)
        # Also decreases as less time remains (less opportunity for deviation)
        data_points = len(tweet_points)
        data_factor = np.sqrt(max(50 - data_points, 10) / 50)  # Decreases from 1.0 to 0.45
        time_factor = np.sqrt(remaining_hours / max(remaining_hours + 1, 1))

        # Base sigma from volatility and time
        sigma = blended_volatility * np.sqrt(max(remaining_hours, 1))

        # Apply confidence adjustments
        sigma *= data_factor  # Less uncertain with more data

        # Minimum floor
        sigma = max(sigma, self.minimum_sigma)

        # Check for velocity changes (both acceleration and deceleration)
        v5m = velocity_snapshot.get("velocity_5m") or 0
        v20m = velocity_snapshot.get("velocity_20m") or 0
        v1 = velocity_snapshot.get("velocity_1h") or 0
        v6 = velocity_snapshot.get("velocity_6h") or v1
        acceleration = velocity_snapshot.get("acceleration") or 0

        # Detect deceleration (slowing down) - only when there was actual activity
        is_decelerating = False
        if v5m is not None and v20m is not None and v20m > 2:
            is_decelerating = v5m < 0.4 * v20m and acceleration < -8
        elif v20m is not None and v1 is not None and v1 > 3:
            is_decelerating = v20m < 0.5 * v1 and acceleration < -5
        elif v1 is not None and v6 is not None and v6 > 4:
            is_decelerating = v1 < 0.5 * v6 and acceleration < -4

        # Detect tweet storms (recent spike) - check 5m vs 20m first
        if v5m > 0 and v20m > 0 and v5m > 2.5 * v20m and acceleration > 12:
            sigma *= 1.30  # Increase uncertainty during volatile periods
            logger.debug(f"Tweet storm detected: v5m={v5m:.2f}, v20m={v20m:.2f}, accel={acceleration:.2f}")
        elif v20m > 0 and v1 > 0 and v20m > 2.0 * v1 and acceleration > 10:
            sigma *= 1.25  # Increase uncertainty during volatile periods
            logger.debug(f"Tweet storm detected: v20m={v20m:.2f}, v1={v1:.2f}, accel={acceleration:.2f}")
        elif v1 > 0 and v6 > 0 and v1 > 2.0 * v6 and acceleration > 8:
            sigma *= 1.20  # Increase uncertainty during volatile periods
            logger.debug(f"Tweet storm detected: v1={v1:.2f}, v6={v6:.2f}, accel={acceleration:.2f}")

        # Apply deceleration adjustment
        elif is_decelerating:
            # Deceleration increases uncertainty moderately
            sigma *= 1.35  # Moderate increase during slowdowns
            logger.info(f"Deceleration detected: v5m={v5m}, v20m={v20m}, v1={v1:.2f}, v6={v6}, accel={acceleration:.2f}, increasing sigma to {sigma:.2f}")

        return float(sigma)

    def _build_pre_market_prediction(
        self,
        market: PolymarketMarket,
        now: datetime,
    ) -> TimeAwareDistribution:
        """
        Build prediction for a market that hasn't started yet.
        Uses purely historical baselines since we have no data yet.
        """
        # Calculate total hours in the market period
        start_texas = market.week_start.astimezone(self.texas_tz)
        end_texas = market.week_end.astimezone(self.texas_tz)

        total_hours = (market.week_end - market.week_start).total_seconds() / 3600

        # Calculate expected tweets based on historical hour baselines
        expected_total = self._calculate_expected_remaining(start_texas, end_texas)

        # For pre-market, sigma is larger (more uncertainty)
        # Use historical variance across the entire period
        avg_variance_mult = sum(HOURLY_VARIANCE.values()) / len(HOURLY_VARIANCE)
        base_volatility = 8.0 * avg_variance_mult

        # Sigma based on full duration (maximum uncertainty)
        sigma = base_volatility * np.sqrt(total_hours)

        # Apply multiplier for pre-market uncertainty
        sigma *= 1.5  # 50% more uncertain when market hasn't started

        # Build intervals
        distribution = stats.norm(loc=expected_total, scale=sigma)
        intervals = {}
        for confidence in (0.68, 0.9, 0.95):
            low, high = distribution.interval(confidence)
            intervals[f"{int(confidence*100)}"] = (float(max(0, low)), float(high))

        # Calculate average velocity from historical baseline
        avg_baseline = AVERAGE_BASELINE

        return TimeAwareDistribution(
            mean=float(expected_total),
            sigma=float(sigma),
            remaining_hours=float(total_hours),
            current_count=0,  # Market hasn't started
            velocity_used=float(avg_baseline),
            confidence_factor=0.3,  # Low confidence - no data yet
            intervals=intervals,
            time_adjusted=True,
        )

    def _recent_window(
        self,
        tweet_points: List[TweetData],
        window_hours: float,
    ) -> Tuple[float, float]:
        """
        Return (tweet_total, hours_spanned) for the specified lookback window.
        Falls back to the closest historical point if the window lacks data.
        """
        if not tweet_points or window_hours <= 0:
            return 0.0, 0.0

        latest = tweet_points[-1]
        cutoff = latest.timestamp - timedelta(hours=window_hours)
        candidates = [point for point in tweet_points if point.timestamp >= cutoff]

        if len(candidates) < 2:
            anchor = next((p for p in reversed(tweet_points) if p.timestamp < cutoff), None)
            if anchor:
                candidates.insert(0, anchor)

        if len(candidates) < 2:
            return 0.0, 0.0

        start = candidates[0]
        end = candidates[-1]
        hours = (end.timestamp - start.timestamp).total_seconds() / 3600
        if hours <= 0:
            return 0.0, 0.0

        delta = end.cumulative_count - start.cumulative_count
        return max(delta, 0.0), hours

    def bracket_probability(
        self,
        distribution: TimeAwareDistribution,
        min_tweets: Optional[int],
        max_tweets: Optional[int],
    ) -> float:
        """Calculate probability for a bracket."""
        norm = stats.norm(loc=distribution.mean, scale=distribution.sigma)
        lower = min_tweets or 0
        upper = max_tweets

        if upper is None:
            return float(1 - norm.cdf(lower))

        return float(norm.cdf(upper) - norm.cdf(lower))
