"""
Abstract base estimator interface for all category estimators.

Each estimator must implement:
- category(): which category it handles
- can_estimate(): whether it can price a given market
- estimate(): produce fair value probabilities for each outcome
- confidence_threshold(): minimum confidence to generate a signal
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FairValueEstimate:
    """Fair value estimate for a single outcome."""
    outcome_id: str
    outcome_text: str
    fair_probability: float
    confidence: float
    reasoning: str
    data_sources: List[str] = field(default_factory=list)


@dataclass
class MarketEstimate:
    """Full estimate for a market with all outcomes."""
    market_id: str
    category: str
    estimates: List[FairValueEstimate]
    external_data: Dict = field(default_factory=dict)

    @property
    def best_edge(self) -> float:
        """Return the maximum absolute edge across all outcomes."""
        if not self.estimates:
            return 0.0
        return max(abs(e.fair_probability) for e in self.estimates)


class BaseEstimator(ABC):
    """Abstract base class for category estimators."""

    @abstractmethod
    def category(self) -> str:
        """Return the category this estimator handles (e.g., 'weather', 'crypto')."""
        ...

    @abstractmethod
    def can_estimate(self, title: str, description: str = "") -> bool:
        """
        Return True if this estimator can produce a fair value for the given market.

        Args:
            title: Market title.
            description: Market description.
        """
        ...

    @abstractmethod
    def estimate(self, market_data: Dict) -> Optional[MarketEstimate]:
        """
        Produce fair value estimates for each outcome in the market.

        Args:
            market_data: Parsed market dict from MarketScanner with keys:
                market_id, title, description, outcomes (list), etc.

        Returns:
            MarketEstimate or None if estimation fails.
        """
        ...

    def confidence_threshold(self) -> float:
        """
        Minimum confidence level for this estimator to generate a tradeable signal.

        Override in subclasses for category-specific thresholds.
        """
        return 0.3
