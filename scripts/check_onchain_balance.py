#!/usr/bin/env python3
"""Check on-chain USDC balance and approvals."""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

POLYGON_RPC = "https://polygon-rpc.com"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

ERC20_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

private_key = os.getenv('POLYMARKET_PRIVATE_KEY', '').replace('0x', '')
w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
account = w3.eth.account.from_key(private_key)

print(f"Wallet: {account.address}")
print()

usdc = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)

# Check balance
balance = usdc.functions.balanceOf(account.address).call()
balance_usdc = balance / 1e6
print(f"USDC Balance: ${balance_usdc:.2f}")

# Check allowance
allowance = usdc.functions.allowance(account.address, EXCHANGE_ADDRESS).call()
allowance_usdc = allowance / 1e6
print(f"Exchange Allowance: ${allowance_usdc:.2f}")

if allowance == 0:
    print("\n❌ Allowance is 0! The approval didn't work or was for a different address.")
elif allowance == 2**256 - 1:
    print("\n✅ Allowance is MAX (unlimited)")
else:
    print(f"\n✅ Allowance set to ${allowance_usdc:,.2f}")
