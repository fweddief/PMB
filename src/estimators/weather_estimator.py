"""
Weather estimator using NOAA api.weather.gov (free, no auth, User-Agent header only).

Flow:
1. Parse city + date from market title
2. Geocode to NOAA grid point
3. Fetch forecast
4. Build Normal distribution around forecast temp
5. CDF for threshold/range
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from scipy import stats

from .base_estimator import BaseEstimator, FairValueEstimate, MarketEstimate

logger = logging.getLogger(__name__)

# Pre-computed grid points for major US cities (lat, lon)
# NOAA only covers US locations
CITY_COORDS: Dict[str, Tuple[float, float]] = {
    "new york": (40.7128, -74.0060),
    "nyc": (40.7128, -74.0060),
    "los angeles": (34.0522, -118.2437),
    "la": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "philadelphia": (39.9526, -75.1652),
    "san antonio": (29.4241, -98.4936),
    "san diego": (32.7157, -117.1611),
    "dallas": (32.7767, -96.7970),
    "austin": (30.2672, -97.7431),
    "jacksonville": (30.3322, -81.6557),
    "san francisco": (37.7749, -122.4194),
    "sf": (37.7749, -122.4194),
    "seattle": (47.6062, -122.3321),
    "denver": (39.7392, -104.9903),
    "washington dc": (38.9072, -77.0369),
    "dc": (38.9072, -77.0369),
    "boston": (42.3601, -71.0589),
    "nashville": (36.1627, -86.7816),
    "detroit": (42.3314, -83.0458),
    "portland": (45.5152, -122.6784),
    "miami": (25.7617, -80.1918),
    "atlanta": (33.7490, -84.3880),
    "minneapolis": (44.9778, -93.2650),
    "cleveland": (41.4993, -81.6944),
    "orlando": (28.5383, -81.3792),
    "st louis": (38.6270, -90.1994),
    "pittsburgh": (40.4406, -79.9959),
    "las vegas": (36.1699, -115.1398),
    "tampa": (27.9506, -82.4572),
    "milwaukee": (43.0389, -87.9065),
    "salt lake city": (40.7608, -111.8910),
    "kansas city": (39.0997, -94.5786),
    "new orleans": (29.9511, -90.0715),
    "anchorage": (61.2181, -149.9003),
    "honolulu": (21.3069, -157.8583),
}

# NOAA forecast sigma by days out (typical standard deviation in Fahrenheit)
FORECAST_SIGMA: Dict[int, float] = {
    0: 2.0,
    1: 3.0,
    2: 4.0,
    3: 5.5,
    4: 7.0,
    5: 8.5,
    6: 10.0,
    7: 12.0,
}

NOAA_HEADERS = {"User-Agent": "PolymarketBot/1.0 (polymarket-weather-estimator)"}


class WeatherEstimator(BaseEstimator):
    """Estimates weather market probabilities using NOAA forecast data."""

    def category(self) -> str:
        return "weather"

    def confidence_threshold(self) -> float:
        return 0.35

    def can_estimate(self, title: str, description: str = "") -> bool:
        text = f"{title} {description}".lower()
        weather_terms = ["temperature", "degrees", "fahrenheit", "celsius", "high temp", "low temp", "weather"]
        return any(term in text for term in weather_terms)

    def estimate(self, market_data: Dict) -> Optional[MarketEstimate]:
        title = market_data.get("title", "")
        description = market_data.get("description", "")

        # Parse city and date from title
        city, target_date = self._parse_city_and_date(title, description)
        if not city:
            logger.debug("Could not parse city from: %s", title)
            return None

        coords = self._get_coords(city)
        if not coords:
            logger.debug("No coordinates for city: %s", city)
            return None

        # Fetch NOAA forecast
        forecast_temp, forecast_type = self._fetch_noaa_forecast(coords, target_date)
        if forecast_temp is None:
            logger.warning("NOAA forecast unavailable for %s on %s", city, target_date)
            return None

        # Calculate days out for confidence
        days_out = max((target_date - datetime.utcnow().date()).days, 0) if target_date else 3
        sigma = FORECAST_SIGMA.get(min(days_out, 7), 12.0)

        # Confidence decreases with forecast horizon
        if days_out <= 2:
            confidence = 0.8
        elif days_out <= 5:
            confidence = 0.6
        elif days_out <= 7:
            confidence = 0.4
        else:
            confidence = 0.2

        # Build estimates for each outcome
        estimates = []
        dist = stats.norm(loc=forecast_temp, scale=sigma)

        for outcome in market_data.get("outcomes", []):
            outcome_text = outcome.get("outcome_text", "")
            low, high = self._parse_temp_range(outcome_text)

            if low is None and high is None:
                continue

            # Calculate probability using CDF
            if low is None:
                prob = dist.cdf(high)
            elif high is None:
                prob = 1.0 - dist.cdf(low)
            else:
                prob = dist.cdf(high) - dist.cdf(low)

            prob = max(0.001, min(0.999, prob))

            estimates.append(FairValueEstimate(
                outcome_id=outcome.get("outcome_id", ""),
                outcome_text=outcome_text,
                fair_probability=prob,
                confidence=confidence,
                reasoning=f"NOAA forecast: {forecast_temp:.0f}F ({forecast_type}), "
                          f"sigma={sigma:.1f}F, {days_out}d out. "
                          f"P({outcome_text})={prob:.3f}",
                data_sources=["NOAA api.weather.gov"],
            ))

        if not estimates:
            return None

        return MarketEstimate(
            market_id=market_data.get("market_id", ""),
            category="weather",
            estimates=estimates,
            external_data={
                "city": city,
                "forecast_temp": forecast_temp,
                "forecast_type": forecast_type,
                "sigma": sigma,
                "days_out": days_out,
                "confidence": confidence,
            },
        )

    def _parse_city_and_date(self, title: str, description: str = "") -> Tuple[Optional[str], Optional[object]]:
        """Extract city name and target date from market title."""
        text = f"{title} {description}".lower()

        # Try to match city names
        found_city = None
        for city_name in sorted(CITY_COORDS.keys(), key=len, reverse=True):
            if city_name in text:
                found_city = city_name
                break

        # Try to parse date
        target_date = None
        # Match patterns like "January 15", "Jan 15", "1/15", "2025-01-15"
        date_patterns = [
            r'(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?',
            r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?',
        ]

        months = {
            "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
            "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
            "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
            "november": 11, "nov": 11, "december": 12, "dec": 12,
        }

        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                try:
                    if groups[0].isdigit():
                        month = int(groups[0])
                        day = int(groups[1])
                    else:
                        month_name = groups[0].lower()
                        if month_name in months:
                            month = months[month_name]
                            day = int(groups[1])
                        else:
                            continue

                    year = int(groups[2]) if groups[2] else datetime.utcnow().year
                    if year < 100:
                        year += 2000

                    from datetime import date
                    target_date = date(year, month, day)
                    break
                except (ValueError, IndexError):
                    continue

        return found_city, target_date

    def _get_coords(self, city: str) -> Optional[Tuple[float, float]]:
        """Get coordinates for a city."""
        return CITY_COORDS.get(city.lower())

    def _fetch_noaa_forecast(self, coords: Tuple[float, float], target_date=None) -> Tuple[Optional[float], str]:
        """
        Fetch forecast from NOAA api.weather.gov.

        Returns (temperature_F, forecast_type) or (None, "").
        """
        lat, lon = coords

        try:
            # Step 1: Get grid point
            points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
            resp = requests.get(points_url, headers=NOAA_HEADERS, timeout=10)
            resp.raise_for_status()
            points_data = resp.json()

            forecast_url = points_data["properties"]["forecast"]

            # Step 2: Get forecast
            resp = requests.get(forecast_url, headers=NOAA_HEADERS, timeout=10)
            resp.raise_for_status()
            forecast_data = resp.json()

            periods = forecast_data.get("properties", {}).get("periods", [])
            if not periods:
                return None, ""

            # Find the period matching our target date (or use first period)
            if target_date:
                for period in periods:
                    period_start = period.get("startTime", "")[:10]
                    try:
                        period_date = datetime.fromisoformat(period_start.replace("Z", "+00:00")).date()
                        if period_date == target_date:
                            temp = period.get("temperature")
                            name = period.get("name", "")
                            if temp is not None:
                                return float(temp), name
                    except (ValueError, AttributeError):
                        continue

            # Fallback: return first daytime period
            for period in periods:
                if period.get("isDaytime", True):
                    temp = period.get("temperature")
                    if temp is not None:
                        return float(temp), period.get("name", "today")

            return None, ""

        except Exception as e:
            logger.warning("NOAA API error: %s", e)
            return None, ""

    def _parse_temp_range(self, outcome_text: str) -> Tuple[Optional[float], Optional[float]]:
        """Parse temperature range from outcome text like '80-85F' or 'Above 90F'."""
        text = outcome_text.lower().strip()

        # Range: "80-85", "80 to 85"
        range_match = re.search(r'(\d+)\s*[-to]+\s*(\d+)', text)
        if range_match:
            return float(range_match.group(1)), float(range_match.group(2))

        # Below/under/less than
        below_match = re.search(r'(?:below|under|less than|<)\s*(\d+)', text)
        if below_match:
            return None, float(below_match.group(1))

        # Above/over/more than
        above_match = re.search(r'(?:above|over|more than|>|or more)\s*(\d+)', text)
        if above_match:
            return float(above_match.group(1)), None

        # Just a number with "or higher"/"or lower"
        num_match = re.search(r'(\d+)\s*(?:or higher|\+)', text)
        if num_match:
            return float(num_match.group(1)), None

        num_match = re.search(r'(\d+)\s*or lower', text)
        if num_match:
            return None, float(num_match.group(1))

        return None, None
