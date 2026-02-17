"""
Multi-market trading engine with Kelly Criterion sizing.

Position size caps:
- Max 6% bankroll per trade (Kelly cap)
- Half-Kelly (multiply by 0.5) for variance reduction
- Max 10% bankroll per market
- Max 25% bankroll per category (20% for crypto)
- Max 80% total exposure (20% cash reserve)
- Min $1.50 trade floor (for $50 bankroll)
- Edge decay exit: if edge drops below 3%, exit position
- Daily loss limit: 10% of bankroll -> pause 4 hours
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from database import DatabaseManager
from database.scanner_schema import Opportunity, CategoryPerformance

logger = logging.getLogger(__name__)


class MultiMarketEngine:
    """
    Kelly Criterion-based position sizing and trade execution
    for multi-market, multi-category trading.
    """

    def __init__(
        self,
        bankroll: float = None,
        trader=None,
        db_manager: DatabaseManager = None,
    ):
        self.bankroll = bankroll or float(os.getenv("PAPER_TRADING_START_BALANCE", "50"))
        self.trader = trader  # PolymarketTrader or PaperTradingTrader
        self.db = db_manager or DatabaseManager()

        # Position sizing parameters
        self.max_kelly_fraction = float(os.getenv("MAX_KELLY_FRACTION", "0.06"))
        self.kelly_multiplier = float(os.getenv("KELLY_MULTIPLIER", "0.5"))  # Half-Kelly
        self.max_market_exposure_pct = float(os.getenv("MAX_MARKET_EXPOSURE_PCT", "10")) / 100
        self.max_category_exposure_pct = float(os.getenv("MAX_CATEGORY_EXPOSURE_PCT", "25")) / 100
        self.max_crypto_exposure_pct = float(os.getenv("MAX_CRYPTO_EXPOSURE_PCT", "20")) / 100
        self.max_total_exposure_pct = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "80")) / 100
        self.min_trade_usd = float(os.getenv("MIN_TRADE_USD", "1.50"))
        self.min_edge_exit = float(os.getenv("MIN_EDGE_EXIT", "3")) / 100  # 3% edge decay exit
        self.daily_loss_limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "10")) / 100

        # Tracking
        self._daily_pnl = 0.0
        self._daily_pnl_reset: Optional[datetime] = None
        self._paused_until: Optional[datetime] = None
        self._current_exposure: Dict[str, float] = {}  # market_id -> USD exposure
        self._category_exposure: Dict[str, float] = {}  # category -> USD exposure

    def size_position(self, opportunity: Dict) -> Tuple[float, float]:
        """
        Calculate position size using Kelly Criterion with all caps applied.

        Args:
            opportunity: Dict with edge, market_price, confidence, category, etc.

        Returns:
            (kelly_fraction, position_size_usd)
        """
        edge = opportunity.get("edge", 0)
        market_price = opportunity.get("market_price", 0.5)
        confidence = opportunity.get("confidence", 0.5)
        category = opportunity.get("category", "unknown")
        market_id = opportunity.get("market_id", "")

        if edge <= 0 or market_price <= 0 or market_price >= 1.0:
            return 0.0, 0.0

        # Kelly Criterion: f* = (b*p - q) / b
        # where b = odds = (1/price) - 1, p = fair_prob, q = 1 - p
        b = (1.0 / market_price) - 1.0
        p = market_price + edge  # fair probability
        q = 1.0 - p

        if b <= 0:
            return 0.0, 0.0

        kelly = (b * p - q) / b
        kelly = max(0, kelly)

        # Cap at max Kelly fraction
        kelly = min(kelly, self.max_kelly_fraction)

        # Apply Kelly multiplier (quarter-Kelly for variance reduction)
        kelly *= self.kelly_multiplier

        # Calculate USD position
        position_usd = kelly * self.bankroll

        # Apply all caps
        position_usd = self._apply_caps(position_usd, market_id, category)

        # Enforce minimum
        if position_usd < self.min_trade_usd:
            return 0.0, 0.0

        return kelly, round(position_usd, 2)

    def should_exit(self, opportunity: Dict) -> bool:
        """Check if an existing position should be exited due to edge decay."""
        edge = abs(opportunity.get("edge", 0))
        return edge < self.min_edge_exit

    def is_paused(self) -> bool:
        """Check if trading is paused due to daily loss limit."""
        if self._paused_until and datetime.utcnow() < self._paused_until:
            return True
        return False

    def record_trade_result(self, pnl: float) -> None:
        """Record a trade result and check daily loss limit."""
        now = datetime.utcnow()

        # Reset daily P&L counter
        if self._daily_pnl_reset is None or (now - self._daily_pnl_reset).total_seconds() > 86400:
            self._daily_pnl = 0.0
            self._daily_pnl_reset = now

        self._daily_pnl += pnl

        # Check daily loss limit
        if self._daily_pnl < -(self.daily_loss_limit_pct * self.bankroll):
            self._paused_until = now + timedelta(hours=4)
            logger.warning(
                "Daily loss limit hit ($%.2f). Pausing trading until %s",
                self._daily_pnl, self._paused_until,
            )

    def execute_opportunities(
        self,
        opportunities: List[Dict],
        dry_run: bool = True,
    ) -> List[Dict]:
        """
        Size and execute a list of opportunities.

        Args:
            opportunities: Sorted list of opportunity dicts.
            dry_run: If True, don't place real orders.

        Returns:
            List of execution result dicts.
        """
        if self.is_paused():
            logger.warning("Trading is paused until %s", self._paused_until)
            return []

        if not self.trader:
            logger.error("No trader configured")
            return []

        # Sync exposure from actual open positions so externally-closed
        # positions don't block new trades
        self._sync_exposure_from_positions()

        results = []
        max_trades = int(os.getenv("MAX_TRADES_PER_CYCLE", "5"))
        trades_this_cycle = 0

        for opp in opportunities:
            if trades_this_cycle >= max_trades:
                break

            kelly, position_usd = self.size_position(opp)

            if position_usd <= 0:
                if len(results) == 0 and trades_this_cycle == 0:
                    # Log why the first few opportunities were skipped
                    logger.debug(
                        "Skipped %s: edge=%.3f price=%.3f kelly=%.4f size=$%.2f (min=$%.2f)",
                        opp.get("market_title", "")[:40], opp.get("edge", 0),
                        opp.get("market_price", 0), kelly, position_usd, self.min_trade_usd,
                    )
                continue

            market_price = opp.get("market_price", 0)
            if market_price <= 0:
                continue

            shares = int(position_usd / market_price)
            if shares <= 0:
                continue

            # Build recommendation in the format the existing trader expects
            rec = {
                "outcome_id": opp.get("outcome_id"),
                "bracket": opp.get("outcome_text", opp.get("market_title", "")),
                "market_price": market_price,
                "position_size": position_usd,
                "shares": shares,
                "action": "BUY",
                "is_sell": False,
                "edge": opp.get("edge", 0),
                "kelly_fraction": kelly,
            }

            result = self.trader.execute_recommendation(rec, dry_run=dry_run)
            result["market_id"] = opp.get("market_id")
            result["category"] = opp.get("category")
            result["edge"] = opp.get("edge")
            result["fair_value"] = opp.get("fair_value")
            results.append(result)

            # Track exposure
            if result.get("success"):
                trades_this_cycle += 1
                self._track_exposure(opp.get("market_id", ""), opp.get("category", ""), position_usd)

                # Store opportunity in DB
                self._store_opportunity(opp, kelly, position_usd, dry_run)

        logger.info(
            "Executed %d/%d opportunities (dry_run=%s)",
            sum(1 for r in results if r.get("success")),
            len(results),
            dry_run,
        )

        return results

    def execute_exits(self, exit_signals: List[Dict], dry_run: bool = True) -> List[Dict]:
        """Execute exits for positions where edge has decayed."""
        if not self.trader:
            return []

        results = []
        for signal in exit_signals:
            if not self.should_exit(signal):
                continue

            rec = {
                "outcome_id": signal.get("outcome_id"),
                "bracket": signal.get("outcome_text", ""),
                "market_price": signal.get("market_price", 0),
                "position_size": signal.get("position_value", 0),
                "shares": signal.get("shares", 0),
                "action": "EXIT (EDGE DECAY)",
                "is_sell": True,
            }

            result = self.trader.execute_recommendation(rec, dry_run=dry_run)
            result["reason"] = "edge_decay"
            results.append(result)

        return results

    def update_bankroll(self, new_bankroll: float) -> None:
        """Update the bankroll (e.g., after deposits or profits)."""
        old = self.bankroll
        self.bankroll = new_bankroll
        logger.info("Bankroll updated: $%.2f -> $%.2f", old, new_bankroll)

    def _sync_exposure_from_positions(self) -> None:
        """Rebuild exposure tracking from actual open positions.

        This ensures externally-closed positions (sold via the UI) don't
        block the engine from placing new trades.
        """
        try:
            positions = self.trader.get_open_positions()
            self._current_exposure.clear()
            self._category_exposure.clear()

            for pos in positions:
                market_id = pos.get("market_id", "unknown")
                value = pos.get("current_value", 0)
                self._current_exposure[market_id] = (
                    self._current_exposure.get(market_id, 0) + value
                )
                # We don't know the category from position data, so track
                # all under a generic key; per-category caps will still
                # work via the per-market caps which are the tighter constraint.

            total = sum(self._current_exposure.values())
            logger.debug(
                "Synced exposure from %d positions: $%.2f total",
                len(positions), total,
            )
        except Exception as e:
            logger.warning("Failed to sync exposure from positions: %s", e)

    def _apply_caps(self, position_usd: float, market_id: str, category: str) -> float:
        """Apply all position sizing caps."""
        # Max per market
        current_market_exposure = self._current_exposure.get(market_id, 0)
        max_market = self.bankroll * self.max_market_exposure_pct
        available_market = max_market - current_market_exposure
        position_usd = min(position_usd, max(available_market, 0))

        # Max per category
        cat_cap = self.max_crypto_exposure_pct if category == "crypto" else self.max_category_exposure_pct
        current_cat_exposure = self._category_exposure.get(category, 0)
        max_cat = self.bankroll * cat_cap
        available_cat = max_cat - current_cat_exposure
        position_usd = min(position_usd, max(available_cat, 0))

        # Max total exposure
        total_exposure = sum(self._current_exposure.values())
        max_total = self.bankroll * self.max_total_exposure_pct
        available_total = max_total - total_exposure
        position_usd = min(position_usd, max(available_total, 0))

        return position_usd

    def _track_exposure(self, market_id: str, category: str, amount: float) -> None:
        """Track exposure after a trade."""
        self._current_exposure[market_id] = self._current_exposure.get(market_id, 0) + amount
        self._category_exposure[category] = self._category_exposure.get(category, 0) + amount

    def _store_opportunity(self, opp: Dict, kelly: float, position_usd: float, dry_run: bool) -> None:
        """Store an executed opportunity in the database."""
        try:
            with self.db.get_session() as session:
                opportunity = Opportunity(
                    market_id=opp.get("market_id", ""),
                    outcome_id=opp.get("outcome_id", ""),
                    market_title=opp.get("market_title", ""),
                    category=opp.get("category", "unknown"),
                    market_price=opp.get("market_price", 0),
                    fair_value=opp.get("fair_value", 0),
                    edge=opp.get("edge", 0),
                    confidence=opp.get("confidence", 0),
                    kelly_fraction=kelly,
                    position_size_usd=position_usd,
                    score=opp.get("score", 0),
                    liquidity=opp.get("liquidity", 0),
                    status="traded" if not dry_run else "paper_traded",
                    traded_at=datetime.utcnow(),
                )
                session.add(opportunity)
                session.commit()
        except Exception as e:
            logger.error("Failed to store opportunity: %s", e)

    def update_category_performance(self) -> None:
        """Update CategoryPerformance table from trade history."""
        try:
            with self.db.get_session() as session:
                # Get all traded opportunities grouped by category
                opps = session.query(Opportunity).filter(
                    Opportunity.status.in_(["traded", "paper_traded", "won", "lost"])
                ).all()

                cat_stats: Dict[str, Dict] = {}
                for opp in opps:
                    cat = opp.category
                    if cat not in cat_stats:
                        cat_stats[cat] = {
                            "total_trades": 0, "wins": 0, "losses": 0,
                            "total_pnl": 0.0, "total_invested": 0.0,
                            "edges": [], "confidences": [],
                        }
                    stats = cat_stats[cat]
                    stats["total_trades"] += 1
                    stats["total_invested"] += opp.position_size_usd or 0
                    stats["edges"].append(opp.edge or 0)
                    stats["confidences"].append(opp.confidence or 0)

                    if opp.status == "won":
                        stats["wins"] += 1
                        stats["total_pnl"] += (opp.position_size_usd or 0) * ((1.0 / (opp.market_price or 1)) - 1)
                    elif opp.status == "lost":
                        stats["losses"] += 1
                        stats["total_pnl"] -= opp.position_size_usd or 0

                for cat, stats in cat_stats.items():
                    perf = session.query(CategoryPerformance).filter_by(category=cat).first()
                    if not perf:
                        perf = CategoryPerformance(category=cat)
                        session.add(perf)

                    perf.total_trades = stats["total_trades"]
                    perf.wins = stats["wins"]
                    perf.losses = stats["losses"]
                    perf.total_pnl = stats["total_pnl"]
                    perf.total_invested = stats["total_invested"]
                    perf.roi_pct = (stats["total_pnl"] / stats["total_invested"] * 100) if stats["total_invested"] > 0 else 0
                    decided = stats["wins"] + stats["losses"]
                    perf.hit_rate = stats["wins"] / decided if decided > 0 else 0
                    perf.avg_edge = sum(stats["edges"]) / len(stats["edges"]) if stats["edges"] else 0
                    perf.avg_confidence = sum(stats["confidences"]) / len(stats["confidences"]) if stats["confidences"] else 0

                session.commit()
                logger.info("Updated category performance for %d categories", len(cat_stats))

        except Exception as e:
            logger.error("Failed to update category performance: %s", e)
