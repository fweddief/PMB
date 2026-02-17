"""Scanner package for multi-market discovery, classification, and ranking."""

__all__ = [
    "RateLimiter",
    "MarketScanner",
    "MarketClassifier",
    "OpportunityRanker",
]


def __getattr__(name):
    if name == "RateLimiter":
        from .rate_limiter import RateLimiter
        return RateLimiter
    elif name == "MarketScanner":
        from .market_scanner import MarketScanner
        return MarketScanner
    elif name == "MarketClassifier":
        from .market_classifier import MarketClassifier
        return MarketClassifier
    elif name == "OpportunityRanker":
        from .opportunity_ranker import OpportunityRanker
        return OpportunityRanker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
