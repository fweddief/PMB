#!/usr/bin/env python3
"""
Approve USDC.e for Polymarket CLOB trading.
Run this after swapping native USDC to USDC.e
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

print("Initializing CLOB client...")
client = ClobClient("https://clob.polymarket.com", 137, private_key)

print("Deriving API credentials...")
creds = client.create_or_derive_api_creds()
client.set_api_creds(creds)

print(f"Wallet address: {client.get_address()}\n")

print("Approving USDC.e for Polymarket CLOB trading...")
try:
    result = client.update_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print("✓ USDC.e approved!")
    print("\nWait ~30 seconds, then check balance:")
    print("python3 scripts/check_balance.py")
except Exception as e:
    print(f"Error: {e}")
