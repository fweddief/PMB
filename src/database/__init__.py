"""Database module for Polymarket trading bot."""

from database.schema import Base, TweetData, PolymarketMarket, MarketOutcome, PriceSnapshot, Prediction, Trade, HistoricalMarketResolution, BotSetting
from database.paper_trading_schema import PaperTradingAccount, PaperTrade, PaperPosition, PaperPortfolioSnapshot
from database.db import DatabaseManager
from trading.paper_trader import PaperTradingTrader

__all__ = [
    'Base',
    'TweetData',
    'PolymarketMarket',
    'MarketOutcome',
    'PriceSnapshot',
    'Prediction',
    'Trade',
    'HistoricalMarketResolution',
    'BotSetting',
    'PaperTradingAccount',
    'PaperTrade',
    'PaperPosition',
    'PaperPortfolioSnapshot',
    'DatabaseManager',
    'PaperTradingTrader',
]
