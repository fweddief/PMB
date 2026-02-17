"""
Scraper for xtracker.io to collect Elon Musk tweet count data.

This scraper supports:
1. Lightweight HTTP parsing of https://xtracker.polymarket.com/user/<handle>
2. CSV file imports (manual or automated)
"""

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests


logger = logging.getLogger(__name__)


class XTrackerScraper:
    """
    Scrapes Elon Musk tweet count data from xtracker.io
    """

    def __init__(self, url: str = "https://xtracker.polymarket.com", username: Optional[str] = None):
        """
        Initialize the scraper.

        Args:
            url: Base URL for xtracker (xtracker.polymarket.com)
        """
        self.url = url.rstrip("/")
        self.username = username or os.getenv("XTRACKER_USER", "elonmusk")
        self.last_scrape_time = None
        self.last_tweet_count = None

    def scrape_via_http(self) -> Optional[Dict]:
        """Fetch tweet count from the public xtracker page."""
        endpoint = f"{self.url}/user/{self.username}"
        logger.info("Fetching live tweets from %s", endpoint)

        try:
            response = requests.get(endpoint, timeout=15)
            response.raise_for_status()
            body_text = response.text
        except Exception as exc:
            logger.error("Failed to fetch xtracker page: %s", exc)
            return None

        tweet_count = self._extract_count(body_text)
        if tweet_count is None:
            logger.warning("Could not locate tweet count in xtracker response")
            return None

        self.last_scrape_time = datetime.utcnow()
        self.last_tweet_count = tweet_count

        return {
            "timestamp": self.last_scrape_time,
            "cumulative_count": tweet_count,
            "source": "xtracker_http",
        }

    def _extract_count(self, body_text: str) -> Optional[int]:
        patterns = [
            r'(\d{1,3}(?:,\d{3})*)\s*TOTAL POSTS',
            r'POSTS\s*(\d{1,3}(?:,\d{3})*)',
            r'"totalPosts"\s*:\s*(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1).replace(",", ""))
                except ValueError:
                    continue
        return None

    def import_from_csv(self, csv_path: str, week_start: datetime = None, week_end: datetime = None) -> List[Dict]:
        """
        Import tweet data from a CSV file exported from xtracker.io.

        Expected CSV format (adjust based on actual format):
        timestamp, tweet_count, tweet_text, ...

        Args:
            csv_path: Path to CSV file
            week_start: Start date for the week (for filtering)
            week_end: End date for the week (for filtering)

        Returns:
            List of tweet data dictionaries
        """
        logger.info(f"Importing tweet data from CSV: {csv_path}")

        if not os.path.exists(csv_path):
            logger.error(f"CSV file not found: {csv_path}")
            return []

        try:
            df = pd.read_csv(csv_path)
            logger.info(f"Loaded CSV with {len(df)} rows")
            logger.debug(f"Columns: {df.columns.tolist()}")

            # Try to identify timestamp and count columns
            # Adjust these based on actual CSV format
            timestamp_col = None
            for col in ['timestamp', 'date', 'time', 'created_at']:
                if col in df.columns:
                    timestamp_col = col
                    break

            if timestamp_col:
                df[timestamp_col] = pd.to_datetime(df[timestamp_col])

                # Filter by week if specified
                if week_start and week_end:
                    df = df[(df[timestamp_col] >= week_start) & (df[timestamp_col] < week_end)]
                    logger.info(f"Filtered to {len(df)} rows for week {week_start} - {week_end}")

            # Generate cumulative count
            data_points = []
            for idx, row in df.iterrows():
                data_points.append({
                    'timestamp': row[timestamp_col] if timestamp_col else datetime.utcnow(),
                    'cumulative_count': idx + 1,  # Simple cumulative count
                    'source': 'xtracker_csv',
                })

            logger.info(f"Processed {len(data_points)} data points from CSV")
            return data_points

        except Exception as e:
            logger.error(f"Failed to import CSV: {e}", exc_info=True)
            return []

    def scrape_current_count(self, method: str = 'http') -> Optional[Dict]:
        """
        Scrape the current tweet count using the specified method.
        """
        if method == 'http':
            return self.scrape_via_http()

        if method == 'polymarket':
            from scrapers.polymarket_scraper import PolymarketScraper

            scraper = PolymarketScraper()
            return scraper.get_live_tweet_count()

        logger.error(f"Unknown scraping method: {method}")
        return None

    def get_week_bounds(self, reference_date: datetime = None) -> tuple:
        """
        Get the start and end dates for the current Polymarket week.
        Polymarket weeks typically run Friday-Friday (adjust if different).

        Args:
            reference_date: Date to calculate week for (defaults to now)

        Returns:
            Tuple of (week_start, week_end)
        """
        if reference_date is None:
            reference_date = datetime.utcnow()

        # Find the most recent Friday (or current day if it's Friday)
        # Polymarket weeks typically start on Friday at 00:00 UTC
        days_since_friday = (reference_date.weekday() - 4) % 7
        week_start = (reference_date - timedelta(days=days_since_friday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_end = week_start + timedelta(days=7)

        return week_start, week_end
