#!/usr/bin/env python3
"""Approve only the CTF contract (USDC already done)."""
import sys
import os
import time
sys.path.insert(0, 'src')
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

POLYGON_RPC = "https://polygon-rpc.com"
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

CTF_ABI = [{
    "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
    "name": "setApprovalForAll",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
}]

private_key = os.getenv('POLYMARKET_PRIVATE_KEY', '').replace('0x', '')
w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
account = w3.eth.account.from_key(private_key)

print(f"Wallet: {account.address}")
print("\nApproving CTF for trading...")

ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)

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
print("Waiting for confirmation (with retries)...")

for attempt in range(20):
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt:
            if receipt['status'] == 1:
                print("\n✅ CTF APPROVED!")
                print("\n🎉 ALL APPROVALS COMPLETE - YOU CAN NOW TRADE!")
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
