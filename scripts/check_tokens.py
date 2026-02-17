#!/usr/bin/env python3
"""
Check what tokens are in the wallet and help redeem them.
"""
import os
import sys
sys.path.insert(0, 'src')

from py_clob_client.client import ClobClient
from dotenv import load_dotenv

load_dotenv()

private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
if private_key and private_key.startswith("0x"):
    private_key = private_key[2:]

client = ClobClient("https://clob.polymarket.com", 137, private_key)
wallet = client.get_address()

print(f"Wallet: {wallet}\n")

# Check for tokens via the CLOB API
try:
    # Derive credentials
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)

    print("Checking for unredeemed tokens...")
    print("\nTo redeem your tokens and convert them to USDC:")
    print("1. Go to polymarket.com/portfolio")
    print("2. Click 'History' tab")
    print("3. Look for any resolved markets with a 'Claim' or 'Redeem' button")
    print("4. Click it to convert your outcome tokens to USDC")
    print("\nNote: You need POL for gas to complete the redemption transaction.")

except Exception as e:
    print(f"Error: {e}")
