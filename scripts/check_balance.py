#!/usr/bin/env python3
"""
Check USDC.e balance available for bot trading.
"""
import os
import sys
sys.path.insert(0, 'src')

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
from dotenv import load_dotenv

load_dotenv()

private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
if not private_key:
    print("Error: POLYMARKET_PRIVATE_KEY not found in .env")
    sys.exit(1)

if private_key.startswith("0x"):
    private_key = private_key[2:]

client = ClobClient("https://clob.polymarket.com", 137, private_key)
creds = client.create_or_derive_api_creds()
client.set_api_creds(creds)

print(f"Wallet: {client.get_address()}\n")

# Check balance
balance_result = client.get_balance_allowance(
    BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
)

if balance_result:
    balance_raw = balance_result.get('balance', '0')
    balance_usd = float(balance_raw) / 1e6

    print(f"USDC.e Balance: ${balance_usd:.2f}")
    print(f"Raw response: {balance_result}\n")

    if balance_usd > 0:
        print("✓ Bot can see your balance!")
        print("Restart your bot and it will start trading.")
    else:
        print("⚠️  Balance is $0")
        print("Make sure you:")
        print("1. Swapped native USDC → USDC.e")
        print("2. Ran: python3 scripts/approve_usdc.py")
else:
    print("Error: Could not get balance")
