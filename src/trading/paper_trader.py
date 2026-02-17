"""
Paper trading trader that simulates executions and tracks portfolio performance.
"""

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

import sys
sys.path.insert(0, 'src')
from database import (
    DatabaseManager,
    PaperTradingAccount,
    PaperTrade,
    PaperPosition,
    PaperPortfolioSnapshot
)

logger = logging.getLogger(__name__)


class PaperTradingTrader:
    """
    Simulates trading with a paper trading account.
    Tracks all trades, positions, and performance metrics.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        account_name: str = 'Main Account',
        starting_balance: float = 1000.0,
    ):
        """
        Initialize paper trading trader.

        Args:
            db_manager: Database manager instance
            account_name: Name for the paper trading account
            starting_balance: Starting cash balance
        """
        self.db = db_manager
        self.account_name = account_name
        env_balance = os.getenv('PAPER_TRADING_START_BALANCE')
        if env_balance is not None and account_name == 'Main Account':
            try:
                starting_balance = float(env_balance)
            except ValueError:
                logger.warning("Invalid PAPER_TRADING_START_BALANCE value '%s'", env_balance)
        self.starting_balance = starting_balance

        # Get or create paper trading account
        with self.db.get_session() as session:
            account = session.query(PaperTradingAccount).filter_by(name=account_name).first()

            if not account:
                account = PaperTradingAccount(
                    name=account_name,
                    starting_balance=starting_balance,
                    current_cash=starting_balance,
                    total_value=starting_balance,
                )
                session.add(account)
                session.commit()
                logger.info(f"✓ Created paper trading account: {account_name} with ${starting_balance:,.2f}")
            else:
                logger.info(f"✓ Loaded paper trading account: {account_name}")

            self.account_id = account.id
            self.starting_balance = account.starting_balance

    def get_balance(self) -> Dict:
        """
        Get current cash and total portfolio value.

        Returns:
            Dictionary with balance information
        """
        with self.db.get_session() as session:
            account = session.query(PaperTradingAccount).filter_by(id=self.account_id).first()

            if not account:
                return {'cash': 0, 'total_value': 0}

            return {
                'cash': account.current_cash,
                'position_value': account.total_value - account.current_cash,
                'total_value': account.total_value,
                'pnl': account.total_value - account.starting_balance,
                'pnl_pct': ((account.total_value - account.starting_balance) / account.starting_balance) * 100,
            }

    def get_open_positions(self) -> List[Dict]:
        """
        Get all current open positions with market information.

        Returns:
            List of position dictionaries with market details
        """
        with self.db.get_session() as session:
            from database import MarketOutcome, PolymarketMarket

            # Join positions with outcomes and markets to get market info
            positions_query = (
                session.query(PaperPosition, MarketOutcome, PolymarketMarket)
                .filter(PaperPosition.account_id == self.account_id)
                .outerjoin(MarketOutcome, PaperPosition.outcome_id == MarketOutcome.outcome_id)
                .outerjoin(PolymarketMarket, MarketOutcome.market_id == PolymarketMarket.id)
                .all()
            )

            result = []
            for position, outcome, market in positions_query:
                pos_dict = {
                    'outcome_id': position.outcome_id,
                    'bracket': position.bracket,
                    'shares': position.shares,
                    'average_cost': position.average_cost,
                    'current_price': position.current_price,
                    'current_value': position.current_value,
                    'unrealized_pnl': position.unrealized_pnl,
                }

                # Add market info if available
                if market:
                    pos_dict['market_id'] = market.market_id
                    pos_dict['market_title'] = market.title

                result.append(pos_dict)

            return result

    def get_positions_dict(self) -> Dict[str, Dict[str, float]]:
        """Get positions as {outcome_id: {shares, avg_cost}}."""
        positions = self.get_open_positions()
        return {
            p['outcome_id']: {
                'shares': p['shares'],
                'average_cost': p['average_cost'] or 0,
            }
            for p in positions
        }

    def execute_buy(
        self,
        outcome_id: str,
        bracket: str,
        shares: float,
        price: float,
        action: str = 'BUY',
        our_prob: float = None,
        market_prob: float = None,
        edge: float = None,
        week_progress: float = None,
    ) -> bool:
        """
        Execute a paper buy order.

        Args:
            outcome_id: Token ID
            bracket: Bracket name (e.g., "180-199")
            shares: Number of shares to buy
            price: Price per share
            action: Action type (STRONG BUY, BUY, etc.)
            our_prob: Bot's calculated probability
            market_prob: Market's implied probability
            edge: Edge percentage
            week_progress: Market progress percentage (0-100, stays 0 until market starts)

        Returns:
            True if successful, False otherwise
        """
        total_cost = shares * price

        with self.db.get_session() as session:
            # Check if enough cash
            account = session.query(PaperTradingAccount).filter_by(id=self.account_id).first()

            if account.current_cash < total_cost:
                logger.error(f"Insufficient cash: ${account.current_cash:.2f} < ${total_cost:.2f}")
                return False

            # Deduct cash
            account.current_cash -= total_cost
            account.updated_at = datetime.utcnow()

            # Record trade
            trade = PaperTrade(
                account_id=self.account_id,
                timestamp=datetime.utcnow(),
                outcome_id=outcome_id,
                bracket=bracket,
                side='BUY',
                shares=shares,
                price=price,
                total_cost=total_cost,
                action=action,
                our_prob=our_prob,
                market_prob=market_prob,
                edge=edge,
                week_progress=week_progress,
                realized_pnl=0,
            )
            session.add(trade)

            # Update or create position
            position = session.query(PaperPosition).filter_by(
                account_id=self.account_id,
                outcome_id=outcome_id
            ).first()

            if position:
                # Update existing position (average cost)
                total_shares = position.shares + shares
                total_invested = position.total_cost + total_cost
                position.shares = total_shares
                position.average_cost = total_invested / total_shares
                position.total_cost = total_invested
                position.current_price = price
                position.current_value = total_shares * price
                position.unrealized_pnl = (total_shares * price) - total_invested
                position.updated_at = datetime.utcnow()
            else:
                # Create new position
                position = PaperPosition(
                    account_id=self.account_id,
                    outcome_id=outcome_id,
                    bracket=bracket,
                    shares=shares,
                    average_cost=price,
                    total_cost=total_cost,
                    current_price=price,
                    current_value=shares * price,
                    unrealized_pnl=0,
                )
                session.add(position)

            session.commit()

            logger.info(f"✓ PAPER BUY: {shares:.0f} shares of {bracket} @ ${price:.4f} (${total_cost:.2f})")
            return True

    def execute_sell(
        self,
        outcome_id: str,
        bracket: str,
        shares: float,
        price: float,
        action: str = 'SELL',
        our_prob: float = None,
        market_prob: float = None,
        edge: float = None,
        week_progress: float = None,
    ) -> bool:
        """
        Execute a paper sell order.

        Args:
            outcome_id: Token ID
            bracket: Bracket name
            shares: Number of shares to sell
            price: Price per share
            action: Action type (STRONG SELL, SELL, etc.)
            our_prob: Bot's calculated probability
            market_prob: Market's implied probability
            edge: Edge percentage
            week_progress: Market progress percentage (0-100, stays 0 until market starts)

        Returns:
            True if successful, False otherwise
        """
        with self.db.get_session() as session:
            # Check if position exists
            position = session.query(PaperPosition).filter_by(
                account_id=self.account_id,
                outcome_id=outcome_id
            ).first()

            if not position or position.shares < shares:
                logger.error(f"Insufficient shares: {position.shares if position else 0} < {shares}")
                return False

            # Calculate proceeds and P&L
            proceeds = shares * price
            cost_basis = shares * position.average_cost
            realized_pnl = proceeds - cost_basis

            # Add cash
            account = session.query(PaperTradingAccount).filter_by(id=self.account_id).first()
            account.current_cash += proceeds
            account.updated_at = datetime.utcnow()

            # Record trade
            trade = PaperTrade(
                account_id=self.account_id,
                timestamp=datetime.utcnow(),
                outcome_id=outcome_id,
                bracket=bracket,
                side='SELL',
                shares=shares,
                price=price,
                total_cost=-proceeds,  # Negative for sales
                action=action,
                our_prob=our_prob,
                market_prob=market_prob,
                edge=edge,
                week_progress=week_progress,
                realized_pnl=realized_pnl,
            )
            session.add(trade)

            # Update position
            position.shares -= shares
            position.total_cost -= cost_basis
            position.current_price = price
            position.updated_at = datetime.utcnow()

            if position.shares <= 0:
                # Close position
                session.delete(position)
            else:
                # Update position values
                position.current_value = position.shares * price
                position.unrealized_pnl = position.current_value - position.total_cost

            session.commit()

            logger.info(f"✓ PAPER SELL: {shares:.0f} shares of {bracket} @ ${price:.4f} "
                       f"(${proceeds:.2f}, P&L: ${realized_pnl:+.2f})")
            return True

    def update_position_prices(self, price_updates: Dict[str, float]):
        """
        Update current prices for all positions (for mark-to-market).

        Args:
            price_updates: Dict of {outcome_id: current_price}
        """
        with self.db.get_session() as session:
            positions = session.query(PaperPosition).filter_by(account_id=self.account_id).all()

            for position in positions:
                if position.outcome_id in price_updates:
                    new_price = price_updates[position.outcome_id]
                    position.current_price = new_price
                    position.current_value = position.shares * new_price
                    position.unrealized_pnl = position.current_value - position.total_cost
                    position.updated_at = datetime.utcnow()

            # Update account total value
            account = session.query(PaperTradingAccount).filter_by(id=self.account_id).first()
            total_position_value = sum(p.current_value or 0 for p in positions)
            account.total_value = account.current_cash + total_position_value
            account.updated_at = datetime.utcnow()

            session.commit()

    def create_snapshot(
        self,
        week_progress: float = None,
        predicted_count: int = None,
        actual_count: int = None,
    ):
        """
        Create a portfolio snapshot for performance tracking.

        Args:
            week_progress: Current market progress percentage (0-100, stays 0 until market starts)
            predicted_count: Bot's predicted tweet count
            actual_count: Actual tweet count so far
        """
        with self.db.get_session() as session:
            account = session.query(PaperTradingAccount).filter_by(id=self.account_id).first()
            positions = session.query(PaperPosition).filter_by(account_id=self.account_id).all()
            trades = session.query(PaperTrade).filter_by(account_id=self.account_id).all()

            position_value = sum(p.current_value or 0 for p in positions)
            total_value = account.current_cash + position_value
            total_pnl = total_value - account.starting_balance
            pnl_pct = (total_pnl / account.starting_balance) * 100 if account.starting_balance > 0 else 0

            # Calculate win rate
            closed_trades = [t for t in trades if t.side == 'SELL']
            winning_trades = [t for t in closed_trades if t.realized_pnl > 0]
            win_rate = (len(winning_trades) / len(closed_trades) * 100) if closed_trades else 0

            snapshot = PaperPortfolioSnapshot(
                account_id=self.account_id,
                timestamp=datetime.utcnow(),
                cash=account.current_cash,
                position_value=position_value,
                total_value=total_value,
                total_pnl=total_pnl,
                pnl_pct=pnl_pct,
                num_positions=len(positions),
                num_trades_total=len(trades),
                win_rate=win_rate,
                week_progress=week_progress,
                predicted_tweet_count=predicted_count,
                actual_tweet_count=actual_count,
            )
            session.add(snapshot)
            session.commit()

            logger.info(f"✓ Created portfolio snapshot: ${total_value:,.2f} ({pnl_pct:+.1f}%)")

    def get_performance_summary(self) -> Dict:
        """
        Get comprehensive performance summary.

        Returns:
            Dictionary with performance metrics
        """
        with self.db.get_session() as session:
            account = session.query(PaperTradingAccount).filter_by(id=self.account_id).first()
            positions = session.query(PaperPosition).filter_by(account_id=self.account_id).all()
            trades = session.query(PaperTrade).filter_by(account_id=self.account_id).all()

            position_value = sum(p.current_value or 0 for p in positions)
            total_value = account.current_cash + position_value
            total_pnl = total_value - account.starting_balance
            pnl_pct = (total_pnl / account.starting_balance) * 100 if account.starting_balance > 0 else 0

            # Trade statistics
            buy_trades = [t for t in trades if t.side == 'BUY']
            sell_trades = [t for t in trades if t.side == 'SELL']
            winning_trades = [t for t in sell_trades if t.realized_pnl > 0]
            losing_trades = [t for t in sell_trades if t.realized_pnl < 0]

            total_realized_pnl = sum(t.realized_pnl or 0 for t in sell_trades)
            total_unrealized_pnl = sum(p.unrealized_pnl or 0 for p in positions)

            win_rate = (len(winning_trades) / len(sell_trades) * 100) if sell_trades else 0
            avg_win = (sum(t.realized_pnl for t in winning_trades) / len(winning_trades)) if winning_trades else 0
            avg_loss = (sum(t.realized_pnl for t in losing_trades) / len(losing_trades)) if losing_trades else 0

            return {
                'account_name': account.name,
                'starting_balance': account.starting_balance,
                'current_cash': account.current_cash,
                'position_value': position_value,
                'total_value': total_value,
                'total_pnl': total_pnl,
                'pnl_pct': pnl_pct,
                'num_positions': len(positions),
                'num_trades': len(trades),
                'num_buys': len(buy_trades),
                'num_sells': len(sell_trades),
                'num_wins': len(winning_trades),
                'num_losses': len(losing_trades),
                'win_rate': win_rate,
                'realized_pnl': total_realized_pnl,
                'unrealized_pnl': total_unrealized_pnl,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
            }

    def reset_for_new_week(self, archive: bool = True) -> dict:
        """
        Reset paper trading account for a new market week.
        
        Args:
            archive: If True, archive current week's performance before resetting
            
        Returns:
            Dictionary with reset results and archived performance
        """
        with self.db.get_session() as session:
            account = session.query(PaperTradingAccount).filter_by(name=self.account_name).first()
            
            if not account:
                logger.warning(f"Account {self.account_name} not found")
                return {'error': 'Account not found'}
            
            # Calculate final performance before reset
            final_balance = self.get_balance()
            
            # Archive performance if requested
            archived_data = None
            if archive:
                archived_data = {
                    'account_name': self.account_name,
                    'starting_balance': account.starting_balance,
                    'final_balance': final_balance['total_value'],
                    'pnl': final_balance['pnl'],
                    'pnl_pct': final_balance['pnl_pct'],
                    'total_trades': len(session.query(PaperTrade).filter_by(account_id=account.id).all()),
                    'reset_date': datetime.utcnow(),
                }
                logger.info(f"Archived performance: P&L ${final_balance['pnl']:+.2f} ({final_balance['pnl_pct']:+.1f}%)")
            
            # Close all positions (delete them for reset)
            positions = session.query(PaperPosition).filter_by(
                account_id=account.id
            ).all()

            closed_positions = len(positions)
            for position in positions:
                session.delete(position)
            
            # Reset account balance
            old_balance = account.current_cash
            account.starting_balance = self.starting_balance
            account.current_cash = self.starting_balance
            account.total_value = self.starting_balance
            
            session.commit()
            
            logger.info(f"✓ Reset account '{self.account_name}': {closed_positions} positions closed, balance reset to $1000")
            
            return {
                'success': True,
                'archived_performance': archived_data,
                'positions_closed': closed_positions,
                'old_balance': old_balance,
                'new_balance': 1000.0,
            }
