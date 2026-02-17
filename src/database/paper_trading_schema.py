"""
Paper trading schema for tracking simulated trades and portfolio performance.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class PaperTradingAccount(Base):
    """Paper trading account tracking."""
    __tablename__ = 'paper_trading_accounts'

    id = Column(Integer, primary_key=True)
    name = Column(String, default='Main Account')
    starting_balance = Column(Float, nullable=False)
    current_cash = Column(Float, nullable=False)
    total_value = Column(Float)  # Cash + position values
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    trades = relationship('PaperTrade', back_populates='account')
    positions = relationship('PaperPosition', back_populates='account')
    snapshots = relationship('PaperPortfolioSnapshot', back_populates='account')


class PaperTrade(Base):
    """Individual paper trades (buys and sells)."""
    __tablename__ = 'paper_trades'

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('paper_trading_accounts.id'), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Trade details
    outcome_id = Column(String, nullable=False)  # Token ID
    bracket = Column(String, nullable=False)  # Human readable (e.g., "180-199")
    side = Column(String, nullable=False)  # BUY or SELL
    shares = Column(Float, nullable=False)
    price = Column(Float, nullable=False)  # Execution price
    total_cost = Column(Float, nullable=False)  # shares * price (negative for sales)

    # Strategy context
    action = Column(String)  # STRONG BUY, BUY, SELL, etc.
    our_prob = Column(Float)  # Bot's calculated probability
    market_prob = Column(Float)  # Market's implied probability
    edge = Column(Float)  # our_prob - market_prob
    week_progress = Column(Float)  # % of market period elapsed when trade made (0% until market starts)

    # Performance tracking
    realized_pnl = Column(Float)  # For sells: profit/loss vs average cost basis

    # Relationships
    account = relationship('PaperTradingAccount', back_populates='trades')


class PaperPosition(Base):
    """Current paper trading positions."""
    __tablename__ = 'paper_positions'

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('paper_trading_accounts.id'), nullable=False)
    outcome_id = Column(String, nullable=False, unique=True)
    bracket = Column(String, nullable=False)

    # Position details
    shares = Column(Float, nullable=False)
    average_cost = Column(Float, nullable=False)  # Average price paid per share
    total_cost = Column(Float, nullable=False)  # Total invested
    current_price = Column(Float)  # Latest market price
    current_value = Column(Float)  # shares * current_price
    unrealized_pnl = Column(Float)  # current_value - total_cost

    # Timestamps
    opened_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    account = relationship('PaperTradingAccount', back_populates='positions')


class PaperPortfolioSnapshot(Base):
    """Periodic snapshots of portfolio value for performance tracking."""
    __tablename__ = 'paper_portfolio_snapshots'

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('paper_trading_accounts.id'), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Portfolio state
    cash = Column(Float, nullable=False)
    position_value = Column(Float, nullable=False)
    total_value = Column(Float, nullable=False)
    total_pnl = Column(Float)  # total_value - starting_balance
    pnl_pct = Column(Float)  # (total_value - starting_balance) / starting_balance * 100

    # Performance metrics
    num_positions = Column(Integer)
    num_trades_total = Column(Integer)
    win_rate = Column(Float)  # % of profitable closed trades
    sharpe_ratio = Column(Float)  # Risk-adjusted returns

    # Market context
    week_progress = Column(Float)  # % of market period elapsed (0% until market starts)
    predicted_tweet_count = Column(Integer)
    actual_tweet_count = Column(Integer)

    # Relationships
    account = relationship('PaperTradingAccount', back_populates='snapshots')
