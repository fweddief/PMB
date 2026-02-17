"""Force a trading cycle to test if auto-trading works."""

import sys
sys.path.insert(0, 'src')

from database import DatabaseManager
from services.trading_engine import TradingEngine, TradingMode
import os

print("=" * 60)
print("FORCE AUTO-TRADING TEST")
print("=" * 60)
print()

# Set up trading engine
db = DatabaseManager()
auto_trade_enabled = os.getenv("AUTO_TRADE", "false").lower() == "true"
engine = TradingEngine(db, mode=TradingMode.PAPER, enable_auto_trading=auto_trade_enabled)

print(f"Auto-trading enabled: {engine.auto_trading_enabled()}")
print(f"  - ENV variable: {auto_trade_enabled}")
print(f"  - DB setting: {engine.settings_service.is_auto_trading_enabled()}")
print()

# Force a trading cycle
print("Triggering auto-trading cycle...")
print("-" * 60)
try:
    engine.process_market_prices()
    print("-" * 60)
    print()

    # Check if any trades were executed
    from trading.paper_trader import PaperTradingTrader
    trader = PaperTradingTrader(db)
    positions = trader.get_open_positions()
    balance = trader.get_balance()

    print(f"Results:")
    print(f"  Positions: {len(positions)}")
    print(f"  Cash: ${balance['cash']:.2f}")
    print(f"  Position value: ${balance['position_value']:.2f}")

    if positions:
        print()
        print("Open positions:")
        for pos in positions[:5]:
            print(f"  - {pos.get('market_title', 'N/A')}: {pos['bracket']}")
            print(f"    {pos['shares']:.2f} shares @ ${pos['average_cost']:.4f}")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 60)
