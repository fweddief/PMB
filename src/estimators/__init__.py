"""Estimators package for building fair value estimates across market categories."""

__all__ = [
    "BaseEstimator",
    "FairValueEstimate",
    "MarketEstimate",
    "WeatherEstimator",
    "SportsEstimator",
    "CryptoEstimator",
    "TweetEstimator",
]


def __getattr__(name):
    if name in ("BaseEstimator", "FairValueEstimate", "MarketEstimate"):
        from .base_estimator import BaseEstimator, FairValueEstimate, MarketEstimate
        return {"BaseEstimator": BaseEstimator, "FairValueEstimate": FairValueEstimate, "MarketEstimate": MarketEstimate}[name]
    elif name == "WeatherEstimator":
        from .weather_estimator import WeatherEstimator
        return WeatherEstimator
    elif name == "SportsEstimator":
        from .sports_estimator import SportsEstimator
        return SportsEstimator
    elif name == "CryptoEstimator":
        from .crypto_estimator import CryptoEstimator
        return CryptoEstimator
    elif name == "TweetEstimator":
        from .tweet_estimator import TweetEstimator
        return TweetEstimator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
