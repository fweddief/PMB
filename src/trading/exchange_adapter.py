"""
Abstract interface for exchange adapters used by MomentumTrader.

Each exchange (Polymarket, Kalshi, etc.) implements these 6 methods
so the strategy logic stays exchange-agnostic.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional


class ExchangeAdapter(ABC):
    """Minimal interface that MomentumTrader calls."""

    exchange_name: str = "unknown"

    @abstractmethod
    def discover_btc_15m_markets(self, now: datetime) -> List[dict]:
        """Return newly discovered BTC 15-min Up/Down markets.

        Each dict must contain:
            yes_token:  str  – identifier for the Up / Yes side
            no_token:   str  – identifier for the Down / No side
            question:   str  – human-readable label
            end_date:   datetime (UTC-aware)
            slug:       str  – unique key to prevent re-entry
        """

    @abstractmethod
    def get_order_book(self, token_id: str) -> dict:
        """Return order book for *token_id*.

        Must return:
            {"asks": [{"price": float, "size": float}, ...],
             "bids": [{"price": float, "size": float}, ...]}
        Prices in dollars (0.00–1.00).
        """

    @abstractmethod
    def get_balance_cash(self) -> float:
        """Return available USD cash balance."""

    @abstractmethod
    def buy(self, token_id: str, price: float, shares: int) -> dict:
        """Place a buy order.

        Returns:
            {"shares": int, "cost": float, "price_per_share": float}
        """

    @abstractmethod
    def sell(self, token_id: str, shares: int, price: float) -> Optional[dict]:
        """Place a sell order.  Returns order result or None on failure."""

    @abstractmethod
    def ensure_ready_to_trade(self, token_id: str) -> bool:
        """Pre-trade setup (token allowance on Poly, no-op on Kalshi)."""

    def check_settlement(self, token_id: str) -> Optional[str]:
        """Check if market settled. Returns 'yes', 'no', or None if not yet settled."""
        return None
