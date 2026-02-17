"""
Trading orchestration that supports both paper and live execution.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from enum import Enum
from typing import Dict

from analysis.monitor import BotMonitor
from database import DatabaseManager, PaperTradingTrader, PolymarketMarket, PriceSnapshot, Trade
from services.settings import BotSettingsService
from trading.polymarket_trader import PolymarketTrader

logger = logging.getLogger(__name__)


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"

    @classmethod
    def from_env(cls) -> "TradingMode":
        return cls(os.getenv("TRADING_MODE", "paper").lower())


class TradingEngine:
    """Evaluates recommendations and routes orders to the desired backend."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        mode: TradingMode = TradingMode.PAPER,
        enable_auto_trading: bool = False,
    ):
        self.db = db_manager
        # Ensure schema exists before traders run queries
        try:
            self.db.create_tables()
        except Exception:
            logger.exception("Failed to create database tables during trading engine init")
        self.mode = mode
        self.enable_auto_trading = enable_auto_trading

        self.monitor = BotMonitor(self.db)
        self.settings_service = BotSettingsService(self.db)

        if self.mode is TradingMode.PAPER:
            self.trader = PaperTradingTrader(self.db)
        else:
            self.trader = PolymarketTrader(
                max_position_size=float(os.getenv("MAX_POSITION_SIZE_USD", 100)),
                max_total_exposure=float(os.getenv("MAX_TOTAL_EXPOSURE_USD", 500)),
            )

        self.max_trades_per_cycle = int(os.getenv("MAX_TRADES_PER_CYCLE", 5))
        self.min_edge = float(os.getenv("MIN_EDGE_FOR_TRADE", 3.0))
        self.min_kelly = float(os.getenv("MIN_KELLY_FOR_TRADE", 0.05))

    def bankroll(self) -> float:
        if self.mode is TradingMode.PAPER:
            balance = self.trader.get_balance()
            return balance["total_value"]

        balance = self.trader.get_balance()
        return balance.get("total_value") or balance.get("usdc_balance", 0)

    def process_market_prices(self):
        """Refresh mark-to-market values and execute trades if enabled."""
        logger.info("=== Auto-Trading Cycle Starting ===")
        logger.info(f"Auto-trading enabled: {self.auto_trading_enabled()}")

        prices = self._latest_prices()
        logger.info(f"Loaded {len(prices)} price snapshots")

        # ALWAYS update position prices for paper trading (mark-to-market)
        if self.mode is TradingMode.PAPER:
            if prices:
                updated_count = self._update_position_prices_detailed(prices)
                logger.info(f"Updated prices for {updated_count} positions")
            else:
                logger.warning("No price data available for mark-to-market")

        if self.auto_trading_enabled():
            self._auto_trade(prices)
        else:
            logger.info("Auto-trading is disabled, skipping trade execution")

        logger.info("=== Auto-Trading Cycle Complete ===")
        logger.info("")

    def auto_trading_enabled(self) -> bool:
        return self.enable_auto_trading and self.settings_service.is_auto_trading_enabled()

    def _latest_prices(self) -> Dict[str, float]:
        with self.db.get_session() as session:
            snapshots = (
                session.query(PriceSnapshot)
                .order_by(PriceSnapshot.timestamp.desc())
                .limit(100)
                .all()
            )

            price_updates: Dict[str, float] = {}
            for snapshot in snapshots:
                outcome_id = str(snapshot.outcome_id)
                if outcome_id not in price_updates:
                    price_updates[outcome_id] = snapshot.price
            return price_updates

    def _update_position_prices_detailed(self, price_updates: Dict[str, float]) -> int:
        """
        Update position prices with detailed logging.
        Returns number of positions updated.
        """
        updated_count = 0

        with self.db.get_session() as session:
            from database.paper_trading_schema import PaperPosition, PaperTradingAccount

            positions = (
                session.query(PaperPosition)
                .filter_by(account_id=self.trader.account_id)
                .all()
            )

            for position in positions:
                if position.outcome_id in price_updates:
                    old_price = position.current_price
                    new_price = price_updates[position.outcome_id]

                    # Update position
                    position.current_price = new_price
                    position.current_value = position.shares * new_price
                    position.unrealized_pnl = position.current_value - position.total_cost
                    position.updated_at = datetime.utcnow()

                    updated_count += 1

                    # Log significant price changes
                    if old_price and abs(new_price - old_price) / old_price > 0.05:  # >5% change
                        logger.info(
                            f"  {position.bracket}: ${old_price:.4f} → ${new_price:.4f} "
                            f"(P&L: ${position.unrealized_pnl:.2f})"
                        )
                else:
                    logger.warning(f"No price data for position {position.bracket} (outcome_id: {position.outcome_id})")

            # Update account total value
            account = session.query(PaperTradingAccount).filter_by(id=self.trader.account_id).first()
            if account:
                total_position_value = sum(p.current_value or 0 for p in positions)
                account.total_value = account.current_cash + total_position_value
                account.updated_at = datetime.utcnow()

            session.commit()

        return updated_count

    def _auto_trade(self, price_updates: Dict[str, float]):
        """Execute trades for all active and future markets."""
        positions = self._current_positions()
        bankroll = self.bankroll()

        try:
            active_markets = self._get_active_markets(include_future=True)
        except Exception as e:
            logger.error(f"Failed to get active markets: {e}", exc_info=True)
            return

        if not active_markets:
            logger.info("No tradeable markets found")
            return

        # Calculate per-market capital limit
        # Divide total bankroll by number of markets to ensure diversification
        num_markets = len(active_markets)
        per_market_limit = bankroll / num_markets
        logger.info(f"Per-market capital limit: ${per_market_limit:.2f} ({num_markets} markets)")

        # Track capital allocated per market (including existing positions)
        market_capital_used = self._calculate_capital_per_market(positions)

        # Collect all recommendations from all markets
        all_recommendations = []
        for market in active_markets:
            try:
                market_id = market["market_id"]
                capital_used = market_capital_used.get(market_id, 0)
                remaining_capital = per_market_limit - capital_used

                if remaining_capital <= 10:  # Skip if less than $10 remaining
                    logger.info(f"Market {market_id}: Capital limit reached (${capital_used:.2f}/${per_market_limit:.2f})")
                    continue

                if market.get("is_future"):
                    # Future market: Get lottery ticket recommendations
                    recs = self._get_lottery_recommendations(
                        market_id=market_id,
                        bankroll=remaining_capital,  # Use remaining capital, not total
                        current_positions=positions,
                    )
                    if recs:
                        all_recommendations.extend(recs)
                        logger.info(f"Pre-market {market_id}: {len(recs)} lottery tickets (${remaining_capital:.2f} available)")
                else:
                    # Active market: Normal recommendations
                    recs = self.monitor.get_recommendations(
                        bankroll=remaining_capital,  # Use remaining capital, not total
                        current_positions=positions,
                        market_id=market_id,
                    )
                    if recs:
                        all_recommendations.extend(recs)
                        logger.info(f"Market {market_id}: {len(recs)} recommendations (${remaining_capital:.2f} available)")
                    else:
                        logger.info(f"No trading signals for market {market_id}")
            except Exception as e:
                logger.error(f"Error getting recommendations for market {market['market_id']}: {e}", exc_info=True)
                continue

        if not all_recommendations:
            logger.info("No recommendations from any market")
            return

        # Filter for actionable trades
        sells = [
            r
            for r in all_recommendations
            if r.get("is_sell")
            and r["position_size"] > 0
            and r.get("timing") == "NOW"
        ]
        buys = [
            r
            for r in all_recommendations
            if not r.get("is_sell")
            and r["position_size"] > 0
            and r["edge"] > self.min_edge
            and r["kelly_fraction"] > self.min_kelly
        ]

        # Sort buys by edge (best opportunities first)
        buys.sort(key=lambda x: x.get("edge", 0), reverse=True)

        logger.info(f"Actionable trades: {len(sells)} sells, {len(buys)} buys")

        if not sells and not buys:
            logger.info("Recommendations exist but do not meet thresholds")
            return

        # Execute sells first
        for rec in sells:
            try:
                self._execute_sell(rec)
            except Exception as e:
                logger.error(f"Failed to execute sell: {e}", exc_info=True)

        # Execute buys respecting per-market limits
        # Track capital used per market during this cycle
        cycle_capital_per_market = {}

        for rec in buys[: self.max_trades_per_cycle]:
            try:
                market_id = rec.get("market_id")
                position_size = rec.get("position_size", 0)

                if market_id:
                    # Check if this trade would exceed the per-market limit
                    already_used = market_capital_used.get(market_id, 0)
                    cycle_used = cycle_capital_per_market.get(market_id, 0)
                    remaining = per_market_limit - already_used - cycle_used

                    if position_size > remaining:
                        logger.info(f"  Skipping {rec['bracket']}: Would exceed market {market_id} limit (${position_size:.2f} > ${remaining:.2f} remaining)")
                        continue

                    # Track capital used in this cycle
                    cycle_capital_per_market[market_id] = cycle_used + position_size

                self._execute_buy(rec)
            except Exception as e:
                logger.error(f"Failed to execute buy: {e}", exc_info=True)

    def _get_active_markets(self, include_future: bool = True):
        """
        Get all tradeable markets.

        Args:
            include_future: If True, includes markets that haven't started yet (for lottery tickets)

        Returns:
            List of market dicts with market_id, title, and is_future flag
        """
        with self.db.get_session() as session:
            from datetime import datetime, timedelta

            now = datetime.utcnow()

            # Get currently active markets
            active_markets = (
                session.query(PolymarketMarket)
                .filter(
                    PolymarketMarket.week_start <= now,
                    PolymarketMarket.week_end >= now,
                )
                .all()
            )

            result = [
                {
                    "market_id": market.market_id,
                    "title": market.title,
                    "is_future": False,
                    "week_start": market.week_start,
                }
                for market in active_markets
            ]

            # Optionally include future markets (starting within next 7 days)
            if include_future:
                future_cutoff = now + timedelta(days=7)
                future_markets = (
                    session.query(PolymarketMarket)
                    .filter(
                        PolymarketMarket.week_start > now,
                        PolymarketMarket.week_start <= future_cutoff,
                    )
                    .all()
                )

                result.extend([
                    {
                        "market_id": market.market_id,
                        "title": market.title,
                        "is_future": True,
                        "week_start": market.week_start,
                    }
                    for market in future_markets
                ])

            # Sort by week start (active first, then future)
            result.sort(key=lambda m: (m["is_future"], m["week_start"]))

            return result

    def _calculate_capital_per_market(self, positions: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        """
        Calculate how much capital is allocated to each market based on existing positions.

        Args:
            positions: Dict of {outcome_id: {shares, average_cost}}

        Returns:
            Dict of {market_id: capital_allocated}
        """
        with self.db.get_session() as session:
            from database import MarketOutcome, PolymarketMarket

            market_capital = {}

            # Get all positions with their market info
            for outcome_id, info in positions.items():
                shares = info.get('shares', 0)
                # Find the market for this outcome
                outcome = (
                    session.query(MarketOutcome)
                    .filter_by(outcome_id=outcome_id)
                    .first()
                )

                if not outcome:
                    continue

                market = (
                    session.query(PolymarketMarket)
                    .filter_by(id=outcome.market_id)
                    .first()
                )

                if not market:
                    continue

                average_cost = info.get('average_cost', 0)
                market_id = market.market_id
                market_capital[market_id] = market_capital.get(market_id, 0) + shares * average_cost

            return market_capital

    def _get_lottery_recommendations(
        self,
        market_id: str,
        bankroll: float,
        current_positions: Dict[str, Dict[str, float]],
    ) -> List[Dict]:
        """
        Generate lottery ticket recommendations for pre-market.

        Targets extreme brackets with good edge using 0.05% capital positions.
        """
        try:
            # Get market bell curve (prices)
            brackets = self.monitor.get_market_bell_curve(market_id=market_id)
            if not brackets:
                return []

            # Get distribution object (not dict) for probability calculations
            context = self.monitor._build_distribution(market_id=market_id)
            if context.get('error'):
                return []

            distribution = context.get('distribution')
            if not distribution:
                return []

            lottery_allocation = 0.0005  # 0.05% of bankroll per ticket
            position_size = bankroll * lottery_allocation

            recommendations = []

            for bracket in brackets:
                outcome_id = str(bracket['outcome_id'])

                # Skip if we already have a position
                info = current_positions.get(outcome_id)
                if info and info.get('shares', 0) > 0:
                    continue

                # Use ask price for buying lottery tickets
                ask_price = bracket.get('ask', bracket['price'])  # Fallback to midpoint if no ask
                midpoint = bracket['price']

                # Calculate our probability
                our_prob = self.monitor.distribution_model.bracket_probability(
                    distribution,
                    bracket['min'],
                    bracket['max']
                )

                # Calculate edge using ask price (what we'd actually pay)
                edge = (our_prob - ask_price) * 100  # Convert to percentage

                # Only consider if we have edge
                if edge < 3.0:  # Minimum 3% edge for lottery tickets
                    continue

                # Focus on extreme outcomes (low probability / high upside)
                # Lottery tickets should be cheap (< $0.02 = 2% probability)
                if ask_price > 0.02:
                    continue

                # Calculate shares using ask price
                shares = position_size / max(ask_price, 0.0001)

                recommendations.append({
                    'outcome_id': outcome_id,
                    'bracket': bracket['bracket'],
                    'market_price': ask_price,  # Use ask price for execution
                    'midpoint_price': midpoint,
                    'bid': bracket.get('bid', midpoint),
                    'ask': ask_price,
                    'spread': bracket.get('spread', 0),
                    'our_prob': our_prob * 100,
                    'market_prob': ask_price * 100,
                    'edge': edge,
                    'action': 'LOTTERY TICKET',
                    'timing': 'PRE-MARKET',
                    'position_size': position_size,
                    'shares': shares,
                    'kelly_fraction': lottery_allocation,  # Fixed small allocation
                    'is_sell': False,
                    'market_title': bracket.get('market_title', 'Unknown'),
                    'market_id': market_id,
                    'roi_if_win': ((1 / ask_price) - 1) * 100 if ask_price > 0 else 0,
                })

            return recommendations

        except Exception as e:
            logger.error(f"Error generating lottery recommendations for {market_id}: {e}", exc_info=True)
            return []

    def _current_positions(self) -> Dict[str, Dict[str, float]]:
        if self.mode is TradingMode.PAPER:
            return self.trader.get_positions_dict()

        # For live trading, use actual positions from the wallet
        # This ensures we only sell shares we actually have (not pending orders)
        positions = self.trader.get_open_positions()
        position_dict = {
            str(p['outcome_id']): {
                'shares': p.get('shares', 0),
                'average_cost': p.get('average_cost', 0),
            }
            for p in positions if p.get('shares', 0) > 0
        }

        # Safety check: Reduce position by shares in pending sell orders
        # This prevents trying to sell the same shares multiple times
        with self.db.get_session() as session:
            pending_sells = session.query(Trade).filter_by(
                side="SELL",
                status="pending"
            ).all()

            for trade in pending_sells:
                outcome_id = str(trade.outcome_id)
                info = position_dict.get(outcome_id)
                if info:
                    # Subtract pending sell shares from available balance
                    info['shares'] = max(0, info['shares'] - trade.size)
                    if info['shares'] == 0:
                        position_dict.pop(outcome_id, None)

        return position_dict

    def update_pending_orders(self):
        """Poll pending orders and update database when they fill."""
        if self.mode is TradingMode.PAPER:
            return  # Paper trading doesn't have real orders

        with self.db.get_session() as session:
            # Get all pending orders
            pending = session.query(Trade).filter_by(status="pending").all()

            if not pending:
                return

            logger.info(f"Checking status of {len(pending)} pending orders...")

            for trade in pending:
                if not trade.trade_id:
                    continue

                try:
                    # Poll order status from Polymarket
                    order_status = self.trader.get_order_status(trade.trade_id)

                    if not order_status:
                        continue

                    # Check if order is filled
                    # Polymarket order statuses: "LIVE", "MATCHED", "CANCELLED", etc.
                    status = order_status.get('status', '').upper()

                    if status in ('MATCHED', 'FILLED'):
                        # Order is filled - update with actual fill data
                        filled_size = float(order_status.get('size_matched', order_status.get('size', trade.size)))
                        filled_price = float(order_status.get('price', trade.price))

                        # Update trade record
                        trade.size = filled_size
                        trade.price = filled_price
                        trade.total_cost = filled_size * filled_price
                        trade.status = "filled"

                        # Recalculate P&L for sells
                        if trade.side == "SELL":
                            buys = session.query(Trade).filter_by(
                                outcome_id=trade.outcome_id,
                                side="BUY",
                                status="filled"
                            ).all()

                            if buys:
                                total_shares_bought = sum(t.size for t in buys)
                                total_cost_paid = sum(t.total_cost for t in buys)
                                avg_cost = total_cost_paid / total_shares_bought if total_shares_bought > 0 else 0
                                trade.realized_pnl = (filled_price - avg_cost) * filled_size

                        logger.info(f"✓ Order {trade.trade_id} filled: {filled_size:.2f} shares @ ${filled_price:.4f}")

                    elif status in ('CANCELLED', 'CANCELED'):
                        trade.status = "cancelled"
                        logger.info(f"✗ Order {trade.trade_id} cancelled")

                except Exception as e:
                    logger.error(f"Failed to check order {trade.trade_id}: {e}")
                    continue

            session.commit()

    def _execute_buy(self, rec: Dict):
        shares = rec["shares"]
        logger.info(f"→ BUY: {rec['bracket']} | {shares:.2f} shares @ ${rec['market_price']:.4f}")
        logger.info(f"  Market: {rec.get('market_title', 'N/A')}")
        logger.info(f"  Edge: {rec['edge']:.1f}% | Size: ${rec['position_size']:.2f}")

        if shares <= 0:
            logger.warning(f"  ⚠️  Skipping - invalid shares: {shares}")
            return

        if self.mode is TradingMode.PAPER:
            success = self.trader.execute_buy(
                outcome_id=rec["outcome_id"],
                bracket=rec["bracket"],
                shares=shares,
                price=rec["market_price"],
                action=rec["action"],
                our_prob=rec["our_prob"] / 100,
                market_prob=rec["market_prob"] / 100,
                edge=rec["edge"] / 100,
                week_progress=rec.get("_meta", {}).get("week_progress"),
            )
            if success:
                logger.info(f"  ✓ Paper trade executed successfully")
            else:
                logger.error(f"  ✗ Paper trade failed")
            return

        usd = rec["position_size"]
        result = self.trader.create_market_buy_order(
            token_id=str(rec["outcome_id"]),
            amount_usd=usd,
            price=rec["market_price"],
        )
        if result:
            order_id = result.get('orderID')
            self._record_live_trade(rec, "buy", usd, order_id=order_id)
            logger.info(f"  ✓ Live order placed: {order_id}")
        else:
            logger.error(f"  ✗ Live order failed")

    def _execute_sell(self, rec: Dict):
        shares = rec["shares"]
        if shares <= 0:
            return

        if self.mode is TradingMode.PAPER:
            self.trader.execute_sell(
                outcome_id=rec["outcome_id"],
                bracket=rec["bracket"],
                shares=shares,
                price=rec["market_price"],
                action=rec["action"],
                our_prob=rec["our_prob"] / 100,
                market_prob=rec["market_prob"] / 100,
                edge=rec["edge"] / 100,
                week_progress=rec.get("_meta", {}).get("week_progress"),
            )
            return

        # For live mode, execute the sell
        # Floor shares to avoid trying to sell more than we have due to rounding
        shares_to_sell = int(shares)  # Floor instead of round

        if shares_to_sell <= 0:
            logger.warning(f"  ⚠️  Skipping sell - shares rounded down to 0 (had {shares:.2f})")
            return

        # Additional safety: subtract 1 share as buffer for race conditions
        # This prevents "not enough balance" errors from pending orders or stale wallet data
        if shares_to_sell > 10:
            shares_to_sell -= 1  # Leave 1 share buffer for positions > 10

        result = self.trader.create_market_sell_order(
            token_id=str(rec["outcome_id"]),
            shares=shares_to_sell,
            price=rec["market_price"],
        )
        if result:
            order_id = result.get('orderID')
            self._record_live_trade(rec, "sell", shares_to_sell * rec["market_price"],
                                   actual_shares=shares_to_sell, order_id=order_id)
            logger.info(f"  ✓ Live order placed: {order_id}")

    def _record_live_trade(self, rec: Dict, side: str, total_cost: float, actual_shares: float = None, order_id: str = None):
        with self.db.get_session() as session:
            # Get market from recommendation data
            market = None
            if rec.get("market_id"):
                market = session.query(PolymarketMarket).filter_by(market_id=rec["market_id"]).first()

            # Use actual shares if provided, otherwise use recommendation shares
            shares = actual_shares if actual_shares is not None else rec["shares"]

            # Calculate realized P&L for sells (will be updated when order fills)
            realized_pnl = None
            if side.upper() == "SELL":
                # Get average cost from previous FILLED buys
                outcome_id = str(rec["outcome_id"])
                buys = session.query(Trade).filter_by(
                    outcome_id=outcome_id,
                    side="BUY",
                    status="filled"  # Only count filled orders
                ).all()

                if buys:
                    total_shares_bought = sum(t.size for t in buys)
                    total_cost_paid = sum(t.total_cost for t in buys)
                    avg_cost = total_cost_paid / total_shares_bought if total_shares_bought > 0 else 0

                    # P&L = (sell_price - avg_buy_price) * shares
                    sell_price = rec["market_price"]
                    realized_pnl = (sell_price - avg_cost) * shares

            trade = Trade(
                market_id=market.id if market else None,
                outcome_id=str(rec["outcome_id"]),
                trade_id=order_id,  # Store the order ID for fill tracking
                side=side.upper(),
                price=rec["market_price"],
                size=shares,
                total_cost=total_cost,
                strategy=rec.get("action", "auto"),
                notes=f"Auto-trade ({self.mode.value})",
                status="pending",  # Will be updated to 'filled' when we poll order status
                realized_pnl=realized_pnl,
            )
            session.add(trade)
            session.commit()
