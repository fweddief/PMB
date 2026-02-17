"""
Keyword-based market classifier.

Categorizes Polymarket markets into: weather, sports, crypto,
politics, entertainment, tweets. Each category has positive keywords
and negative exclusion keywords.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Classification:
    category: str
    confidence: float
    matched_keywords: List[str]


# Category definitions: (positive_keywords, negative_keywords, base_confidence)
CATEGORIES: Dict[str, Dict] = {
    "weather": {
        "keywords": [
            "temperature", "weather", "degrees", "fahrenheit", "celsius",
            "high temp", "low temp", "heat", "cold", "snow", "rain",
            "hurricane", "tornado", "climate", "forecast", "noaa",
            "warmest", "coldest", "heatwave", "blizzard", "flood",
        ],
        "exclude": ["political climate", "crypto winter"],
        "base_confidence": 0.85,
    },
    "sports": {
        "keywords": [
            "nba", "nfl", "mlb", "nhl", "mls", "premier league",
            "champions league", "world cup", "super bowl", "playoffs",
            "game", "match", "score", "points", "goals", "touchdowns",
            "assists", "rebounds", "rushing yards", "passing yards",
            "win the game", "championship", "mvp", "rookie", "all-star",
            "season", "player", "team", "coach", "draft",
            "lakers", "celtics", "warriors", "chiefs", "eagles",
            "yankees", "dodgers", "injury", "injured",
            "ufc", "boxing", "tennis", "golf", "f1", "formula 1",
            "grand prix", "pga", "masters",
        ],
        "exclude": ["esports", "e-sports", "gaming"],
        "base_confidence": 0.80,
    },
    "crypto": {
        "keywords": [
            "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
            "crypto", "cryptocurrency", "blockchain", "defi",
            "token", "coin", "altcoin", "market cap", "tvl",
            "total value locked", "trading volume", "binance",
            "coinbase", "dex", "nft", "stablecoin", "usdt", "usdc",
            "cardano", "ada", "xrp", "ripple", "dogecoin", "doge",
            "polkadot", "avalanche", "avax", "polygon", "matic",
            "chainlink", "link", "uniswap", "aave", "maker",
            "bitcoin price", "eth price", "crypto market",
            "halving", "mining", "staking", "yield",
        ],
        "exclude": [],
        "base_confidence": 0.85,
    },
    "tweets": {
        "keywords": [
            "tweets", "tweet", "elon musk", "x posts", "twitter",
            "social media posts", "number of tweets",
            "how many tweets", "tweet count", "posting",
        ],
        "exclude": [],
        "base_confidence": 0.90,
    },
    "politics": {
        "keywords": [
            "president", "election", "vote", "congress", "senate",
            "house", "republican", "democrat", "trump", "biden",
            "political", "governor", "mayor", "legislation", "bill",
            "supreme court", "executive order", "impeach", "cabinet",
            "approval rating", "poll", "primary", "caucus",
            "parliament", "prime minister", "eu", "nato",
        ],
        "exclude": [],
        "base_confidence": 0.75,
    },
    "entertainment": {
        "keywords": [
            "oscar", "grammy", "emmy", "tony", "golden globe",
            "box office", "movie", "film", "album", "song",
            "streaming", "netflix", "spotify", "youtube",
            "celebrity", "actor", "actress", "singer", "rapper",
            "concert", "tour", "release", "premiere",
            "tv show", "series", "reality tv", "award",
        ],
        "exclude": [],
        "base_confidence": 0.70,
    },
}


class MarketClassifier:
    """
    Classifies Polymarket markets into categories using keyword matching.

    Returns the best-matching category with a confidence score.
    """

    def __init__(self, custom_categories: Dict = None):
        self.categories = dict(CATEGORIES)
        if custom_categories:
            self.categories.update(custom_categories)

        # Pre-compile keyword patterns for faster matching
        self._compiled = {}
        for cat_name, cat_config in self.categories.items():
            patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in cat_config["keywords"]]
            excludes = [re.compile(re.escape(kw), re.IGNORECASE) for kw in cat_config.get("exclude", [])]
            self._compiled[cat_name] = (patterns, excludes, cat_config["base_confidence"])

    def classify(self, title: str, description: str = "") -> Classification:
        """
        Classify a market by its title and description.

        Args:
            title: Market title/question.
            description: Market description (optional).

        Returns:
            Classification with category, confidence, and matched keywords.
        """
        text = f"{title} {description}".strip()

        best_category = "unknown"
        best_score = 0.0
        best_matched = []

        for cat_name, (patterns, excludes, base_conf) in self._compiled.items():
            # Check exclusions first
            excluded = any(exc.search(text) for exc in excludes)
            if excluded:
                continue

            # Count keyword matches
            matched = []
            for pattern in patterns:
                if pattern.search(text):
                    matched.append(pattern.pattern)

            if not matched:
                continue

            # Score: base_confidence scaled by number of matches (diminishing returns)
            match_boost = min(len(matched) / 3.0, 1.0)  # max boost at 3 matches
            score = base_conf * (0.5 + 0.5 * match_boost)

            if score > best_score:
                best_score = score
                best_category = cat_name
                best_matched = matched

        return Classification(
            category=best_category,
            confidence=round(best_score, 3),
            matched_keywords=best_matched,
        )

    def classify_batch(self, markets: List[Dict]) -> List[Dict]:
        """
        Classify a batch of markets, adding category info to each.

        Args:
            markets: List of market dicts with 'title' and optional 'description'.

        Returns:
            Same markets with 'category', 'category_confidence', 'matched_keywords' added.
        """
        for market in markets:
            classification = self.classify(
                title=market.get("title", ""),
                description=market.get("description", ""),
            )
            market["category"] = classification.category
            market["category_confidence"] = classification.confidence
            market["matched_keywords"] = classification.matched_keywords

        return markets
