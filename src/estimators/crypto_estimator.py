"""
Crypto estimator using Polymarket-internal pricing only.

Finds mispricings by analyzing the market's own outcome prices:
- Probability sum errors: outcome prices that don't sum to 1.0
- Stale/wide spreads: outcomes where bid-ask spread suggests mispricing
- Cross-outcome consistency: related outcomes that contradict each other

No external API calls — eliminates CoinGecko/DeFiLlama rate limiting entirely.
"""

import logging
from typing import Dict, List, Optional, Tuple

from .base_estimator import BaseEstimator, FairValueEstimate, MarketEstimate

logger = logging.getLogger(__name__)

# Minimum mispricing (as fraction of 1.0) to flag as an opportunity
MIN_SUM_DEVIATION = 0.02  # 2% probability sum error
# Outcomes priced below this are considered illiquid/ignore
MIN_OUTCOME_PRICE = 0.01
# Maximum spread to consider an outcome tradeable
MAX_SPREAD_RATIO = 0.20  # 20% of mid price
# Valid range for probability sum — outside this it's a multi-bracket market,
# not a real mispricing. The scanner only captures one Yes price per sub-market,
# so multi-bracket markets (e.g. "by March 31", "by June 30") will have
# prob_sum << 1.0 which is NOT an arbitrage opportunity.
MIN_VALID_SUM = 0.80
MAX_VALID_SUM = 1.25


class CryptoEstimator(BaseEstimator):
    """
    Estimates crypto market probabilities purely from Polymarket structure.

    Instead of trying to predict crypto prices with external data,
    we find internal mispricings: outcomes whose prices don't add up,
    stale quotes, or obvious inconsistencies.
    """

    def __init__(self, rate_limiter=None):
        # rate_limiter accepted for interface compatibility but not used
        pass

    def category(self) -> str:
        return "crypto"

    def confidence_threshold(self) -> float:
        return 0.25

    def can_estimate(self, title: str, description: str = "") -> bool:
        text = f"{title} {description}".lower()
        crypto_terms = [
            "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
            "crypto", "token", "blockchain", "defi", "tvl",
            "market cap", "price", "dogecoin", "doge", "xrp",
            "cardano", "ada", "polkadot", "avalanche", "polygon",
        ]
        return any(term in text for term in crypto_terms)

    def estimate(self, market_data: Dict) -> Optional[MarketEstimate]:
        """
        Estimate fair probabilities from the market's own outcome prices.

        Strategy:
        1. Read each outcome's current price (= implied probability)
        2. Check if prices sum to 1.0 — if not, the deviation is the edge
        3. Normalize to fair probabilities and flag any outcomes with edge
        """
        title = market_data.get("title", "")
        outcomes = market_data.get("outcomes", [])

        if not outcomes:
            return None

        # Extract prices for each outcome
        priced_outcomes = []
        for outcome in outcomes:
            price = self._get_outcome_price(outcome)
            if price is not None and price >= MIN_OUTCOME_PRICE:
                priced_outcomes.append((outcome, price))

        if len(priced_outcomes) < 2:
            return None

        # Calculate probability sum
        prob_sum = sum(price for _, price in priced_outcomes)

        # Skip multi-bracket markets where the scanner only captures one Yes
        # price per sub-market. These have prob_sum far from 1.0 and are NOT
        # mispricings (e.g. "Khamenei leaves by March 31" Yes=5.5%, No=94.5%
        # but we only see 5.5% per bracket, so sum across brackets << 1.0).
        if prob_sum < MIN_VALID_SUM or prob_sum > MAX_VALID_SUM:
            logger.debug(
                "Skipping %s: prob_sum=%.3f outside valid range [%.2f, %.2f] "
                "(likely multi-bracket market)",
                market_data.get("title", "")[:50], prob_sum,
                MIN_VALID_SUM, MAX_VALID_SUM,
            )
            return None

        # If probabilities sum very close to 1.0, no obvious mispricing
        deviation = abs(prob_sum - 1.0)
        if deviation < MIN_SUM_DEVIATION:
            # Still check for wide spreads that might indicate staleness
            return self._estimate_from_spreads(market_data, priced_outcomes, prob_sum)

        # Probabilities don't sum to 1.0 but within valid range — real mispricing
        return self._estimate_from_sum_error(market_data, priced_outcomes, prob_sum)

    def _estimate_from_sum_error(
        self, market_data: Dict, priced_outcomes: List[Tuple[Dict, float]], prob_sum: float
    ) -> Optional[MarketEstimate]:
        """
        When outcome prices don't sum to 1.0, normalize to find fair values.

        If sum > 1.0: market is overpriced overall (vig), all outcomes slightly overpriced
        If sum < 1.0: market is underpriced, there's free expected value
        """
        estimates = []
        overround = prob_sum - 1.0  # positive = overpriced, negative = underpriced

        # Confidence scales with the size of the deviation
        deviation = abs(overround)
        confidence = min(0.6, 0.25 + deviation * 2)

        for outcome, market_price in priced_outcomes:
            # Fair probability = normalized price
            fair_prob = market_price / prob_sum
            fair_prob = max(0.001, min(0.999, fair_prob))

            # Check if spread makes this tradeable
            spread = self._get_spread(outcome)
            if spread is not None and spread > MAX_SPREAD_RATIO * market_price:
                # Too wide a spread — reduce confidence
                confidence_adj = confidence * 0.5
            else:
                confidence_adj = confidence

            edge = fair_prob - market_price
            direction = "underpriced" if edge > 0 else "overpriced"

            estimates.append(FairValueEstimate(
                outcome_id=outcome.get("outcome_id", ""),
                outcome_text=outcome.get("outcome_text", ""),
                fair_probability=fair_prob,
                confidence=confidence_adj,
                reasoning=(
                    f"Sum={prob_sum:.3f} (overround={overround:+.3f}). "
                    f"Market price={market_price:.3f}, fair={fair_prob:.3f}, "
                    f"edge={edge:+.3f} ({direction})"
                ),
                data_sources=["polymarket_internal"],
            ))

        if not estimates:
            return None

        return MarketEstimate(
            market_id=market_data.get("market_id", ""),
            category="crypto",
            estimates=estimates,
            external_data={
                "prob_sum": prob_sum,
                "overround": overround,
                "num_outcomes": len(priced_outcomes),
                "method": "sum_error",
            },
        )

    def _estimate_from_spreads(
        self, market_data: Dict, priced_outcomes: List[Tuple[Dict, float]], prob_sum: float
    ) -> Optional[MarketEstimate]:
        """
        When prices sum to ~1.0, look for wide spreads indicating stale pricing.

        Wide bid-ask spreads on individual outcomes can still represent edge
        even when the overall market sums correctly.
        """
        estimates = []

        for outcome, market_price in priced_outcomes:
            bid = outcome.get("bid", None)
            ask = outcome.get("ask", None)

            if bid is not None and ask is not None and bid > 0 and ask > 0:
                spread = ask - bid
                mid = (bid + ask) / 2.0

                # Wide spread = potential mispricing, use mid as fair value
                if spread > MIN_SUM_DEVIATION and mid > MIN_OUTCOME_PRICE:
                    fair_prob = mid / prob_sum
                    fair_prob = max(0.001, min(0.999, fair_prob))

                    # Confidence based on spread width (wider = more uncertain)
                    confidence = max(0.15, 0.4 - spread)

                    estimates.append(FairValueEstimate(
                        outcome_id=outcome.get("outcome_id", ""),
                        outcome_text=outcome.get("outcome_text", ""),
                        fair_probability=fair_prob,
                        confidence=confidence,
                        reasoning=(
                            f"Wide spread: bid={bid:.3f}, ask={ask:.3f}, "
                            f"spread={spread:.3f}, mid={mid:.3f}. "
                            f"Market price={market_price:.3f}, fair={fair_prob:.3f}"
                        ),
                        data_sources=["polymarket_internal"],
                    ))
            else:
                # No bid/ask data — use price as-is, normalized
                fair_prob = market_price / prob_sum
                fair_prob = max(0.001, min(0.999, fair_prob))
                estimates.append(FairValueEstimate(
                    outcome_id=outcome.get("outcome_id", ""),
                    outcome_text=outcome.get("outcome_text", ""),
                    fair_probability=fair_prob,
                    confidence=0.25,
                    reasoning=(
                        f"Normalized price: market={market_price:.3f}, "
                        f"fair={fair_prob:.3f}, sum={prob_sum:.3f}"
                    ),
                    data_sources=["polymarket_internal"],
                ))

        if not estimates:
            return None

        return MarketEstimate(
            market_id=market_data.get("market_id", ""),
            category="crypto",
            estimates=estimates,
            external_data={
                "prob_sum": prob_sum,
                "num_outcomes": len(priced_outcomes),
                "method": "spread_analysis",
            },
        )

    def _get_outcome_price(self, outcome: Dict) -> Optional[float]:
        """Extract the current price from an outcome dict."""
        # Try various field names Polymarket uses
        for key in ("price", "last_price", "outcome_price", "lastTradePrice"):
            val = outcome.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue

        # Try bid/ask midpoint as fallback
        bid = outcome.get("bid")
        ask = outcome.get("ask")
        if bid is not None and ask is not None:
            try:
                return (float(bid) + float(ask)) / 2.0
            except (ValueError, TypeError):
                pass

        return None

    def _get_spread(self, outcome: Dict) -> Optional[float]:
        """Get bid-ask spread for an outcome."""
        bid = outcome.get("bid")
        ask = outcome.get("ask")
        if bid is not None and ask is not None:
            try:
                return float(ask) - float(bid)
            except (ValueError, TypeError):
                pass
        return None
