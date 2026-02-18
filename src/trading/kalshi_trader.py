"""
Kalshi adapter — trades BTC 15-min Up/Down markets on Kalshi
through the ExchangeAdapter interface.

Series ticker: KXBTC15M
Each market is a single ticker (e.g. KXBTC15M-26FEB171645).
  Yes = Up, No = Down.

Kalshi prices are in integer cents (1-99).  This adapter converts
to/from dollars so strategy thresholds work unchanged.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from trading.exchange_adapter import ExchangeAdapter

logger = logging.getLogger(__name__)

KALSHI_SERIES = "KXBTC15M"


class KalshiAdapter(ExchangeAdapter):
    exchange_name = "kalshi"

    def __init__(self):
        # Fix SSL cert verification on macOS / Python 3.13+
        if not os.environ.get("SSL_CERT_FILE"):
            try:
                import certifi
                os.environ["SSL_CERT_FILE"] = certifi.where()
            except ImportError:
                pass

        from kalshi_python import Configuration, KalshiClient

        api_key_id = os.getenv("KALSHI_API_KEY_ID")
        pem_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_key.pem")

        if not api_key_id:
            raise RuntimeError("KALSHI_API_KEY_ID not set in environment")

        with open(pem_path, "r") as f:
            private_key_pem = f.read()

        config = Configuration(
            host="https://api.elections.kalshi.com/trade-api/v2",
        )
        config.api_key_id = api_key_id
        config.private_key_pem = private_key_pem

        self.client = KalshiClient(config)
        logger.info("KalshiAdapter initialized  (key=%s...)", api_key_id[:8])

    # ---- market discovery ----

    def discover_btc_15m_markets(self, now: datetime) -> List[dict]:
        """Discover open BTC 15-min markets from Kalshi series KXBTC15M."""
        try:
            resp = self.client.get_markets(
                series_ticker=KALSHI_SERIES,
                status="open",
                limit=20,
            )
        except Exception as e:
            logger.error("Kalshi get_markets failed: %s", e)
            return []

        markets_data = resp.markets if hasattr(resp, "markets") else []
        new_markets = []

        for mkt in markets_data:
            ticker = mkt.ticker if hasattr(mkt, "ticker") else mkt.get("ticker", "")
            if not ticker:
                continue

            # Parse close time
            close_time = None
            if hasattr(mkt, "close_time") and mkt.close_time:
                close_time = mkt.close_time
            elif hasattr(mkt, "expected_expiration_time") and mkt.expected_expiration_time:
                close_time = mkt.expected_expiration_time

            if isinstance(close_time, str):
                try:
                    end_date = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    end_date = now + timedelta(minutes=15)
            elif isinstance(close_time, datetime):
                end_date = close_time if close_time.tzinfo else close_time.replace(tzinfo=timezone.utc)
            else:
                end_date = now + timedelta(minutes=15)

            title = ""
            if hasattr(mkt, "title"):
                title = mkt.title or ""
            elif hasattr(mkt, "subtitle"):
                title = mkt.subtitle or ""
            if not title:
                title = f"BTC 15m ({ticker})"

            # On Kalshi a single ticker covers both Yes (Up) and No (Down).
            # We encode the side into the token_id as "TICKER:yes" / "TICKER:no"
            # so the strategy can distinguish them.
            new_markets.append(
                {
                    "yes_token": f"{ticker}:yes",
                    "no_token": f"{ticker}:no",
                    "question": title,
                    "end_date": end_date,
                    "slug": ticker,  # unique per window
                }
            )

        return new_markets

    # ---- order book ----

    def get_order_book(self, token_id: str) -> dict:
        """Fetch order book for a Kalshi token_id ("TICKER:yes" or "TICKER:no").

        Kalshi returns yes_bids and no_bids only (no explicit asks).
        For the Yes side:  bids = yes_bids,  asks are implied from no_bids
        For the No side:   bids = no_bids,   asks are implied from yes_bids
        Prices converted from cents to dollars.
        """
        ticker, side = self._parse_token(token_id)

        try:
            resp = self.client.get_market_orderbook(ticker=ticker, depth=20)
        except Exception as e:
            logger.error("Kalshi get_market_orderbook(%s) failed: %s", ticker, e)
            return {"asks": [], "bids": []}

        yes_bids = []
        no_bids = []

        if hasattr(resp, "orderbook"):
            ob = resp.orderbook
            yes_bids = getattr(ob, "yes", None) or []
            no_bids = getattr(ob, "no", None) or []
        else:
            yes_bids = getattr(resp, "yes", None) or []
            no_bids = getattr(resp, "no", None) or []

        def _convert(levels):
            """Convert list of [price_cents, size] pairs to dicts in dollars."""
            out = []
            for item in levels:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    out.append({"price": item[0] / 100.0, "size": float(item[1])})
                elif hasattr(item, "price"):
                    out.append({"price": item.price / 100.0, "size": float(item.quantity if hasattr(item, "quantity") else item.size)})
            return out

        if side == "yes":
            bids = _convert(yes_bids)
            # Asks for Yes side = 1 - no_bid_price (each no bid is implicitly a yes ask)
            asks = [{"price": 1.0 - nb["price"], "size": nb["size"]} for nb in _convert(no_bids)]
        else:
            bids = _convert(no_bids)
            asks = [{"price": 1.0 - yb["price"], "size": yb["size"]} for yb in _convert(yes_bids)]

        return {"asks": asks, "bids": bids}

    # ---- balance ----

    def get_balance_cash(self) -> float:
        try:
            resp = self.client.get_balance()
            # Balance is in cents
            cents = resp.balance if hasattr(resp, "balance") else 0
            return cents / 100.0
        except Exception as e:
            logger.error("Kalshi get_balance failed: %s", e)
            return 0.0

    # ---- buy ----

    def buy(self, token_id: str, price: float, shares: int) -> dict:
        ticker, side = self._parse_token(token_id)
        price_cents = max(1, min(99, round(price * 100)))

        kwargs = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": shares,
            "type": "limit",
            "time_in_force": "good_till_canceled",
        }
        # Set price on the correct side
        if side == "yes":
            kwargs["yes_price"] = price_cents
        else:
            kwargs["no_price"] = price_cents

        try:
            resp = self.client.create_order(**kwargs)
        except Exception as e:
            logger.error("Kalshi buy order failed: %s", e)
            raise

        order = resp.order if hasattr(resp, "order") else resp
        fill_count = self._get_fill_count(order, shares)
        cost = fill_count * price
        pps = cost / fill_count if fill_count else price

        logger.info("Kalshi BUY  %s %s  |  %d shares @ $%.3f", ticker, side, fill_count, pps)
        return {"shares": fill_count, "cost": cost, "price_per_share": pps, "raw": order}

    # ---- sell ----

    def sell(self, token_id: str, shares: int, price: float) -> Optional[dict]:
        ticker, side = self._parse_token(token_id)
        price_cents = max(1, min(99, round(price * 100)))

        kwargs = {
            "ticker": ticker,
            "action": "sell",
            "side": side,
            "count": shares,
            "type": "limit",
            "time_in_force": "good_till_canceled",
        }
        if side == "yes":
            kwargs["yes_price"] = price_cents
        else:
            kwargs["no_price"] = price_cents

        try:
            resp = self.client.create_order(**kwargs)
            order = resp.order if hasattr(resp, "order") else resp
            logger.info("Kalshi SELL  %s %s  |  %d shares @ $%.3f", ticker, side, shares, price)
            return order if isinstance(order, dict) else {"order_id": getattr(order, "order_id", "unknown")}
        except Exception as e:
            logger.error("Kalshi sell order failed: %s", e)
            return None

    # ---- no-op token allowance ----

    def ensure_ready_to_trade(self, token_id: str) -> bool:
        return True

    # ---- settlement detection ----

    def check_settlement(self, token_id: str) -> Optional[str]:
        """Check if Kalshi market settled. Returns 'yes', 'no', or None."""
        ticker, _ = self._parse_token(token_id)
        try:
            resp = self.client.get_market(ticker=ticker)
            market = resp.market if hasattr(resp, "market") else resp
            status = getattr(market, "status", None)
            result = getattr(market, "result", None)
            if status in ("settled", "determined") and result in ("yes", "no"):
                logger.info("Kalshi market %s settled: result=%s", ticker, result)
                return result
        except Exception as e:
            logger.error("Kalshi get_market(%s) failed: %s", ticker, e)
        return None

    # ---- helpers ----

    @staticmethod
    def _parse_token(token_id: str):
        """Split 'TICKER:yes' into (ticker, side)."""
        parts = token_id.rsplit(":", 1)
        if len(parts) == 2 and parts[1] in ("yes", "no"):
            return parts[0], parts[1]
        return token_id, "yes"

    @staticmethod
    def _get_fill_count(order, default: int) -> int:
        """Extract fill count from order response, falling back to default."""
        for attr in ("fill_count", "fill_count_fp"):
            val = getattr(order, attr, None) if not isinstance(order, dict) else order.get(attr)
            if val is not None:
                try:
                    count = int(float(str(val)))
                    if count > 0:
                        return count
                except (ValueError, TypeError):
                    pass
        return default
