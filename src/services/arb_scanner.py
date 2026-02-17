"""
Bitcoin 5-Minute Momentum Trader for Polymarket.

When either Up or Down reaches $0.87 on the ask, buy that side and ride it
to market resolution ($1.00 payout).  Stop-loss at $0.75 bid.

Starts in observe-only mode by default.  Run with --live to enable execution.

Usage:
    PYTHONPATH=src python3 src/services/arb_scanner.py [--live] [--budget 10]
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

from trading.polymarket_trader import PolymarketTrader
from py_clob_client.clob_types import (
    OrderArgs,
    BookParams,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY, SELL

logger = logging.getLogger(__name__)


class MomentumTrader:
    """Buys the winning side of BTC 5-min markets when price hits $0.87."""

    GAMMA_API = "https://gamma-api.polymarket.com"
    ENTRY_PRICE = 0.87       # Buy when best ask >= this
    MAX_ENTRY_PRICE = 0.95   # Don't buy past this price
    STOP_LOSS = 0.75         # Sell if best bid drops below this
    MARKET_REFRESH_INTERVAL = 60   # seconds
    SCAN_INTERVAL = 1.0      # seconds

    def __init__(self, trader: PolymarketTrader, observe_only: bool = True, per_trade_budget: float = 10.0):
        self.trader = trader
        self.client = trader.client
        self.observe_only = observe_only
        self.per_trade_budget = per_trade_budget
        self.total_spent = 0.0
        self._stop_requested = False
        self.tracked_markets = []
        self.last_market_refresh = 0.0

        # Position tracking — one position at a time
        self.position = None  # {token_id, side, shares, entry_price, market}
        self.traded_slugs = set()  # prevent re-entry into same market

        self.stats = {
            "scans": 0,
            "trades": 0,
            "wins": 0,
            "resolved": 0,
            "total_pnl": 0.0,
            "total_spent": 0.0,
        }

    # ------------------------------------------------------------------
    # Market discovery (unchanged)
    # ------------------------------------------------------------------

    def discover_markets(self):
        """Find active BTC 5-min Up/Down markets by predictable slug."""
        now = datetime.now(timezone.utc)
        now_ts = int(now.timestamp())

        current_boundary = now_ts - (now_ts % 300)
        known_tokens = {m["yes_token"] for m in self.tracked_markets}
        new_markets = []

        for i in range(4):
            window_start = current_boundary + i * 300
            slug = f"btc-updown-5m-{window_start}"

            try:
                resp = requests.get(
                    f"{self.GAMMA_API}/events",
                    params={"slug": slug},
                    timeout=10,
                )
                resp.raise_for_status()
                events = resp.json()
            except Exception as e:
                logger.debug("Slug %s fetch failed: %s", slug, e)
                continue

            if not events:
                continue

            event = events[0] if isinstance(events, list) else events
            for market in event.get("markets", []):
                if not market.get("active", False):
                    continue
                if market.get("closed", True):
                    continue
                if not market.get("acceptingOrders", False):
                    continue

                raw_ids = market.get("clobTokenIds")
                if not raw_ids:
                    continue
                try:
                    clob_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                except (json.JSONDecodeError, TypeError):
                    continue

                if len(clob_ids) < 2:
                    continue

                up_token, down_token = clob_ids[0], clob_ids[1]

                if up_token in known_tokens:
                    continue
                known_tokens.add(up_token)

                end_str = market.get("endDate") or market.get("end_date_iso")
                if end_str:
                    try:
                        end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        end_date = datetime.fromtimestamp(window_start + 300, tz=timezone.utc)
                else:
                    end_date = datetime.fromtimestamp(window_start + 300, tz=timezone.utc)

                new_markets.append(
                    {
                        "yes_token": up_token,
                        "no_token": down_token,
                        "question": market.get("question", f"BTC 5m Up/Down ({slug})"),
                        "end_date": end_date,
                        "condition_id": market.get("conditionId", ""),
                        "slug": slug,
                    }
                )

        if new_markets:
            self.tracked_markets.extend(new_markets)
            for m in new_markets:
                logger.info(
                    "NEW MARKET  %s  |  ends %s  |  UP %s...  DOWN %s...",
                    m["question"],
                    m["end_date"].strftime("%H:%M:%S UTC"),
                    m["yes_token"][:12],
                    m["no_token"][:12],
                )

        # Prune expired markets
        before = len(self.tracked_markets)
        self.tracked_markets = [m for m in self.tracked_markets if m["end_date"] > now]
        pruned = before - len(self.tracked_markets)
        if pruned:
            logger.info("Pruned %d expired market(s)", pruned)

        logger.info("Tracking %d market(s)", len(self.tracked_markets))
        self.last_market_refresh = time.time()

    # ------------------------------------------------------------------
    # Order-book scanning — two modes
    # ------------------------------------------------------------------

    def scan_order_books(self):
        """Scan the active market. Behavior depends on whether we hold a position."""
        if not self.tracked_markets:
            return

        now = datetime.now(timezone.utc)

        if self.position:
            self._monitor_position(now)
        else:
            self._scan_for_entry(now)

        self.stats["scans"] += 1

    def _scan_for_entry(self, now: datetime):
        """No position held — look for entry signal in the last 5 minutes only."""
        active = [
            m for m in self.tracked_markets
            if m["end_date"] - timedelta(minutes=5) <= now < m["end_date"]
            and m["slug"] not in self.traded_slugs
        ]
        if not active:
            return

        market = active[0]  # Focus on the current window

        try:
            books = self.client.get_order_books(
                [
                    BookParams(token_id=market["yes_token"]),
                    BookParams(token_id=market["no_token"]),
                ]
            )
            if len(books) < 2:
                return

            yes_book, no_book = books[0], books[1]

            best_up_ask = None
            best_up_size = 0.0
            if yes_book.asks:
                best = sorted(yes_book.asks, key=lambda o: float(o.price))[0]
                best_up_ask = float(best.price)
                best_up_size = float(best.size)

            best_down_ask = None
            best_down_size = 0.0
            if no_book.asks:
                best = sorted(no_book.asks, key=lambda o: float(o.price))[0]
                best_down_ask = float(best.price)
                best_down_size = float(best.size)

            ends = market["end_date"].strftime("%H:%M")
            up_str = f"${best_up_ask:.3f}" if best_up_ask else "---"
            down_str = f"${best_down_ask:.3f}" if best_down_ask else "---"

            # Check for entry signal
            signal_side = None
            signal_token = None
            signal_price = None
            signal_size = None

            if best_up_ask is not None and self.ENTRY_PRICE <= best_up_ask <= self.MAX_ENTRY_PRICE:
                signal_side = "Up"
                signal_token = market["yes_token"]
                signal_price = best_up_ask
                signal_size = best_up_size
            elif best_down_ask is not None and self.ENTRY_PRICE <= best_down_ask <= self.MAX_ENTRY_PRICE:
                signal_side = "Down"
                signal_token = market["no_token"]
                signal_price = best_down_ask
                signal_size = best_down_size

            if signal_side:
                logger.info(
                    "WATCHING  %s  |  Up %s  Down %s  |  ENTRY SIGNAL: %s @ $%.3f",
                    ends, up_str, down_str, signal_side, signal_price,
                )
                self.execute_entry(market, signal_token, signal_side, signal_price, signal_size)
            else:
                logger.info(
                    "WATCHING  %s  |  Up %s  Down %s  |  no signal",
                    ends, up_str, down_str,
                )

        except Exception as e:
            logger.error("Error scanning %s: %s", market["question"][:50], e)

    def _monitor_position(self, now: datetime):
        """Position held — monitor for stop-loss or resolution."""
        pos = self.position
        market = pos["market"]

        # Check best bid — if >= $0.99, sell for profit
        try:
            books = self.client.get_order_books(
                [BookParams(token_id=pos["token_id"])]
            )
            if books and books[0].bids:
                best_bid = float(sorted(books[0].bids, key=lambda o: float(o.price), reverse=True)[0].price)
            else:
                best_bid = None
        except Exception as e:
            logger.error("Error getting book for sell check: %s", e)
            best_bid = None

        # Sell at $0.99 if bid is there (only try once per position)
        if best_bid is not None and best_bid >= 0.99 and not pos.get("sell_failed"):
            proceeds = pos["shares"] * best_bid
            cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
            pnl = proceeds - cost
            logger.info(
                "SELLING  %s  |  %d shares @ $%.3f  |  proceeds $%.2f  |  P&L %+.2f",
                pos["side"], pos["shares"], best_bid, proceeds, pnl,
            )
            if not self.observe_only:
                # Force re-approve token allowance for selling
                self.trader._approved_tokens.discard(pos["token_id"])
                self.trader.ensure_token_allowance(pos["token_id"])
                sell_result = self.trader.create_market_sell_order(
                    token_id=pos["token_id"],
                    shares=pos["shares"],
                    price=best_bid,
                )
                if sell_result:
                    logger.info("Sell order submitted: %s", sell_result)
                else:
                    logger.warning("Sell order failed — will hold through resolution")
                    pos["sell_failed"] = True
                    return
            self.stats["resolved"] += 1
            self.stats["wins"] += 1
            self.stats["total_pnl"] += pnl
            self.per_trade_budget += pnl  # roll profits into next trade
            logger.info("WIN — sold at $%.2f. Budget now $%.2f. Continuing...", best_bid, self.per_trade_budget)
            self.position = None
            return

        # Check if market has resolved (end_date passed) — position likely lost
        if now >= market["end_date"]:
            cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
            logger.info(
                "EXPIRED  %s @ $%.3f  |  %d shares  |  bid was $%s  |  lost $%.2f",
                pos["side"], pos["entry_price"], pos["shares"],
                f"{best_bid:.3f}" if best_bid is not None else "none",
                cost,
            )
            self.stats["resolved"] += 1
            self.stats["total_pnl"] -= cost
            self.per_trade_budget -= cost
            self.position = None
            if self.per_trade_budget <= 1.0:
                logger.info("Budget depleted ($%.2f). Stopping.", self.per_trade_budget)
                self._stop_requested = True
            return

        # Log holding status and check stop-loss
        if best_bid is not None:
            pnl_per_share = best_bid - pos["entry_price"]
            logger.info(
                "HOLDING  %s @ $%.3f  |  bid $%.3f  |  P&L %+.3f/share",
                pos["side"], pos["entry_price"], best_bid, pnl_per_share,
            )

            if best_bid < self.STOP_LOSS:
                self.execute_stop_loss(best_bid)
        else:
            logger.info(
                "HOLDING  %s @ $%.3f  |  no bids  |  waiting...",
                pos["side"], pos["entry_price"],
            )

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------

    def get_available_cash(self) -> float:
        """Get USDC balance (cash only, not existing positions)."""
        try:
            balance = self.trader.get_balance()
            return float(balance.get("cash", 0))
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return 0.0

    def execute_entry(self, market: dict, token_id: str, side: str, price: float, size: float):
        """Buy the momentum side."""
        if self.observe_only:
            logger.info(
                "OBSERVE  Would buy %s @ $%.3f  (observe-only mode)",
                side, price,
            )
            return

        cash = self.get_available_cash()
        if cash <= 0:
            logger.warning("No available cash for trading")
            return

        # Cap each trade to per_trade_budget
        cash = min(cash, self.per_trade_budget)

        import math
        min_shares = math.ceil(1.0 / price)  # ensure order >= $1 minimum
        shares = int(min(size, cash / price))
        if shares < min_shares:
            logger.warning(
                "Insufficient cash or liquidity (cash=$%.2f, price=$%.3f, avail_size=%.0f)",
                cash, price, size,
            )
            return

        logger.info(
            "BUYING  %s  |  %d shares @ $%.3f  |  cost $%.2f",
            side, shares, price, shares * price,
        )

        try:
            self.trader.ensure_token_allowance(token_id)

            signed_order = self.client.create_order(
                OrderArgs(
                    price=price,
                    size=float(shares),
                    side=BUY,
                    token_id=token_id,
                )
            )
            result = self.client.post_order(signed_order, OrderType.GTC)
            logger.info("Order submitted: %s", result)

            # Use actual fill amounts from the result
            actual_shares = int(float(result.get("takingAmount", shares)))
            actual_cost = float(result.get("makingAmount", shares * price))
            actual_price_per_share = actual_cost / actual_shares if actual_shares > 0 else price

            logger.info(
                "FILL  %s  |  %d shares  |  actual cost $%.2f ($%.3f/share)",
                side, actual_shares, actual_cost, actual_price_per_share,
            )

            # Reject if actual fill price is too low (means we're on the wrong side)
            if actual_price_per_share < 0.50:
                logger.warning(
                    "BAD FILL — actual price $%.3f/share is too low, wrong side. Skipping.",
                    actual_price_per_share,
                )
                return

            self.total_spent += actual_cost
            self.stats["trades"] += 1
            self.stats["total_spent"] += actual_cost

            self.position = {
                "token_id": token_id,
                "side": side,
                "shares": actual_shares,
                "entry_price": actual_price_per_share,
                "actual_cost": actual_cost,
                "market": market,
            }
            self.traded_slugs.add(market["slug"])

        except Exception as e:
            logger.error("Failed to execute entry: %s", e)

    def execute_stop_loss(self, current_bid: float):
        """Sell position to limit loss."""
        if not self.position:
            return

        pos = self.position
        cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
        proceeds = current_bid * pos["shares"]
        total_loss = cost - proceeds

        logger.info(
            "STOP-LOSS  %s @ $%.3f -> $%.3f  |  %d shares  |  loss $%.2f",
            pos["side"], pos["entry_price"], current_bid, pos["shares"], total_loss,
        )

        if self.observe_only:
            logger.info("OBSERVE  Would sell (observe-only mode)")
        else:
            try:
                result = self.trader.create_market_sell_order(
                    token_id=pos["token_id"],
                    shares=pos["shares"],
                    price=current_bid,
                )
                logger.info("Stop-loss sell submitted: %s", result)
            except Exception as e:
                logger.error("Failed to execute stop-loss: %s", e)

        self.stats["resolved"] += 1
        self.stats["total_pnl"] -= total_loss
        self.per_trade_budget -= total_loss
        self.position = None

        if self.per_trade_budget <= 1.0:
            logger.info("Budget depleted ($%.2f). Stopping.", self.per_trade_budget)
            self._stop_requested = True
        else:
            logger.info("LOSS — budget now $%.2f. Continuing...", self.per_trade_budget)

    # ------------------------------------------------------------------
    # Stats & main loop
    # ------------------------------------------------------------------

    def print_stats(self):
        s = self.stats
        logger.info(
            "STATS  |  scans: %d  |  trades: %d  |  wins: %d  |  resolved: %d  |  "
            "spent: $%.2f  |  P&L: %+.2f  |  per-trade: $%.2f",
            s["scans"], s["trades"], s["wins"], s["resolved"],
            s["total_spent"], s["total_pnl"], self.per_trade_budget,
        )

    def run(self):
        """Main loop: discover markets every 60s, scan books every 1s."""
        mode = "OBSERVE ONLY" if self.observe_only else "LIVE TRADING"
        logger.info("Momentum Trader starting  |  mode: %s", mode)
        logger.info(
            "Entry: $%.2f  |  Stop-loss: $%.2f  |  Per-trade: $%.2f  |  scan: %.1fs",
            self.ENTRY_PRICE,
            self.STOP_LOSS,
            self.per_trade_budget,
            self.SCAN_INTERVAL,
        )

        scan_count = 0
        try:
            while not self._stop_requested:
                if time.time() - self.last_market_refresh >= self.MARKET_REFRESH_INTERVAL:
                    self.discover_markets()

                self.scan_order_books()
                scan_count += 1

                if scan_count % 100 == 0:
                    self.print_stats()

                time.sleep(self.SCAN_INTERVAL)

        except KeyboardInterrupt:
            logger.info("\nShutting down...")

        if self._stop_requested:
            logger.info("Bot stopped — budget depleted or manual stop.")
        self.print_stats()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Bitcoin 5-min momentum trader for Polymarket"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading (default: observe only)",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=10.0,
        help="Maximum USD per trade (default: $10)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    load_dotenv()

    logger.info("Initializing PolymarketTrader...")
    trader = PolymarketTrader()

    momentum = MomentumTrader(trader, observe_only=not args.live, per_trade_budget=args.budget)
    momentum.run()


if __name__ == "__main__":
    main()
