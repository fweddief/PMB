"""
Bitcoin 15-Minute Momentum Trader (exchange-agnostic).

When either Up or Down reaches $0.87 on the ask, buy that side and ride it
to market resolution ($1.00 payout).  Stop-loss at $0.75 bid.

Supports both Polymarket and Kalshi via the ExchangeAdapter interface.

Starts in observe-only mode by default.  Run with --live to enable execution.

Usage:
    PYTHONPATH=src python3 src/services/arb_scanner_15m.py [--live] [--budget 10] [--exchange polymarket|kalshi]
"""

import time
import logging
import argparse
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from trading.exchange_adapter import ExchangeAdapter

logger = logging.getLogger(__name__)


class MomentumTrader:
    """Buys the winning side of BTC 15-min markets when price hits $0.87."""

    ENTRY_PRICE = 0.87       # Buy when best ask >= this
    MAX_ENTRY_PRICE = 0.95   # Don't buy past this price
    STOP_LOSS = 0.75         # Sell if best bid drops below this
    FILL_SLIPPAGE_TOLERANCE = 0.02  # Allow fills up to 2¢ below signal price
    MARKET_REFRESH_INTERVAL = 60   # seconds
    SCAN_INTERVAL = 1.0      # seconds

    def __init__(self, exchange: ExchangeAdapter, observe_only: bool = True, per_trade_budget: float = 10.0):
        self.exchange = exchange
        self.observe_only = observe_only
        self.per_trade_budget = per_trade_budget
        self._initial_budget = per_trade_budget  # fixed cap per trade
        self.total_spent = 0.0
        self._stop_requested = False
        self.tracked_markets = []
        self.last_market_refresh = 0.0

        # Position tracking — one position at a time
        self.position = None  # {token_id, side, shares, entry_price, market}
        self.traded_slugs = set()  # prevent re-entry into same market
        self.local_cash = None  # shadow balance — initialized from first on-chain read

        self.stats = {
            "scans": 0,
            "trades": 0,
            "wins": 0,
            "resolved": 0,
            "total_pnl": 0.0,
            "total_spent": 0.0,
        }

    # ------------------------------------------------------------------
    # Market discovery
    # ------------------------------------------------------------------

    def discover_markets(self):
        """Find active BTC 15-min Up/Down markets via the exchange adapter."""
        now = datetime.now(timezone.utc)
        known_tokens = {m["yes_token"] for m in self.tracked_markets}

        new_markets = self.exchange.discover_btc_15m_markets(now)

        # Deduplicate against already-tracked markets
        added = []
        for m in new_markets:
            if m["yes_token"] not in known_tokens:
                known_tokens.add(m["yes_token"])
                added.append(m)

        if added:
            self.tracked_markets.extend(added)
            for m in added:
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
            yes_book = self.exchange.get_order_book(market["yes_token"])
            no_book = self.exchange.get_order_book(market["no_token"])

            best_up_ask = None
            best_up_size = 0.0
            if yes_book["asks"]:
                best = sorted(yes_book["asks"], key=lambda o: o["price"])[0]
                best_up_ask = best["price"]
                best_up_size = best["size"]

            best_down_ask = None
            best_down_size = 0.0
            if no_book["asks"]:
                best = sorted(no_book["asks"], key=lambda o: o["price"])[0]
                best_down_ask = best["price"]
                best_down_size = best["size"]

            # Sanity check: Up + Down asks should be close to $1.00
            if best_up_ask and best_down_ask:
                total = best_up_ask + best_down_ask
                if not (0.90 <= total <= 1.15):
                    logger.warning(
                        "BOOK SANITY FAIL  Up=$%.3f + Down=$%.3f = $%.3f (expected ~$1.00) — skipping",
                        best_up_ask, best_down_ask, total,
                    )
                    return

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

        # Check best bid
        try:
            book = self.exchange.get_order_book(pos["token_id"])
            if book["bids"]:
                best_bid = max(b["price"] for b in book["bids"])
            else:
                best_bid = None
        except Exception as e:
            logger.error("Error getting book for sell check: %s", e)
            best_bid = None

        # Sell at $0.97+ if bid is there
        if best_bid is not None and best_bid >= 0.97 and not pos.get("sell_failed"):
            proceeds = pos["shares"] * best_bid
            cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
            pnl = proceeds - cost
            logger.info(
                "SELLING  %s  |  %d shares @ $%.3f  |  proceeds $%.2f  |  P&L %+.2f",
                pos["side"], pos["shares"], best_bid, proceeds, pnl,
            )
            if not self.observe_only:
                sold = False
                for attempt in range(1, 6):
                    try:
                        sell_result = self.exchange.sell(
                            token_id=pos["token_id"],
                            shares=pos["shares"],
                            price=best_bid,
                        )
                        if sell_result:
                            logger.info("Sell order submitted: %s", sell_result)
                            sold = True
                            break
                        else:
                            logger.warning("Sell returned None (attempt %d/5)", attempt)
                    except Exception as e:
                        logger.error("Sell attempt %d/5 failed: %s", attempt, e)
                    if attempt < 5:
                        time.sleep(0.5)
                if not sold:
                    logger.warning("All sell attempts failed — will hold through resolution")
                    pos["sell_failed"] = True
                    return
            self.stats["resolved"] += 1
            self.stats["wins"] += 1
            self.stats["total_pnl"] += pnl
            self.per_trade_budget += pnl  # roll profits into next trade
            self.local_cash = (self.local_cash or 0) + proceeds
            logger.info("WIN — sold at $%.2f. Budget now $%.2f. Continuing...", best_bid, self.per_trade_budget)
            self.position = None
            return

        # Market expiring — check settlement first, then force-sell as fallback
        if now >= market["end_date"]:
            # Check if market already settled (Kalshi auto-settles at $1.00)
            settlement = self.exchange.check_settlement(pos["token_id"])
            if settlement is not None:
                token_side = "yes" if pos["token_id"] == market["yes_token"] else "no"
                won = (settlement == token_side)
                payout = pos["shares"] * 1.00 if won else 0.0
                cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
                pnl = payout - cost

                logger.info(
                    "SETTLED  %s  |  result=%s  our_side=%s  |  %s  |  payout $%.2f  P&L %+.2f",
                    pos["side"], settlement, token_side,
                    "WIN" if won else "LOSS", payout, pnl,
                )

                self.stats["resolved"] += 1
                if won:
                    self.stats["wins"] += 1
                    # Trigger on-chain redemption for winning positions
                    condition_id = market.get("condition_id", "")
                    is_yes = (pos["token_id"] == market["yes_token"])
                    if condition_id and hasattr(self.exchange, "redeem_position"):
                        try:
                            self.exchange.redeem_position(condition_id, is_yes)
                        except Exception as exc:
                            logger.warning("Auto-redeem failed — claim manually: %s", exc)
                self.stats["total_pnl"] += pnl
                self.per_trade_budget += pnl
                self.local_cash = (self.local_cash or 0) + payout
                self.position = None
                return

            logger.info(
                "MARKET EXPIRING — force-selling  %s @ $%.3f  |  %d shares  |  bid $%s",
                pos["side"], pos["entry_price"], pos["shares"],
                f"{best_bid:.3f}" if best_bid is not None else "none",
            )
            cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])
            if best_bid is not None and best_bid > 0 and not self.observe_only:
                sold = False
                for attempt in range(1, 6):
                    try:
                        sell_result = self.exchange.sell(
                            token_id=pos["token_id"],
                            shares=pos["shares"],
                            price=best_bid,
                        )
                        if sell_result:
                            logger.info("Expiry sell submitted: %s", sell_result)
                            sold = True
                            break
                        else:
                            logger.warning("Expiry sell returned None (attempt %d/5)", attempt)
                    except Exception as e:
                        logger.error("Expiry sell attempt %d/5 failed: %s", attempt, e)
                    if attempt < 5:
                        time.sleep(0.5)
                if sold:
                    proceeds = pos["shares"] * best_bid
                    pnl = proceeds - cost
                    self.stats["resolved"] += 1
                    if pnl >= 0:
                        self.stats["wins"] += 1
                    self.stats["total_pnl"] += pnl
                    self.per_trade_budget += pnl
                    self.local_cash = (self.local_cash or 0) + proceeds
                    logger.info("Expiry sell OK — P&L %+.2f. Budget now $%.2f", pnl, self.per_trade_budget)
                else:
                    logger.warning("All expiry sell attempts failed — clearing position as loss")
                    pnl = -cost
                    self.stats["resolved"] += 1
                    self.stats["total_pnl"] += pnl
                    self.per_trade_budget += pnl
            else:
                if self.observe_only:
                    logger.info("Observe-only: would force-sell at expiry")
                else:
                    logger.warning("No bid available at expiry — clearing position as loss")
                    pnl = -cost
                    self.stats["resolved"] += 1
                    self.stats["total_pnl"] += pnl
                    self.per_trade_budget += pnl
            self.position = None
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
        """Get USD balance via the exchange adapter."""
        try:
            return self.exchange.get_balance_cash()
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return 0.0

    def execute_entry(self, market: dict, token_id: str, side: str, price: float, size: float):
        """Buy the momentum side."""
        # Hard price guard — refuse to even attempt orders outside our range
        if not (self.ENTRY_PRICE <= price <= self.MAX_ENTRY_PRICE):
            logger.warning(
                "REJECTED  %s @ $%.3f — outside allowed range $%.2f-$%.2f",
                side, price, self.ENTRY_PRICE, self.MAX_ENTRY_PRICE,
            )
            return

        if self.observe_only:
            logger.info(
                "OBSERVE  Would buy %s @ $%.3f  (observe-only mode)",
                side, price,
            )
            return

        on_chain_cash = self.get_available_cash()
        if on_chain_cash > 0:
            # Always sync to live wallet balance when available
            self.local_cash = on_chain_cash
        elif self.local_cash is None:
            # Proxy wallets may return 0 — fall back to initial budget on first run
            self.local_cash = self._initial_budget
        cash = min(self.local_cash, self._initial_budget)
        if cash <= 0:
            logger.warning("No available cash (on-chain=$%.2f, local=$%.2f, budget=$%.2f)",
                           on_chain_cash, self.local_cash, self._initial_budget)
            return

        import math
        min_shares = max(5, math.ceil(1.0 / price))  # Polymarket minimum is 5 shares
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

        # Lock out re-entry immediately (before order goes on-chain)
        self.traded_slugs.add(market["slug"])

        try:
            self.exchange.ensure_ready_to_trade(token_id)

            result = self.exchange.buy(token_id, price, shares)
            logger.info("Order submitted: %s", result)

            actual_shares = result["shares"]
            actual_cost = result["cost"]
            actual_price_per_share = result["price_per_share"]

            logger.info(
                "FILL  %s  |  %d shares  |  actual cost $%.2f ($%.3f/share)",
                side, actual_shares, actual_cost, actual_price_per_share,
            )

            # Reject only if fill is way below entry price (wrong-side fill, not slippage).
            # Allow up to FILL_SLIPPAGE_TOLERANCE below signal price for normal market movement.
            if actual_price_per_share < self.ENTRY_PRICE - self.FILL_SLIPPAGE_TOLERANCE:
                logger.warning(
                    "BAD FILL — actual price $%.3f/share is below $%.2f entry threshold. "
                    "Likely filled on wrong side. Shares may be stranded!",
                    actual_price_per_share, self.ENTRY_PRICE,
                )
                return

            self.local_cash -= actual_cost
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

        except Exception as e:
            logger.error("Failed to execute entry for %s: %s", market["slug"], e)
            # Do NOT discard slug — order may have gone on-chain before the exception

    def execute_stop_loss(self, initial_bid: float):
        """Walk the bid book level-by-level — sell as many shares as possible at the best
        available price, then sell the remainder at the next level down, repeating every
        0.5s until all shares are gone or the market expires."""
        if not self.position:
            return

        pos = self.position
        market = pos["market"]
        total_cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])

        logger.info(
            "STOP-LOSS  %s @ $%.3f -> $%.3f  |  %d shares",
            pos["side"], pos["entry_price"], initial_bid, pos["shares"],
        )

        if not self.observe_only:
            shares_remaining = pos["shares"]
            total_proceeds = 0.0

            while shares_remaining > 0:
                # Market expired mid-sell — record partial P&L and let settlement
                # handle the remaining shares
                if datetime.now(timezone.utc) >= market["end_date"]:
                    sold_shares = pos["shares"] - shares_remaining
                    logger.info(
                        "Market expired — sold %d/%d shares ($%.2f), %d going to settlement",
                        sold_shares, pos["shares"], total_proceeds, shares_remaining,
                    )
                    if sold_shares > 0:
                        sold_cost = total_cost * (sold_shares / pos["shares"])
                        self.stats["total_pnl"] += total_proceeds - sold_cost
                        self.per_trade_budget += total_proceeds - sold_cost
                        self.local_cash = (self.local_cash or 0) + total_proceeds
                        # Shrink position so settlement accounts only for what's left
                        pos["shares"] = shares_remaining
                        pos["actual_cost"] = total_cost - sold_cost
                    return  # settlement path handles the rest

                try:
                    fresh_book = self.exchange.get_order_book(pos["token_id"])
                    bids = sorted(
                        fresh_book["bids"], key=lambda b: b["price"], reverse=True
                    )
                except Exception:
                    time.sleep(0.5)
                    continue

                if not bids:
                    logger.warning("No bids available — waiting 0.5s")
                    time.sleep(0.5)
                    continue

                # Walk each bid level, selling as many shares as available at that price
                for bid in bids:
                    if shares_remaining <= 0:
                        break
                    chunk = min(int(bid["size"]), shares_remaining)
                    if chunk <= 0:
                        continue
                    try:
                        result = self.exchange.sell(
                            token_id=pos["token_id"],
                            shares=chunk,
                            price=bid["price"],
                        )
                        if result:
                            total_proceeds += chunk * bid["price"]
                            shares_remaining -= chunk
                            logger.info(
                                "Sold %d shares @ $%.3f  |  %d remaining",
                                chunk, bid["price"], shares_remaining,
                            )
                        else:
                            logger.warning(
                                "Chunk sell returned None @ $%.3f — will retry",
                                bid["price"],
                            )
                    except Exception as e:
                        logger.error("Chunk sell failed @ $%.3f: %s", bid["price"], e)

                if shares_remaining > 0:
                    time.sleep(0.5)

        else:
            logger.info("OBSERVE  Would sell (observe-only mode)")
            total_proceeds = initial_bid * pos["shares"]
            total_cost = pos.get("actual_cost", pos["entry_price"] * pos["shares"])

        total_loss = total_cost - total_proceeds
        avg_price = total_proceeds / pos["shares"] if pos["shares"] > 0 else initial_bid
        logger.info(
            "STOP-LOSS complete  |  avg exit $%.3f  |  total proceeds $%.2f  |  loss $%.2f",
            avg_price, total_proceeds, total_loss,
        )
        self.stats["resolved"] += 1
        self.stats["total_pnl"] -= total_loss
        self.per_trade_budget -= total_loss
        self.local_cash = (self.local_cash or 0) + total_proceeds
        self.position = None
        logger.info("Stop-loss done. Continuing to next market...")

    # ------------------------------------------------------------------
    # Stats & main loop
    # ------------------------------------------------------------------

    def print_stats(self):
        s = self.stats
        logger.info(
            "STATS  |  scans: %d  |  trades: %d  |  wins: %d  |  resolved: %d  |  "
            "spent: $%.2f  |  P&L: %+.2f  |  budget/trade: $%.2f",
            s["scans"], s["trades"], s["wins"], s["resolved"],
            s["total_spent"], s["total_pnl"], self._initial_budget,
        )

    def run(self):
        """Main loop: discover markets every 60s, scan books every 1s."""
        mode = "OBSERVE ONLY" if self.observe_only else "LIVE TRADING"
        logger.info(
            "Momentum Trader starting  |  exchange: %s  |  mode: %s",
            self.exchange.exchange_name, mode,
        )
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
        description="Bitcoin 15-min momentum trader (Polymarket / Kalshi)"
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
        help="Maximum USD per trade (default: $10.00)",
    )
    parser.add_argument(
        "--exchange",
        choices=["polymarket", "kalshi"],
        default="polymarket",
        help="Exchange to trade on (default: polymarket)",
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

    if args.exchange == "kalshi":
        from trading.kalshi_trader import KalshiAdapter
        logger.info("Initializing KalshiAdapter...")
        exchange = KalshiAdapter()
    else:
        from trading.polymarket_adapter import PolymarketAdapter
        logger.info("Initializing PolymarketAdapter...")
        exchange = PolymarketAdapter()

    momentum = MomentumTrader(exchange, observe_only=not args.live, per_trade_budget=args.budget)
    momentum.run()


if __name__ == "__main__":
    main()
