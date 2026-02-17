"""
Paginated scanner for all active Polymarket markets via the Gamma API.

Discovers 500-1000+ markets per scan, filters by liquidity,
and returns full market data for classification and estimation.
"""

import logging
import os
import requests
from datetime import datetime
from typing import Dict, List, Optional

from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

GAMMA_API_URL = os.getenv("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "500"))


class MarketScanner:
    """
    Paginated scan of all active Polymarket markets via the Gamma API.

    Filters out markets with low liquidity and returns structured data
    suitable for classification and estimation.
    """

    def __init__(self, rate_limiter: RateLimiter = None):
        self.gamma_url = GAMMA_API_URL
        self.rate_limiter = rate_limiter or RateLimiter()
        self.min_liquidity = MIN_LIQUIDITY

    def scan_all_markets(self, min_liquidity: float = None) -> List[Dict]:
        """
        Paginated scan of all active, open markets.

        Args:
            min_liquidity: Minimum liquidity in USD to include (default from env/500).

        Returns:
            List of market dicts with full data.
        """
        if min_liquidity is None:
            min_liquidity = self.min_liquidity

        all_markets = []
        offset = 0
        page_size = 100
        max_pages = 20  # Safety cap: 2000 events max

        logger.info("Starting full market scan (min_liquidity=$%.0f)...", min_liquidity)

        for page in range(max_pages):
            self.rate_limiter.acquire("gamma")

            params = {
                "limit": page_size,
                "offset": offset,
                "active": "true",
                "closed": "false",
            }

            try:
                resp = requests.get(
                    f"{self.gamma_url}/events",
                    params=params,
                    timeout=15,
                )
                resp.raise_for_status()
                events = resp.json()
            except Exception as e:
                logger.error("Gamma API error at offset %d: %s", offset, e)
                break

            if not events:
                break

            for event in events:
                parsed = self._parse_event(event)
                if parsed and parsed["total_liquidity"] >= min_liquidity:
                    all_markets.append(parsed)

            logger.debug(
                "Page %d: fetched %d events, %d total qualifying markets",
                page + 1,
                len(events),
                len(all_markets),
            )

            if len(events) < page_size:
                break  # Last page

            offset += page_size

        logger.info(
            "Scan complete: %d qualifying markets from %d pages",
            len(all_markets),
            min(page + 1, max_pages),
        )
        return all_markets

    def refresh_prices(self, market_ids: List[str]) -> Dict[str, List[Dict]]:
        """
        Fetch fresh prices for a list of market IDs.

        Returns:
            Dict mapping market_id -> list of outcome price dicts.
        """
        results = {}
        for market_id in market_ids:
            self.rate_limiter.acquire("gamma")
            try:
                resp = requests.get(
                    f"{self.gamma_url}/events",
                    params={"id": market_id},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                event = data[0] if isinstance(data, list) and data else data
                if event:
                    outcomes = self._parse_outcomes(event)
                    results[market_id] = outcomes
            except Exception as e:
                logger.warning("Price refresh failed for %s: %s", market_id, e)

        return results

    def _parse_event(self, event: Dict) -> Optional[Dict]:
        """Parse a Gamma API event into our standardized market dict."""
        try:
            event_id = str(event.get("id", ""))
            title = event.get("title") or event.get("question") or ""
            description = event.get("description", "")
            slug = event.get("slug", "")

            # Aggregate liquidity and volume across all sub-markets
            sub_markets = event.get("markets", [])
            total_liquidity = 0.0
            total_volume_24h = 0.0
            outcomes = []

            for mkt in sub_markets:
                liq = self._safe_float(mkt.get("liquidity", 0))
                vol = self._safe_float(mkt.get("volume24hr", 0))
                total_liquidity += liq
                total_volume_24h += vol

                # Parse outcome prices
                outcome_prices_str = mkt.get("outcomePrices", '["0.5","0.5"]')
                try:
                    outcome_prices = eval(outcome_prices_str)
                    price = float(outcome_prices[0]) if outcome_prices else 0.5
                except Exception:
                    price = 0.5

                # Parse CLOB token IDs
                clob_ids_str = mkt.get("clobTokenIds", "[]")
                try:
                    clob_ids = eval(clob_ids_str)
                    token_id = clob_ids[0] if clob_ids else mkt.get("id", "")
                except Exception:
                    token_id = mkt.get("id", "")

                outcomes.append({
                    "outcome_id": str(token_id),
                    "outcome_text": mkt.get("groupItemTitle") or mkt.get("question", ""),
                    "price": price,
                    "liquidity": liq,
                    "volume_24h": vol,
                    "market_sub_id": mkt.get("id", ""),
                })

            # Also use event-level liquidity/volume if available
            event_liq = self._safe_float(event.get("liquidity", 0))
            event_vol = self._safe_float(event.get("volume", 0))
            if event_liq > total_liquidity:
                total_liquidity = event_liq
            if event_vol > total_volume_24h:
                total_volume_24h = event_vol

            end_date_str = event.get("endDate") or event.get("end_date_iso", "")
            start_date_str = event.get("startDate") or event.get("start_date_iso", "")

            return {
                "market_id": event_id,
                "title": title,
                "description": description,
                "slug": slug,
                "outcomes": outcomes,
                "total_liquidity": total_liquidity,
                "total_volume_24h": total_volume_24h,
                "end_date": end_date_str,
                "start_date": start_date_str,
                "active": event.get("active", True),
                "scanned_at": datetime.utcnow(),
            }

        except Exception as e:
            logger.warning("Failed to parse event: %s", e)
            return None

    def _parse_outcomes(self, event: Dict) -> List[Dict]:
        """Parse outcomes from a single event for price refresh."""
        outcomes = []
        for mkt in event.get("markets", []):
            outcome_prices_str = mkt.get("outcomePrices", '["0.5","0.5"]')
            try:
                outcome_prices = eval(outcome_prices_str)
                price = float(outcome_prices[0]) if outcome_prices else 0.5
            except Exception:
                price = 0.5

            clob_ids_str = mkt.get("clobTokenIds", "[]")
            try:
                clob_ids = eval(clob_ids_str)
                token_id = clob_ids[0] if clob_ids else mkt.get("id", "")
            except Exception:
                token_id = mkt.get("id", "")

            outcomes.append({
                "outcome_id": str(token_id),
                "outcome_text": mkt.get("groupItemTitle") or mkt.get("question", ""),
                "price": price,
            })
        return outcomes

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0
