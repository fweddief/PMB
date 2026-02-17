"""Test auto-trading setup and execution."""

import sys
sys.path.insert(0, 'src')

from database import DatabaseManager, PolymarketMarket, TweetData
from services.trading_engine import TradingEngine, TradingMode
from services.settings import BotSettingsService
from datetime import datetime
import os

print("=" * 60)
print("AUTO-TRADING DIAGNOSTIC TEST")
print("=" * 60)
print()

# 1. Check environment variables
print("1. Environment Variables:")
print(f"   AUTO_TRADE={os.getenv('AUTO_TRADE', 'NOT SET')}")
print()

# 2. Check database settings
print("2. Database Settings:")
db = DatabaseManager()
settings_service = BotSettingsService(db)
db_enabled = settings_service.is_auto_trading_enabled()
print(f"   auto_trading_enabled={db_enabled}")
print()

# 3. Check active markets
print("3. Active Markets:")
with db.get_session() as session:
    now = datetime.utcnow()
    markets = session.query(PolymarketMarket).filter(
        PolymarketMarket.week_start <= now,
        PolymarketMarket.week_end >= now,
    ).all()
    print(f"   Found {len(markets)} active markets")
    for m in markets:
        data_count = session.query(TweetData).filter(
            TweetData.week_start == m.week_start
        ).count()
        print(f"   - {m.market_id}: {m.title[:50]}...")
        print(f"     Data points: {data_count}")
print()

# 4. Check trading engine
print("4. Trading Engine Setup:")
auto_trade_env = os.getenv("AUTO_TRADE", "false").lower() == "true"
engine = TradingEngine(db, mode=TradingMode.PAPER, enable_auto_trading=auto_trade_env)
print(f"   enable_auto_trading (from env): {engine.enable_auto_trading}")
print(f"   auto_trading_enabled() method: {engine.auto_trading_enabled()}")
print()

# 5. Try to get recommendations
print("5. Recommendations Test:")
try:
    recs = engine.monitor.get_recommendations(
        bankroll=1000.0,
        current_positions={},
        market_id=markets[0].market_id if markets else None
    )
    print(f"   Generated {len(recs)} recommendations")
    if recs:
        for rec in recs[:3]:  # Show first 3
            print(f"   - {rec['bracket']}: {rec['action']} (edge: {rec['edge']:.1f}%)")
    else:
        print("   ⚠️  No recommendations (might need more data)")
except Exception as e:
    print(f"   ❌ Error: {e}")
print()

# 6. Check paper trader balance
print("6. Paper Trader Status:")
from trading.paper_trader import PaperTradingTrader
trader = PaperTradingTrader(db)
balance = trader.get_balance()
print(f"   Cash: ${balance['cash']:.2f}")
print(f"   Positions: ${balance['position_value']:.2f}")
print(f"   Total: ${balance['total_value']:.2f}")
positions = trader.get_open_positions()
print(f"   Open positions: {len(positions)}")
print()

# 7. Final verdict
print("=" * 60)
print("VERDICT:")
print("=" * 60)

issues = []
if not auto_trade_env:
    issues.append("❌ AUTO_TRADE env var not set to 'true'")
if not db_enabled:
    issues.append("❌ Database setting is disabled")
if len(markets) == 0:
    issues.append("❌ No active markets found")
for m in markets:
    with db.get_session() as session:
        count = session.query(TweetData).filter(TweetData.week_start == m.week_start).count()
        if count < 2:
            issues.append(f"⚠️  Market {m.market_id} has insufficient data ({count} < 2)")

if not issues:
    print("✅ Auto-trading should be working!")
    print("   If no trades are executing, check:")
    print("   - Recommendations have edge > 3%")
    print("   - Railway service has restarted with new code")
else:
    print("Issues found:")
    for issue in issues:
        print(f"   {issue}")
print()

print("Recommendations:")
if not auto_trade_env:
    print("   1. Set AUTO_TRADE=true in Railway environment variables")
if any("insufficient data" in i for i in issues):
    print("   2. Wait 2-3 collection cycles for data to accumulate")
    print("   3. After restart, collector will populate ALL active markets")
