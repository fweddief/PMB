"""
GeckoTerminal-Triggered BTC Momentum Trader for Polymarket.

Watches real BTC price via GeckoTerminal, detects spikes/dips, then buys
the corresponding side on Polymarket 5-min Up/Down markets and sells on
a trailing peak (exit when bid drops $0.01 from observed high).

Starts in observe-only mode by default.  Run with --live to enable execution.

Usage:
    PYTHONPATH=src python3 src/services/gecko_momentum.py [--live] [--budget 10]
"""

import os
import sys
import json
import math
import time
import logging
import argparse
from collections import deque
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


class GeckoMomentumTrader:
    """Watches BTC price on GeckoTerminal, trades Polymarket 5-min markets."""

    # GeckoTerminal
    GECKO_API = "https://api.geckoterminal.com/api/v2"
    WBTC_ADDRESS = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
    WBTC_USDC_POOL = "0x9a772018fbd77fcd2d25657e5c547baff3fd7d16"

    # BTC price monitoring — burst mode (30 calls over ~15s, then 45s pause)
    # GeckoTerminal free tier allows ~30 calls/min
    GECKO_BURST_CALLS = 30
    GECKO_BURST_INTERVAL = 0.5
    GECKO_PAUSE_DURATION = 45
    SPIKE_THRESHOLD = 0.003       # 0.3% move = spike signal
    SPIKE_WINDOW = 60             # look at price change over last 60 seconds

    # Polymarket
    GAMMA_API = "https://gamma-api.polymarket.com"
    MAX_ENTRY_PRICE = 0.95
    STOP_LOSS = None              # disabled — rely on trailing exit only
    TRAILING_DROP = 0.01
    MARKET_REFRESH_INTERVAL = 60
    BOOK_POLL_INTERVAL = 0.1

    def __init__(
        self,
        trader: PolymarketTrader,
        observe_only: bool = True,
        per_trade_budget: float = 10.0,
        spike_threshold: float = None,
        spike_window: int = None,
    ):
        self.trader = trader
        self.client = trader.client
        self.observe_only = observe_only
        self.per_trade_budget = per_trade_budget
        self._stop_requested = False

        if spike_threshold is not None:
            self.SPIKE_THRESHOLD = spike_threshold
        if spike_window is not None:
            self.SPIKE_WINDOW = spike_window

        # Price history: deque of (timestamp, price)
        self.price_history: deque = deque()

        # Market tracking
        self.tracked_markets = []
        self.last_market_refresh = 0.0
        self.traded_slugs: set = set()

        # Position tracking — one position at a time
        self.position = None  # {token_id, side, shares, entry_price, market, highest_bid_seen}

        self.stats = {
            "scans": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "resolved": 0,
            "total_pnl": 0.0,
            "total_spent": 0.0,
            "spikes_detected": 0,
            "gecko_calls": 0,
            "gecko_errors": 0,
        }

    # ------------------------------------------------------------------
    # GeckoTerminal price fetching
    # ------------------------------------------------------------------

    def fetch_btc_price(self) -> Optional[float]:
        """Get current BTC price from GeckoTerminal simple price endpoint."""
        try:
            url = f"{self.GECKO_API}/simple/networks/eth/token_price/{self.WBTC_ADDRESS}"
            resp = requests.get(
                url,
                headers={"Accept": "application/json;version=20230302"},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            price = float(data["data"]["attributes"]["token_prices"][self.WBTC_ADDRESS])
            self.stats["gecko_calls"] += 1
            return price
        except Exception as e:
            self.stats["gecko_errors"] += 1
            logger.debug("GeckoTerminal fetch failed: %s", e)
            return None

    def record_price(self, price: float):
        """Add price to rolling window and prune old entries."""
        now = time.time()
        self.price_history.append((now, price))
        # Prune entries older than spike window + buffer
        cutoff = now - self.SPIKE_WINDOW - 10
        while self.price_history and self.price_history[0][0] < cutoff:
            self.price_history.popleft()

    def detect_spike(self) -> Optional[str]:
        """Check if BTC price moved >= SPIKE_THRESHOLD over SPIKE_WINDOW seconds.

        Returns "Up", "Down", or None.
        """
        if len(self.price_history) < 2:
            return None

        now = time.time()
        cutoff = now - self.SPIKE_WINDOW

        # Find oldest price within the window
        oldest_price = None
        for ts, price in self.price_history:
            if ts >= cutoff:
                oldest_price = price
                break

        if oldest_price is None:
            return None

        newest_price = self.price_history[-1][1]
        pct_change = (newest_price - oldest_price) / oldest_price

        if pct_change >= self.SPIKE_THRESHOLD:
            logger.info(
                "SPIKE DETECTED  +%.2f%%  ($%.2f -> $%.2f)  over %ds",
                pct_change * 100, oldest_price, newest_price, self.SPIKE_WINDOW,
            )
            self.stats["spikes_detected"] += 1
            return "Up"
        elif pct_change <= -self.SPIKE_THRESHOLD:
            logger.info(
                "DIP DETECTED  %.2f%%  ($%.2f -> $%.2f)  over %ds",
                pct_change * 100, oldest_price, newest_price, self.SPIKE_WINDOW,
            )
            self.stats["spikes_detected"] += 1
            return "Down"

        return None

    # ------------------------------------------------------------------
    # Market discovery (5-min markets)
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
    # Entry logic — find current market and buy the signaled side
    # ------------------------------------------------------------------

    def find_current_market(self) -> Optional[dict]:
        """Return the best market to trade: active, not yet traded, accepting orders."""
        now = datetime.now(timezone.utc)
        candidates = [
            m for m in self.tracked_markets
            if now < m["end_date"]
            and m["slug"] not in self.traded_slugs
        ]
        if not candidates:
            return None
        # Pick the one expiring soonest (most likely to resolve quickly)
        candidates.sort(key=lambda m: m["end_date"])
        return candidates[0]

    def attempt_entry(self, signal: str):
        """Given a spike signal ('Up' or 'Down'), try to buy the matching side."""
        market = self.find_current_market()
        if not market:
            logger.info("SIGNAL %s but no tradeable market found", signal)
            return

        token_id = market["yes_token"] if signal == "Up" else market["no_token"]

        try:
            books = self.client.get_order_books(
                [BookParams(token_id=token_id)]
            )
            if not books or not books[0].asks:
                logger.info("SIGNAL %s but no asks on %s", signal, signal)
                return

            best_ask = float(sorted(books[0].asks, key=lambda o: float(o.price))[0].price)
            best_ask_size = float(sorted(books[0].asks, key=lambda o: float(o.price))[0].size)

            if best_ask > self.MAX_ENTRY_PRICE:
                logger.info(
                    "SIGNAL %s but ask $%.3f > max $%.3f — skipping",
                    signal, best_ask, self.MAX_ENTRY_PRICE,
                )
                return

            logger.info(
                "ENTRY SIGNAL  %s  |  %s ask $%.3f (%.0f avail)  |  market ends %s",
                signal, signal, best_ask, best_ask_size,
                market["end_date"].strftime("%H:%M:%S UTC"),
            )
            self.execute_entry(market, token_id, signal, best_ask, best_ask_size)

        except Exception as e:
            logger.error("Error checking book for entry: %s", e)

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------

    def get_available_cash(self) -> float:
        """Get USDC balance."""
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
            # Create simulated position so we can monitor trailing exit logic
            self.position = {
                "token_id": token_id,
                "side": side,
                "shares": 0,
                "entry_price": price,
                "actual_cost": 0.0,
                "market": market,
                "highest_bid_seen": price,
                "simulated": True,
            }
            self.traded_slugs.add(market["slug"])
            return

        cash = self.get_available_cash()
        if cash <= 0:
            logger.warning("No available cash for trading")
            return

        cash = min(cash, self.per_trade_budget)

        min_shares = math.ceil(1.0 / price)
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

            actual_shares = int(float(result.get("takingAmount", shares)))
            actual_cost = float(result.get("makingAmount", shares * price))
            actual_price = actual_cost / actual_shares if actual_shares > 0 else price

            logger.info(
                "FILL  %s  |  %d shares  |  actual cost $%.2f ($%.3f/share)",
                side, actual_shares, actual_cost, actual_price,
            )

            if actual_price < 0.50:
                logger.warning(
                    "BAD FILL — actual price $%.3f/share is too low. Skipping.",
                    actual_price,
                )
                return

            self.stats["trades"] += 1
            self.stats["total_spent"] += actual_cost

            self.position = {
                "token_id": token_id,
                "side": side,
                "shares": actual_shares,
                "entry_price": actual_price,
                "actual_cost": actual_cost,
                "market": market,
                "highest_bid_seen": 0.0,
            }
            self.traded_slugs.add(market["slug"])

        except Exception as e:
            logger.error("Failed to execute entry: %s", e)

    def execute_sell(self, bid_price: float, reason: str):
        """Sell current position."""
        if not self.position:
            return

        pos = self.position
        cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
        proceeds = bid_price * pos["shares"]
        pnl = proceeds - cost

        logger.info(
            "SELLING  %s  |  %d shares @ $%.3f  |  proceeds $%.2f  |  P&L %+.2f  |  %s",
            pos["side"], pos["shares"], bid_price, proceeds, pnl, reason,
        )

        if not self.observe_only:
            try:
                self.trader._approved_tokens.discard(pos["token_id"])
                self.trader.ensure_token_allowance(pos["token_id"])
                sell_result = self.trader.create_market_sell_order(
                    token_id=pos["token_id"],
                    shares=pos["shares"],
                    price=bid_price,
                )
                if sell_result:
                    logger.info("Sell order submitted: %s", sell_result)
                else:
                    logger.warning("Sell order failed — will hold through resolution")
                    return
            except Exception as e:
                logger.error("Failed to sell: %s", e)
                return

        self.stats["resolved"] += 1
        self.stats["total_pnl"] += pnl
        if pnl >= 0:
            self.stats["wins"] += 1
        else:
            self.stats["losses"] += 1
        self.per_trade_budget += pnl
        logger.info("EXIT — P&L %+.2f. Budget now $%.2f.", pnl, self.per_trade_budget)
        self.position = None

    # ------------------------------------------------------------------
    # Position monitoring — trailing peak exit
    # ------------------------------------------------------------------

    def monitor_position(self):
        """Poll Polymarket bid and apply trailing exit logic."""
        if not self.position:
            return

        pos = self.position
        market = pos["market"]
        now = datetime.now(timezone.utc)

        # Check if market expired
        if now >= market["end_date"]:
            cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
            logger.info(
                "EXPIRED  %s @ $%.3f  |  %d shares  |  lost $%.2f",
                pos["side"], pos["entry_price"], pos["shares"], cost,
            )
            if not pos.get("simulated"):
                self.stats["resolved"] += 1
                self.stats["losses"] += 1
                self.stats["total_pnl"] -= cost
                self.per_trade_budget -= cost
            else:
                logger.info("OBSERVE  Simulated position expired — no real loss")
            self.position = None
            if self.per_trade_budget <= 1.0:
                logger.info("Budget depleted ($%.2f). Stopping.", self.per_trade_budget)
                self._stop_requested = True
            return

        # Get best bid
        try:
            books = self.client.get_order_books(
                [BookParams(token_id=pos["token_id"])]
            )
            if books and books[0].bids:
                best_bid = float(sorted(books[0].bids, key=lambda o: float(o.price), reverse=True)[0].price)
            else:
                best_bid = None
        except Exception as e:
            logger.error("Error getting bid: %s", e)
            return

        if best_bid is None:
            logger.info(
                "HOLDING  %s @ $%.3f  |  no bids  |  peak $%.3f",
                pos["side"], pos["entry_price"], pos["highest_bid_seen"],
            )
            return

        # Update peak
        if best_bid > pos["highest_bid_seen"]:
            pos["highest_bid_seen"] = best_bid

        pnl_per_share = best_bid - pos["entry_price"]
        logger.info(
            "HOLDING  %s @ $%.3f  |  bid $%.3f  |  peak $%.3f  |  P&L %+.3f/share",
            pos["side"], pos["entry_price"], best_bid, pos["highest_bid_seen"], pnl_per_share,
        )

        # Exit conditions
        if best_bid >= 0.99:
            self.execute_sell(best_bid, reason="NEAR PAYOUT")
        elif best_bid < pos["highest_bid_seen"] - self.TRAILING_DROP:
            self.execute_sell(best_bid, reason=f"TRAILING EXIT (peak was ${pos['highest_bid_seen']:.3f})")
        elif self.STOP_LOSS is not None and best_bid < self.STOP_LOSS:
            self.execute_sell(best_bid, reason="STOP LOSS")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def print_stats(self):
        s = self.stats
        logger.info(
            "STATS  |  trades: %d  |  wins: %d  |  losses: %d  |  spikes: %d  |  "
            "spent: $%.2f  |  P&L: %+.2f  |  budget: $%.2f  |  gecko: %d calls (%d err)",
            s["trades"], s["wins"], s["losses"], s["spikes_detected"],
            s["total_spent"], s["total_pnl"], self.per_trade_budget,
            s["gecko_calls"], s["gecko_errors"],
        )

    # ------------------------------------------------------------------
    # Main loop — burst mode
    # ------------------------------------------------------------------

    def run(self):
        """Main loop: burst-poll GeckoTerminal, then pause. Monitor position at 0.1s."""
        mode = "OBSERVE ONLY" if self.observe_only else "LIVE TRADING"
        logger.info("GeckoMomentum Trader starting  |  mode: %s", mode)
        logger.info(
            "Spike: %.1f%% over %ds  |  Trailing drop: $%.2f  |  Max entry: $%.2f  |  Budget: $%.2f",
            self.SPIKE_THRESHOLD * 100,
            self.SPIKE_WINDOW,
            self.TRAILING_DROP,
            self.MAX_ENTRY_PRICE,
            self.per_trade_budget,
        )

        cycle_count = 0

        try:
            while not self._stop_requested:
                # Refresh markets periodically
                if time.time() - self.last_market_refresh >= self.MARKET_REFRESH_INTERVAL:
                    self.discover_markets()

                if self.position:
                    # MODE 2: Holding — fast poll Polymarket book
                    self.monitor_position()
                    time.sleep(self.BOOK_POLL_INTERVAL)
                else:
                    # MODE 1: Watching BTC price — burst mode
                    self._run_burst_cycle()

                cycle_count += 1
                if cycle_count % 200 == 0:
                    self.print_stats()

        except KeyboardInterrupt:
            logger.info("\nShutting down...")

        if self._stop_requested:
            logger.info("Bot stopped — budget depleted or manual stop.")
        self.print_stats()

    def _run_burst_cycle(self):
        """Fire GECKO_BURST_CALLS over ~10s, then pause ~50s.

        During both phases, check for spike signals and monitor position if entered.
        """
        # --- Burst phase ---
        logger.debug("Burst phase: %d calls over %.0fs", self.GECKO_BURST_CALLS,
                      self.GECKO_BURST_CALLS * self.GECKO_BURST_INTERVAL)

        for i in range(self.GECKO_BURST_CALLS):
            if self._stop_requested or self.position:
                return  # exit burst if we entered a position or stopping

            price = self.fetch_btc_price()
            if price is not None:
                self.record_price(price)

                # Log every 5th call to avoid spam
                if i % 5 == 0:
                    pct = self._current_pct_change()
                    pct_str = f"{pct:+.3f}%" if pct is not None else "n/a"
                    logger.info(
                        "BTC $%.2f  |  %d prices  |  %s change  |  call %d/%d",
                        price, len(self.price_history), pct_str,
                        i + 1, self.GECKO_BURST_CALLS,
                    )

                signal = self.detect_spike()
                if signal:
                    self.attempt_entry(signal)
                    if self.position:
                        return  # switch to position monitoring

            time.sleep(self.GECKO_BURST_INTERVAL)

        # --- Pause phase ---
        logger.debug("Pause phase: %.0fs", self.GECKO_PAUSE_DURATION)
        pause_end = time.time() + self.GECKO_PAUSE_DURATION

        while time.time() < pause_end:
            if self._stop_requested:
                return

            # Refresh markets if needed during pause
            if time.time() - self.last_market_refresh >= self.MARKET_REFRESH_INTERVAL:
                self.discover_markets()

            # Still check for spikes using existing price history
            # (no new gecko calls, but window may trigger differently as time moves)

            time.sleep(1.0)

    def _current_pct_change(self) -> Optional[float]:
        """Compute current % change over the spike window."""
        if len(self.price_history) < 2:
            return None

        now = time.time()
        cutoff = now - self.SPIKE_WINDOW

        oldest_price = None
        for ts, price in self.price_history:
            if ts >= cutoff:
                oldest_price = price
                break

        if oldest_price is None:
            return None

        newest_price = self.price_history[-1][1]
        return ((newest_price - oldest_price) / oldest_price) * 100


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="GeckoTerminal-triggered BTC momentum trader for Polymarket"
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
    parser.add_argument(
        "--spike-threshold",
        type=float,
        default=None,
        help="Override spike threshold (default: 0.003 = 0.3%%)",
    )
    parser.add_argument(
        "--spike-window",
        type=int,
        default=None,
        help="Override spike lookback window in seconds (default: 60)",
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

    gecko = GeckoMomentumTrader(
        trader,
        observe_only=not args.live,
        per_trade_budget=args.budget,
        spike_threshold=args.spike_threshold,
        spike_window=args.spike_window,
    )
    gecko.run()


if __name__ == "__main__":
    main()
