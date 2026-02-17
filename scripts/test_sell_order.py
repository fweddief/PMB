#!/usr/bin/env python3
"""Test a sell order to debug the issue."""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

os.environ['POLYMARKET_FUNDER_ADDRESS'] = '0x314df4f4aa6bcd62a7b444d46af6e7220b6c65bf'

from trading.polymarket_trader import PolymarketTrader
import requests

trader = PolymarketTrader()
print(f'Proxy/Signer: {trader.client.get_address()}')
print(f'Funder: {trader.funder_address}')
print()

# Get a position from the funder address
response = requests.get('https://data-api.polymarket.com/positions', params={'user': trader.funder_address})
positions = response.json()

if not positions:
    print("No positions found!")
    sys.exit(1)

# Find a position with shares
pos = None
for p in positions:
    if float(p.get('size', 0)) > 10:
        pos = p
        break

if not pos:
    print("No positions with >10 shares")
    sys.exit(1)

token_id = pos.get('asset_id') or pos.get('asset')
shares = float(pos.get('size', 0))
price = float(pos.get('price', 0))

print(f'Found position:')
print(f'  Token: {token_id}')
print(f'  Shares: {shares}')
print(f'  Price: ${price:.4f}')
print()

# Try to sell a small amount
sell_shares = min(5, int(shares))
print(f'Attempting to sell {sell_shares} shares...')
print()

result = trader.create_market_sell_order(
    token_id=token_id,
    shares=sell_shares,
    price=price,
)

if result:
    print(f'✅ Sell order created!')
    print(f'   Order ID: {result.get("orderID")}')
else:
    print(f'❌ Sell order failed')
