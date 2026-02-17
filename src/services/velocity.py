"""
Utility helpers for calculating tweet velocity metrics.

The ingestion plan requires rolling 1h/6h/24h velocities plus
acceleration (change in velocity). This module centralizes the math so
collectors and monitoring code can stay lean.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from database import TweetData


@dataclass
class VelocitySnapshot:
    """Container for rolling velocity metrics."""

    velocity_5m: Optional[float] = None   # 5-minute velocity for ultra-fast reaction
    velocity_20m: Optional[float] = None  # 20-minute velocity for fast reaction
    velocity_1h: Optional[float] = None
    velocity_6h: Optional[float] = None
    velocity_24h: Optional[float] = None
    acceleration: Optional[float] = None

    def as_dict(self) -> Dict[str, Optional[float]]:
        return {
            "velocity_5m": self.velocity_5m,
            "velocity_20m": self.velocity_20m,
            "velocity_1h": self.velocity_1h,
            "velocity_6h": self.velocity_6h,
            "velocity_24h": self.velocity_24h,
            "acceleration": self.acceleration,
        }


class VelocityCalculator:
    """Calculates rolling velocity windows from tweet samples."""

    WINDOWS = (1, 6, 24)
    WINDOWS_MINUTES = (5, 20)  # 5-minute and 20-minute windows for fast reaction

    def __init__(self, minimum_points: int = 2):
        self.minimum_points = minimum_points

    def calculate(
        self,
        session: Session,
        week_start: datetime,
        latest_timestamp: datetime,
        latest_count: int,
    ) -> VelocitySnapshot:
        """
        Compute rolling velocities for a specific market.

        Since each market's tweet count is normalized to its own time period,
        we must only compare data points from the SAME market (same week_start).

        Args:
            session: Active SQLAlchemy session
            week_start: Start of this market's time period (used to filter data)
            latest_timestamp: Timestamp of the data point being stored
            latest_count: Tweet count at `latest_timestamp` (normalized to this market)
        """
        snapshot = VelocitySnapshot()

        # Fetch previous data point from THIS MARKET ONLY
        previous_point = (
            session.query(TweetData)
            .filter(
                TweetData.week_start == week_start,
                TweetData.timestamp < latest_timestamp
            )
            .order_by(TweetData.timestamp.desc())
            .first()
        )

        # Calculate minute-based windows (e.g., 10 minutes)
        for window_minutes in self.WINDOWS_MINUTES:
            value = self._calc_window_velocity_minutes(
                session=session,
                week_start=week_start,
                latest_timestamp=latest_timestamp,
                latest_count=latest_count,
                window_minutes=window_minutes,
            )
            setattr(snapshot, f"velocity_{window_minutes}m", value)

        # Calculate hour-based windows
        for window in self.WINDOWS:
            value = self._calc_window_velocity(
                session=session,
                week_start=week_start,
                latest_timestamp=latest_timestamp,
                latest_count=latest_count,
                window_hours=window,
            )
            setattr(snapshot, f"velocity_{window}h", value)

        # Acceleration = change in 5m velocity vs previous sample for ultra-fast reaction
        # Fall back to 20m, then 1h velocity if 5m not available
        if previous_point:
            if previous_point.velocity_5m is not None and snapshot.velocity_5m is not None:
                snapshot.acceleration = snapshot.velocity_5m - previous_point.velocity_5m
            elif previous_point.velocity_20m is not None and snapshot.velocity_20m is not None:
                snapshot.acceleration = snapshot.velocity_20m - previous_point.velocity_20m
            elif previous_point.velocity_1h is not None and snapshot.velocity_1h is not None:
                snapshot.acceleration = snapshot.velocity_1h - previous_point.velocity_1h

        return snapshot

    def _calc_window_velocity_global(
        self,
        session: Session,
        latest_timestamp: datetime,
        latest_count: int,
        window_hours: int,
    ) -> Optional[float]:
        """
        Return tweets/hour over the specified window (GLOBAL calculation).

        This uses ALL recent tweet data, not filtered by market week.
        The velocity represents Elon's current tweeting rate, which is
        the same regardless of which market you're analyzing.
        """
        window_start = latest_timestamp - timedelta(hours=window_hours)

        # Find the closest data point near the window start (NO WEEK FILTER)
        anchor_point = (
            session.query(TweetData)
            .filter(TweetData.timestamp <= window_start)
            .order_by(TweetData.timestamp.desc())
            .first()
        )

        if not anchor_point:
            return None

        hours = (latest_timestamp - anchor_point.timestamp).total_seconds() / 3600
        if hours <= 0:
            return None

        tweets_delta = latest_count - anchor_point.cumulative_count
        return round(tweets_delta / hours, 3)

    def _calc_window_velocity(
        self,
        session: Session,
        week_start: datetime,
        latest_timestamp: datetime,
        latest_count: int,
        window_hours: int,
    ) -> Optional[float]:
        """
        Return tweets/hour over the specified window for THIS market.

        Only compares data points from the same market (same week_start) since
        each market's count is normalized to its own time period.
        """
        window_start = latest_timestamp - timedelta(hours=window_hours)

        # Find the closest data point near the window start FROM THIS MARKET
        anchor_point = (
            session.query(TweetData)
            .filter(
                TweetData.week_start == week_start,
                TweetData.timestamp <= window_start
            )
            .order_by(TweetData.timestamp.desc())
            .first()
        )

        if not anchor_point:
            return None

        hours = (latest_timestamp - anchor_point.timestamp).total_seconds() / 3600
        if hours <= 0:
            return None

        tweets_delta = latest_count - anchor_point.cumulative_count
        return round(tweets_delta / hours, 3)

    def _calc_window_velocity_minutes(
        self,
        session: Session,
        week_start: datetime,
        latest_timestamp: datetime,
        latest_count: int,
        window_minutes: int,
    ) -> Optional[float]:
        """
        Return tweets/hour over the specified minute window for THIS market.

        Note: Returns tweets/hour (not tweets/minute) for consistency with other velocities.
        """
        window_start = latest_timestamp - timedelta(minutes=window_minutes)

        # Find the closest data point near the window start FROM THIS MARKET
        anchor_point = (
            session.query(TweetData)
            .filter(
                TweetData.week_start == week_start,
                TweetData.timestamp <= window_start
            )
            .order_by(TweetData.timestamp.desc())
            .first()
        )

        if not anchor_point:
            return None

        hours = (latest_timestamp - anchor_point.timestamp).total_seconds() / 3600
        if hours <= 0:
            return None

        tweets_delta = latest_count - anchor_point.cumulative_count
        return round(tweets_delta / hours, 3)
