"""
Database schema for the multi-market scanner.

Tables:
- ScannedMarket: tracks all discovered markets and their categories
- EstimatorResult: stores fair value estimates from each estimator
- Opportunity: tradeable opportunities meeting edge/confidence thresholds
- CategoryPerformance: aggregated performance stats per category
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text, Index
from datetime import datetime

from .schema import Base


class ScannedMarket(Base):
    """
    Tracks all markets discovered by the scanner.
    """
    __tablename__ = 'scanned_markets'

    id = Column(Integer, primary_key=True)
    market_id = Column(String(100), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    slug = Column(String(300), nullable=True)

    # Classification
    category = Column(String(50), nullable=False, default='unknown', index=True)
    category_confidence = Column(Float, nullable=True)

    # Market metrics
    liquidity = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    num_outcomes = Column(Integer, nullable=True)

    # Scanner state
    best_edge = Column(Float, nullable=True)
    last_scanned = Column(DateTime, default=datetime.utcnow)
    last_estimated = Column(DateTime, nullable=True)

    # Market timing
    end_date = Column(String(50), nullable=True)
    start_date = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_scanned_category_edge', 'category', 'best_edge'),
        Index('idx_scanned_active', 'is_active', 'last_scanned'),
    )

    def __repr__(self):
        return f"<ScannedMarket({self.market_id}, cat={self.category}, edge={self.best_edge})>"


class EstimatorResult(Base):
    """
    Stores fair value estimates from each estimator run.
    """
    __tablename__ = 'estimator_results'

    id = Column(Integer, primary_key=True)
    market_id = Column(String(100), nullable=False, index=True)
    outcome_id = Column(String(100), nullable=False, index=True)

    # Estimator info
    estimator_name = Column(String(50), nullable=False)
    category = Column(String(50), nullable=False)

    # Estimate values
    fair_probability = Column(Float, nullable=False)
    market_price = Column(Float, nullable=True)
    edge = Column(Float, nullable=True)  # fair_probability - market_price
    confidence = Column(Float, nullable=True)

    # Context
    reasoning = Column(Text, nullable=True)
    external_data = Column(Text, nullable=True)  # JSON string

    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index('idx_estimator_market_outcome', 'market_id', 'outcome_id'),
        Index('idx_estimator_edge', 'edge'),
    )

    def __repr__(self):
        return f"<EstimatorResult({self.market_id}/{self.outcome_id}, edge={self.edge})>"


class Opportunity(Base):
    """
    Tradeable opportunities meeting edge/confidence thresholds.
    """
    __tablename__ = 'opportunities'

    id = Column(Integer, primary_key=True)
    market_id = Column(String(100), nullable=False, index=True)
    outcome_id = Column(String(100), nullable=False, index=True)
    market_title = Column(String(500), nullable=True)

    # Classification
    category = Column(String(50), nullable=False, index=True)

    # Pricing
    market_price = Column(Float, nullable=False)
    fair_value = Column(Float, nullable=False)
    edge = Column(Float, nullable=False)  # fair_value - market_price
    confidence = Column(Float, nullable=True)

    # Sizing
    kelly_fraction = Column(Float, nullable=True)
    position_size_usd = Column(Float, nullable=True)

    # Ranking
    score = Column(Float, nullable=True)
    liquidity = Column(Float, nullable=True)

    # Status: pending, traded, expired, passed, exited
    status = Column(String(20), default='pending', index=True)
    trade_id = Column(String(100), nullable=True)  # Links to Trade if executed

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    traded_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('idx_opportunity_status_edge', 'status', 'edge'),
        Index('idx_opportunity_category', 'category', 'status'),
    )

    def __repr__(self):
        return f"<Opportunity({self.market_id}, edge={self.edge:.2%}, status={self.status})>"


class CategoryPerformance(Base):
    """
    Aggregated performance statistics per category.
    Updated periodically by the scanner runtime.
    """
    __tablename__ = 'category_performance'

    id = Column(Integer, primary_key=True)
    category = Column(String(50), unique=True, nullable=False, index=True)

    # Trade counts
    total_trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    pending = Column(Integer, default=0)

    # P&L
    total_pnl = Column(Float, default=0.0)
    total_invested = Column(Float, default=0.0)
    roi_pct = Column(Float, default=0.0)

    # Accuracy
    hit_rate = Column(Float, default=0.0)  # wins / (wins + losses)
    avg_edge = Column(Float, default=0.0)
    avg_confidence = Column(Float, default=0.0)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<CategoryPerformance({self.category}, roi={self.roi_pct:.1f}%, trades={self.total_trades})>"
