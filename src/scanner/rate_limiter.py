"""
Token-bucket rate limiter shared by all API callers.

Configured per-API with different burst capacities and refill rates.
"""

import asyncio
import time
import threading
from typing import Dict


class RateLimiter:
    """
    Token-bucket rate limiter.

    Each API gets its own bucket with configurable capacity and refill rate.
    Supports both sync and async usage.
    """

    # Default rate limits per API
    DEFAULT_LIMITS: Dict[str, Dict] = {
        "gamma": {"capacity": 900, "refill_rate": 90.0},       # 900 per 10s
        "clob": {"capacity": 1500, "refill_rate": 150.0},      # 1500 per 10s
        "coingecko": {"capacity": 100, "refill_rate": 1.667},   # 100 per min
        "noaa": {"capacity": 500, "refill_rate": 50.0},         # generous
        "balldontlie": {"capacity": 30, "refill_rate": 0.5},    # 30 per min
        "espn": {"capacity": 100, "refill_rate": 5.0},          # conservative
        "defillama": {"capacity": 100, "refill_rate": 5.0},     # conservative
        "alternativeme": {"capacity": 30, "refill_rate": 0.5},  # conservative
    }

    def __init__(self, overrides: Dict[str, Dict] = None):
        self._buckets: Dict[str, Dict] = {}
        self._lock = threading.Lock()

        limits = dict(self.DEFAULT_LIMITS)
        if overrides:
            limits.update(overrides)

        for api_name, config in limits.items():
            self._buckets[api_name] = {
                "tokens": float(config["capacity"]),
                "capacity": float(config["capacity"]),
                "refill_rate": float(config["refill_rate"]),  # tokens per second
                "last_refill": time.monotonic(),
            }

    def _refill(self, bucket: Dict) -> None:
        now = time.monotonic()
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            bucket["capacity"],
            bucket["tokens"] + elapsed * bucket["refill_rate"],
        )
        bucket["last_refill"] = now

    def acquire(self, api_name: str, tokens: int = 1) -> float:
        """
        Acquire tokens from a bucket. Blocks (sleeps) if insufficient tokens.

        Returns the number of seconds waited (0 if no wait needed).
        """
        api_name = api_name.lower()
        if api_name not in self._buckets:
            return 0.0

        waited = 0.0
        with self._lock:
            bucket = self._buckets[api_name]
            self._refill(bucket)

            if bucket["tokens"] >= tokens:
                bucket["tokens"] -= tokens
                return 0.0

            # Calculate wait time
            deficit = tokens - bucket["tokens"]
            wait_time = deficit / bucket["refill_rate"]

        # Sleep outside lock
        time.sleep(wait_time)
        waited = wait_time

        with self._lock:
            bucket = self._buckets[api_name]
            self._refill(bucket)
            bucket["tokens"] = max(0, bucket["tokens"] - tokens)

        return waited

    async def acquire_async(self, api_name: str, tokens: int = 1) -> float:
        """Async version of acquire."""
        api_name = api_name.lower()
        if api_name not in self._buckets:
            return 0.0

        with self._lock:
            bucket = self._buckets[api_name]
            self._refill(bucket)

            if bucket["tokens"] >= tokens:
                bucket["tokens"] -= tokens
                return 0.0

            deficit = tokens - bucket["tokens"]
            wait_time = deficit / bucket["refill_rate"]

        await asyncio.sleep(wait_time)

        with self._lock:
            bucket = self._buckets[api_name]
            self._refill(bucket)
            bucket["tokens"] = max(0, bucket["tokens"] - tokens)

        return wait_time

    def available(self, api_name: str) -> float:
        """Return the number of tokens currently available for an API."""
        api_name = api_name.lower()
        if api_name not in self._buckets:
            return float("inf")

        with self._lock:
            bucket = self._buckets[api_name]
            self._refill(bucket)
            return bucket["tokens"]
