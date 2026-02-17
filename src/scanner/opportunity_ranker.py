"""
Opportunity ranker: scores and ranks trading opportunities.

Score formula: edge * sqrt(liquidity) * estimator_confidence
Tie-breaks by category historical ROI.
"""

import logging
import math
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class OpportunityRanker:
    """
    Ranks opportunities by a composite score combining edge, liquidity,
    and estimator confidence. Uses historical category performance as tie-breaker.
    """

    def __init__(self, category_roi: Dict[str, float] = None):
        """
        Args:
            category_roi: Dict mapping category -> historical ROI percentage.
                          Used as a tie-breaker. Default: all categories equal.
        """
        self.category_roi = category_roi or {}

    def update_category_roi(self, category_roi: Dict[str, float]) -> None:
        """Update category ROI data for tie-breaking."""
        self.category_roi = category_roi

    def rank(self, opportunities: List[Dict]) -> List[Dict]:
        """
        Score and rank a list of opportunities.

        Each opportunity dict should have:
            - edge: float (e.g., 0.12 for 12% edge)
            - liquidity: float (USD)
            - confidence: float (0-1, estimator confidence)
            - category: str
            - market_id, outcome_id, etc.

        Returns:
            Same list sorted by score descending, with 'score' added.
        """
        for opp in opportunities:
            edge = abs(opp.get("edge", 0))
            liquidity = max(opp.get("liquidity", 0), 1)
            confidence = max(opp.get("confidence", 0), 0.01)
            category = opp.get("category", "unknown")

            # Primary score: edge * sqrt(liquidity) * confidence
            score = edge * math.sqrt(liquidity) * confidence

            # Tie-breaker: category historical ROI (small additive bonus)
            cat_roi = self.category_roi.get(category, 0)
            roi_bonus = max(cat_roi, 0) * 0.001  # Small so it only breaks ties
            score += roi_bonus

            opp["score"] = round(score, 4)

        # Sort by score descending
        opportunities.sort(key=lambda x: x.get("score", 0), reverse=True)

        logger.info(
            "Ranked %d opportunities (top score: %.4f)",
            len(opportunities),
            opportunities[0]["score"] if opportunities else 0,
        )

        return opportunities
