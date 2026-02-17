#!/usr/bin/env python3
"""Approve CTF contract for the FUNDER address."""
import sys
import os
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

POLYGON_RPC = "https://polygon-rpc.com"
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

CTF_ABI = [
    {
        "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
        "name": "setApprovalForAll",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# You need the PRIVATE KEY for the funder address!
funder_private_key = os.getenv('POLYMARKET_FUNDER_PRIVATE_KEY', '').replace('0x', '')

if not funder_private_key:
    print("❌ POLYMARKET_FUNDER_PRIVATE_KEY not found in .env")
    print("\nYou need the private key for address: 0x314df4f4aa6bcd62a7b444d46af6e7220b6c65bf")
    print("Add it to your .env file as: POLYMARKET_FUNDER_PRIVATE_KEY=...")
    sys.exit(1)

w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
account = w3.eth.account.from_key(funder_private_key)

print(f"Funder Wallet: {account.address}")
print()

# Check if already approved
ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)
try:
    is_approved = ctf.functions.isApprovedForAll(account.address, EXCHANGE_ADDRESS).call()
    if is_approved:
        print("✅ Already approved - no action needed!")
        sys.exit(0)
except Exception as e:
    print(f"⚠️  Could not check approval status: {e}")

print("Approving CTF for trading...")

tx = ctf.functions.setApprovalForAll(EXCHANGE_ADDRESS, True).build_transaction({
    'from': account.address,
    'nonce': w3.eth.get_transaction_count(account.address),
    'gas': 100000,
    'gasPrice': w3.eth.gas_price,
    'chainId': 137,
})

signed = account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

print(f"TX sent: {tx_hash.hex()}")
print("Waiting for confirmation...")

for attempt in range(20):
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt:
            if receipt['status'] == 1:
                print("\n✅ CTF APPROVED FOR FUNDER!")
                print(f"\nFunder {account.address} can now sell tokens!")
                break
            else:
                print("\n❌ Transaction failed")
                break
    except Exception as e:
        if 'not found' in str(e).lower():
            time.sleep(3)
        else:
            print(f"Waiting... (attempt {attempt+1})")
            time.sleep(10)
else:
    print(f"\n⏳ Check status at: https://polygonscan.com/tx/{tx_hash.hex()}")
