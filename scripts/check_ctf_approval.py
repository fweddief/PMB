#!/usr/bin/env python3
"""Check CTF operator approval status."""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

POLYGON_RPC = "https://polygon-rpc.com"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

CTF_ABI = [{
    "inputs": [
        {"name": "owner", "type": "address"},
        {"name": "operator", "type": "address"}
    ],
    "name": "isApprovedForAll",
    "outputs": [{"name": "", "type": "bool"}],
    "stateMutability": "view",
    "type": "function"
}]

private_key = os.getenv('POLYMARKET_PRIVATE_KEY', '').replace('0x', '')
w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
account = w3.eth.account.from_key(private_key)

print(f"Wallet: {account.address}")
print()

ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)

# Check if exchange is approved as operator
is_approved = ctf.functions.isApprovedForAll(account.address, EXCHANGE_ADDRESS).call()

print(f"CTF Operator Approval: {'✅ APPROVED' if is_approved else '❌ NOT APPROVED'}")
print(f"  Owner: {account.address}")
print(f"  Operator: {EXCHANGE_ADDRESS}")

if not is_approved:
    print("\n❌ The exchange is NOT approved to transfer your conditional tokens!")
    print("   Run: python scripts/approve_ctf_only.py")
