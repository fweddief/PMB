"""Check why auto-trading isn't executing on Railway."""

import sys
sys.path.insert(0, 'src')

from database import DatabaseManager
from services.trading_engine import TradingEngine, TradingMode
from analysis.monitor import BotMonitor
import os

print("=" * 60)
print("AUTO-TRADING EXECUTION CHECK")
print("=" * 60)
print()

db = DatabaseManager()
monitor = BotMonitor(db)

# Get active markets
with db.get_session() as session:
    from database import PolymarketMarket
    from datetime import datetime

    now = datetime.utcnow()
    markets = session.query(PolymarketMarket).filter(
        PolymarketMarket.week_start <= now,
        PolymarketMarket.week_end >= now
    ).all()

    print(f"Active Markets: {len(markets)}")
    for m in markets:
        print(f"  - {m.market_id}: {m.title[:50]}")

print()

# Try to get recommendations for first market
if markets:
    market_id = markets[0].market_id
    print(f"Testing recommendations for market {market_id}:")

    try:
        recs = monitor.get_recommendations(
            bankroll=1000.0,
            current_positions={},
            market_id=market_id
        )

        print(f"  Total recommendations: {len(recs)}")

        # Filter like the trading engine does
        min_edge = float(os.getenv('MIN_EDGE_FOR_TRADE', 3.0))
        min_kelly = float(os.getenv('MIN_KELLY_FOR_TRADE', 0.05))

        buys = [
            r for r in recs
            if not r.get("is_sell")
            and r["position_size"] > 0
            and r["edge"] > min_edge
            and r["kelly_fraction"] > min_kelly
        ]

        print(f"  Actionable buys (edge > {min_edge}%, kelly > {min_kelly}): {len(buys)}")

        if buys:
            print()
            print("  Top 3 buy opportunities:")
            for rec in buys[:3]:
                print(f"    {rec['bracket']}: {rec['action']}")
                print(f"      Edge: {rec['edge']:.1f}% | Kelly: {rec['kelly_fraction']:.3f}")
                print(f"      Size: ${rec['position_size']:.2f} | Timing: {rec['timing']}")
        else:
            print()
            print("  ⚠️  No actionable buys found!")
            print(f"  All recommendations:")
            for rec in recs[:5]:
                print(f"    {rec['bracket']}: {rec['action']}")
                print(f"      Edge: {rec['edge']:.1f}% | Kelly: {rec['kelly_fraction']:.3f}")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()

print()
print("=" * 60)
print("VERDICT:")
print("=" * 60)

# Check thresholds
min_edge = float(os.getenv('MIN_EDGE_FOR_TRADE', 3.0))
min_kelly = float(os.getenv('MIN_KELLY_FOR_TRADE', 0.05))
print(f"Trading thresholds: edge > {min_edge}%, kelly > {min_kelly}")
print()
print("If no trades are executing:")
print("  1. Check that recommendations meet thresholds")
print("  2. Verify collector is running (check for fresh data)")
print("  3. Check Railway logs for auto-trade cycle output")
print("  4. Ensure process_market_prices() is being called")
