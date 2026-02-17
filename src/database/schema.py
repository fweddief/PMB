"""
Database schema for Polymarket trading bot.
Tracks tweet data, market data, predictions, and trades.
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class TweetData(Base):
    """
    Stores real-time tweet count data from xtracker.io
    """
    __tablename__ = 'tweet_data'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    cumulative_count = Column(Integer, nullable=False)

    # Week tracking (for matching to Polymarket markets)
    week_start = Column(DateTime, nullable=False, index=True)
    week_end = Column(DateTime, nullable=False)

    # Legacy metrics (still used in dashboards)
    tweets_per_hour = Column(Float, nullable=True)  # Rolling average
    tweets_per_day = Column(Float, nullable=True)   # Rolling average

    # Rolling windows used by the new probabilistic model
    velocity_5m = Column(Float, nullable=True)   # 5-minute velocity for ultra-fast reaction
    velocity_20m = Column(Float, nullable=True)  # 20-minute velocity for fast reaction
    velocity_1h = Column(Float, nullable=True)
    velocity_6h = Column(Float, nullable=True)
    velocity_24h = Column(Float, nullable=True)
    acceleration = Column(Float, nullable=True)

    # Data source
    source = Column(String(50), default='xtracker')  # xtracker, twitter_api, manual

    __table_args__ = (
        Index('idx_week_timestamp', 'week_start', 'timestamp'),
    )

    def __repr__(self):
        return f"<TweetData(timestamp={self.timestamp}, count={self.cumulative_count})>"


class PolymarketMarket(Base):
    """
    Stores Polymarket market information
    """
    __tablename__ = 'polymarket_markets'

    id = Column(Integer, primary_key=True)
    market_id = Column(String(100), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)

    # Time range for the market
    week_start = Column(DateTime, nullable=False, index=True)
    week_end = Column(DateTime, nullable=False)

    # Market status
    is_active = Column(Boolean, default=True)
    resolution_date = Column(DateTime, nullable=True)
    resolved_outcome = Column(String(100), nullable=True)

    # Multi-market scanner fields
    category = Column(String(50), nullable=True, index=True)
    total_liquidity = Column(Float, nullable=True)
    total_volume_24h = Column(Float, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    outcomes = relationship("MarketOutcome", back_populates="market", cascade="all, delete-orphan")
    price_snapshots = relationship("PriceSnapshot", back_populates="market", cascade="all, delete-orphan")
    predictions = relationship("Prediction", back_populates="market", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="market", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<PolymarketMarket(id={self.market_id}, title={self.title})>"


class MarketOutcome(Base):
    """
    Stores individual outcomes/brackets for a market
    Example: <300, 300-350, 350-400, 400-450, 450-500, >500
    """
    __tablename__ = 'market_outcomes'

    id = Column(Integer, primary_key=True)
    market_id = Column(Integer, ForeignKey('polymarket_markets.id'), nullable=False)
    outcome_id = Column(String(100), unique=True, nullable=False, index=True)

    # Outcome description
    outcome_text = Column(String(200), nullable=False)  # e.g., "400-450"
    min_tweets = Column(Integer, nullable=True)  # Lower bound (tweet markets)
    max_tweets = Column(Integer, nullable=True)  # Upper bound (tweet markets)

    # Generic value bounds (for non-tweet markets: temperature, price, etc.)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    market = relationship("PolymarketMarket", back_populates="outcomes")

    def __repr__(self):
        return f"<MarketOutcome(outcome={self.outcome_text})>"


class PriceSnapshot(Base):
    """
    Stores price snapshots for market outcomes over time
    """
    __tablename__ = 'price_snapshots'

    id = Column(Integer, primary_key=True)
    market_id = Column(Integer, ForeignKey('polymarket_markets.id'), nullable=False)
    outcome_id = Column(String(100), nullable=False, index=True)

    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Price data
    price = Column(Float, nullable=False)  # Current price (0-1)
    bid = Column(Float, nullable=True)     # Best bid
    ask = Column(Float, nullable=True)     # Best ask

    # Volume and liquidity
    volume_24h = Column(Float, nullable=True)
    liquidity = Column(Float, nullable=True)

    # Relationships
    market = relationship("PolymarketMarket", back_populates="price_snapshots")

    __table_args__ = (
        Index('idx_outcome_timestamp', 'outcome_id', 'timestamp'),
    )

    def __repr__(self):
        return f"<PriceSnapshot(outcome={self.outcome_id}, price={self.price}, time={self.timestamp})>"


class Prediction(Base):
    """
    Stores bot's predictions over time
    """
    __tablename__ = 'predictions'

    id = Column(Integer, primary_key=True)
    market_id = Column(Integer, ForeignKey('polymarket_markets.id'), nullable=False)

    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Prediction data
    predicted_tweet_count = Column(Float, nullable=False)
    confidence_lower = Column(Float, nullable=True)  # Lower bound of confidence interval
    confidence_upper = Column(Float, nullable=True)  # Upper bound of confidence interval

    # Model inputs at time of prediction
    current_tweet_count = Column(Integer, nullable=False)
    tweets_per_hour_velocity = Column(Float, nullable=True)
    days_elapsed = Column(Float, nullable=False)

    # Model metadata
    model_version = Column(String(50), default='v1')

    # Relationships
    market = relationship("PolymarketMarket", back_populates="predictions")

    def __repr__(self):
        return f"<Prediction(predicted={self.predicted_tweet_count}, time={self.timestamp})>"


class Trade(Base):
    """
    Stores all trades executed by the bot
    """
    __tablename__ = 'trades'

    id = Column(Integer, primary_key=True)
    market_id = Column(Integer, ForeignKey('polymarket_markets.id'), nullable=False)
    outcome_id = Column(String(100), nullable=False)

    # Trade details
    trade_id = Column(String(100), unique=True, nullable=True)  # Polymarket trade ID
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    side = Column(String(10), nullable=False)  # 'buy' or 'sell'
    price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)  # Number of shares
    total_cost = Column(Float, nullable=False)  # Total USD cost/proceeds

    # Trade rationale
    strategy = Column(String(50), nullable=False)  # 'ladder', 'floor_shares', 'hedge', etc.
    notes = Column(String(500), nullable=True)

    # Status
    status = Column(String(20), default='pending')  # pending, filled, cancelled, failed

    # P&L tracking (filled in when position is closed)
    realized_pnl = Column(Float, nullable=True)

    # Relationships
    market = relationship("PolymarketMarket", back_populates="trades")

    def __repr__(self):
        return f"<Trade(outcome={self.outcome_id}, side={self.side}, price={self.price}, size={self.size})>"


class BotSetting(Base):
    """
    Stores runtime settings that need to be toggled remotely (e.g., auto-trading).
    """
    __tablename__ = 'bot_settings'

    id = Column(Integer, primary_key=True, default=1)
    auto_trading_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HistoricalMarketResolution(Base):
    """
    Stores resolved market data for backtesting and model improvement
    """
    __tablename__ = 'historical_resolutions'

    id = Column(Integer, primary_key=True)
    market_id = Column(String(100), nullable=False, index=True)

    week_start = Column(DateTime, nullable=False)
    week_end = Column(DateTime, nullable=False)

    actual_tweet_count = Column(Integer, nullable=False)
    winning_outcome = Column(String(100), nullable=False)

    resolution_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<HistoricalResolution(count={self.actual_tweet_count}, outcome={self.winning_outcome})>"
