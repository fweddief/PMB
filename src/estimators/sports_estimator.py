"""
Sports estimator using BALLDONTLIE API (free key) and ESPN hidden API.

Handles:
- Game outcomes: injury-adjusted win probabilities
- Player props: season avg + recent 5-game trend, Normal CDF for threshold
"""

import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
from scipy import stats

from .base_estimator import BaseEstimator, FairValueEstimate, MarketEstimate

logger = logging.getLogger(__name__)

BALLDONTLIE_API_KEY = os.getenv("BALLDONTLIE_API_KEY", "")
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports"


class SportsEstimator(BaseEstimator):
    """Estimates sports market probabilities using public APIs."""

    def category(self) -> str:
        return "sports"

    def confidence_threshold(self) -> float:
        return 0.30

    def can_estimate(self, title: str, description: str = "") -> bool:
        text = f"{title} {description}".lower()
        sports_terms = [
            "nba", "nfl", "mlb", "nhl", "points", "goals", "touchdowns",
            "assists", "rebounds", "rushing yards", "passing yards", "win",
            "game", "match", "score", "season", "playoff", "super bowl",
            "championship", "mvp", "ufc", "boxing", "tennis", "golf",
        ]
        return any(term in text for term in sports_terms)

    def estimate(self, market_data: Dict) -> Optional[MarketEstimate]:
        title = market_data.get("title", "")
        description = market_data.get("description", "")

        # Determine if this is a player prop or game outcome
        prop_info = self._parse_player_prop(title, description)
        game_info = self._parse_game_outcome(title, description)

        if prop_info:
            return self._estimate_player_prop(market_data, prop_info)
        elif game_info:
            return self._estimate_game_outcome(market_data, game_info)

        return None

    def _estimate_player_prop(self, market_data: Dict, prop_info: Dict) -> Optional[MarketEstimate]:
        """Estimate player prop markets using season average and recent trend."""
        player_name = prop_info.get("player", "")
        stat_type = prop_info.get("stat", "")

        # Fetch player stats from BALLDONTLIE (NBA)
        player_stats = self._fetch_player_stats_balldontlie(player_name, stat_type)

        if not player_stats:
            # Try ESPN as fallback
            player_stats = self._fetch_player_stats_espn(player_name, stat_type)

        if not player_stats:
            logger.debug("No stats found for %s (%s)", player_name, stat_type)
            return None

        season_avg = player_stats.get("season_avg", 0)
        recent_avg = player_stats.get("recent_avg", season_avg)
        std_dev = player_stats.get("std_dev", season_avg * 0.25)

        # Weighted mean: 60% season average, 40% recent 5-game trend
        mean = 0.6 * season_avg + 0.4 * recent_avg
        sigma = max(std_dev, 1.0)

        dist = stats.norm(loc=mean, scale=sigma)
        confidence = 0.35  # Player props are inherently uncertain

        estimates = []
        for outcome in market_data.get("outcomes", []):
            outcome_text = outcome.get("outcome_text", "")
            low, high = self._parse_stat_range(outcome_text)

            if low is None and high is None:
                continue

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
                reasoning=f"{player_name} {stat_type}: season_avg={season_avg:.1f}, "
                          f"recent_5={recent_avg:.1f}, sigma={sigma:.1f}. "
                          f"P({outcome_text})={prob:.3f}",
                data_sources=["BALLDONTLIE", "ESPN"],
            ))

        if not estimates:
            return None

        return MarketEstimate(
            market_id=market_data.get("market_id", ""),
            category="sports",
            estimates=estimates,
            external_data={
                "player": player_name,
                "stat": stat_type,
                "season_avg": season_avg,
                "recent_avg": recent_avg,
                "std_dev": std_dev,
            },
        )

    def _estimate_game_outcome(self, market_data: Dict, game_info: Dict) -> Optional[MarketEstimate]:
        """Estimate game outcome markets using available data."""
        team_a = game_info.get("team_a", "")
        team_b = game_info.get("team_b", "")
        sport = game_info.get("sport", "")

        # Fetch team records and injuries
        team_a_data = self._fetch_team_data(team_a, sport)
        team_b_data = self._fetch_team_data(team_b, sport)

        if not team_a_data and not team_b_data:
            logger.debug("No team data for %s vs %s", team_a, team_b)
            return None

        # Simple win probability from win percentages
        win_pct_a = team_a_data.get("win_pct", 0.5) if team_a_data else 0.5
        win_pct_b = team_b_data.get("win_pct", 0.5) if team_b_data else 0.5

        # Normalize to probabilities
        total = win_pct_a + win_pct_b
        if total > 0:
            prob_a = win_pct_a / total
            prob_b = win_pct_b / total
        else:
            prob_a = prob_b = 0.5

        # Injury adjustment: significant injuries reduce win probability
        injuries_a = team_a_data.get("key_injuries", 0) if team_a_data else 0
        injuries_b = team_b_data.get("key_injuries", 0) if team_b_data else 0
        injury_penalty = 0.03  # 3% per key injury

        prob_a -= injuries_a * injury_penalty
        prob_b -= injuries_b * injury_penalty
        prob_a = max(0.05, min(0.95, prob_a))
        prob_b = max(0.05, min(0.95, prob_b))

        # Re-normalize
        total = prob_a + prob_b
        prob_a /= total
        prob_b /= total

        confidence = 0.6 if (injuries_a > 0 or injuries_b > 0) else 0.45

        estimates = []
        for outcome in market_data.get("outcomes", []):
            outcome_text = outcome.get("outcome_text", "").lower()

            if team_a.lower() in outcome_text or "yes" in outcome_text:
                prob = prob_a
            elif team_b.lower() in outcome_text or "no" in outcome_text:
                prob = prob_b
            else:
                continue

            estimates.append(FairValueEstimate(
                outcome_id=outcome.get("outcome_id", ""),
                outcome_text=outcome.get("outcome_text", ""),
                fair_probability=prob,
                confidence=confidence,
                reasoning=f"{team_a} ({win_pct_a:.3f}) vs {team_b} ({win_pct_b:.3f}), "
                          f"injuries: {injuries_a}/{injuries_b}. P={prob:.3f}",
                data_sources=["ESPN", "BALLDONTLIE"],
            ))

        if not estimates:
            return None

        return MarketEstimate(
            market_id=market_data.get("market_id", ""),
            category="sports",
            estimates=estimates,
            external_data={
                "team_a": team_a,
                "team_b": team_b,
                "sport": sport,
                "prob_a": prob_a,
                "prob_b": prob_b,
            },
        )

    def _parse_player_prop(self, title: str, description: str = "") -> Optional[Dict]:
        """Parse player name and stat type from a player prop market title."""
        text = f"{title} {description}".lower()

        stat_patterns = {
            "points": r"(\d+)\+?\s*points",
            "rebounds": r"(\d+)\+?\s*rebounds",
            "assists": r"(\d+)\+?\s*assists",
            "passing yards": r"(\d+)\+?\s*passing yards",
            "rushing yards": r"(\d+)\+?\s*rushing yards",
            "touchdowns": r"(\d+)\+?\s*touchdowns?",
            "goals": r"(\d+)\+?\s*goals?",
            "strikeouts": r"(\d+)\+?\s*strikeouts?",
            "home runs": r"(\d+)\+?\s*home runs?",
        }

        for stat_type, pattern in stat_patterns.items():
            match = re.search(pattern, text)
            if match:
                # Try to extract player name (words before the stat)
                # Simple heuristic: capitalized words before stat keywords
                words_before = title.split(stat_type.split()[0])[0].strip() if stat_type.split()[0] in title.lower() else ""
                player = re.sub(r'\s*(over|under|at least|more than|less than)\s*\d+.*', '', words_before, flags=re.IGNORECASE).strip()
                if player and len(player) > 2:
                    return {"player": player, "stat": stat_type, "threshold": int(match.group(1))}

        return None

    def _parse_game_outcome(self, title: str, description: str = "") -> Optional[Dict]:
        """Parse team names from a game outcome market title."""
        text = title.lower()

        # Pattern: "Team A vs Team B" or "Team A to win"
        vs_match = re.search(r'(.+?)\s+(?:vs?\.?|versus)\s+(.+?)(?:\s+|$)', text)
        if vs_match:
            team_a = vs_match.group(1).strip().rstrip(" -")
            team_b = vs_match.group(2).strip().rstrip("?. ")

            sport = "unknown"
            for s, terms in [("nba", ["nba"]), ("nfl", ["nfl"]), ("mlb", ["mlb"]), ("nhl", ["nhl"])]:
                if any(t in text for t in terms):
                    sport = s
                    break

            return {"team_a": team_a, "team_b": team_b, "sport": sport}

        return None

    def _fetch_player_stats_balldontlie(self, player_name: str, stat_type: str) -> Optional[Dict]:
        """Fetch player stats from BALLDONTLIE API."""
        if not BALLDONTLIE_API_KEY:
            return None

        try:
            headers = {"Authorization": BALLDONTLIE_API_KEY}

            # Search for player
            resp = requests.get(
                f"{BALLDONTLIE_BASE}/players",
                params={"search": player_name},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            players = resp.json().get("data", [])
            if not players:
                return None

            player_id = players[0]["id"]

            # Get season averages
            current_year = datetime.utcnow().year
            resp = requests.get(
                f"{BALLDONTLIE_BASE}/season_averages",
                params={"season": current_year, "player_ids[]": player_id},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            averages = resp.json().get("data", [])

            if not averages:
                return None

            avg = averages[0]
            stat_map = {
                "points": "pts",
                "rebounds": "reb",
                "assists": "ast",
                "goals": "pts",
            }
            stat_key = stat_map.get(stat_type, stat_type[:3])
            season_avg = avg.get(stat_key, 0)

            # Get recent games for trend
            resp = requests.get(
                f"{BALLDONTLIE_BASE}/stats",
                params={
                    "player_ids[]": player_id,
                    "seasons[]": current_year,
                    "per_page": 5,
                },
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            recent_games = resp.json().get("data", [])

            recent_values = [g.get(stat_key, 0) for g in recent_games if g.get(stat_key) is not None]
            recent_avg = sum(recent_values) / len(recent_values) if recent_values else season_avg

            # Calculate standard deviation
            if len(recent_values) >= 3:
                mean_val = sum(recent_values) / len(recent_values)
                variance = sum((v - mean_val) ** 2 for v in recent_values) / len(recent_values)
                std_dev = variance ** 0.5
            else:
                std_dev = season_avg * 0.25  # Default 25% CV

            return {
                "season_avg": season_avg,
                "recent_avg": recent_avg,
                "std_dev": max(std_dev, 1.0),
            }

        except Exception as e:
            logger.warning("BALLDONTLIE API error: %s", e)
            return None

    def _fetch_player_stats_espn(self, player_name: str, stat_type: str) -> Optional[Dict]:
        """Fallback: try ESPN hidden API for player stats."""
        # ESPN's hidden API is less structured, so we keep this simple
        return None

    def _fetch_team_data(self, team_name: str, sport: str) -> Optional[Dict]:
        """Fetch team win percentage and injury data from ESPN."""
        sport_map = {
            "nba": "basketball/nba",
            "nfl": "football/nfl",
            "mlb": "baseball/mlb",
            "nhl": "hockey/nhl",
        }

        sport_path = sport_map.get(sport)
        if not sport_path:
            return None

        try:
            # ESPN scoreboard gives current records
            resp = requests.get(
                f"{ESPN_SCOREBOARD}/{sport_path}/scoreboard",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            for event in data.get("events", []):
                for comp in event.get("competitions", []):
                    for competitor in comp.get("competitors", []):
                        name = competitor.get("team", {}).get("displayName", "").lower()
                        short_name = competitor.get("team", {}).get("shortDisplayName", "").lower()
                        abbrev = competitor.get("team", {}).get("abbreviation", "").lower()

                        if team_name.lower() in name or team_name.lower() in short_name or team_name.lower() == abbrev:
                            records = competitor.get("records", [{}])
                            record_str = records[0].get("summary", "0-0") if records else "0-0"

                            parts = record_str.split("-")
                            wins = int(parts[0]) if parts else 0
                            losses = int(parts[1]) if len(parts) > 1 else 0
                            total = wins + losses
                            win_pct = wins / total if total > 0 else 0.5

                            # Count injuries from the competitor data
                            injuries = competitor.get("injuries", [])
                            key_injuries = len([i for i in injuries if i.get("status", "").lower() == "out"])

                            return {
                                "win_pct": win_pct,
                                "wins": wins,
                                "losses": losses,
                                "key_injuries": key_injuries,
                            }

            return None

        except Exception as e:
            logger.warning("ESPN API error for %s: %s", team_name, e)
            return None

    def _parse_stat_range(self, outcome_text: str) -> Tuple[Optional[float], Optional[float]]:
        """Parse stat range from outcome text."""
        text = outcome_text.lower().strip()

        # "Over 25.5" / "25+"
        over_match = re.search(r'(?:over|more than|above|at least|\+)\s*([\d.]+)', text)
        if over_match:
            return float(over_match.group(1)), None

        # "Under 25.5"
        under_match = re.search(r'(?:under|less than|below|fewer than)\s*([\d.]+)', text)
        if under_match:
            return None, float(under_match.group(1))

        # Range "20-25"
        range_match = re.search(r'([\d.]+)\s*[-to]+\s*([\d.]+)', text)
        if range_match:
            return float(range_match.group(1)), float(range_match.group(2))

        # Just "Yes"/"No" for binary outcomes
        if "yes" in text:
            return 0.5, None  # Placeholder
        if "no" in text:
            return None, 0.5

        return None, None
