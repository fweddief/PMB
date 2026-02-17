#!/usr/bin/env python3
"""Check if the funder address has CTF approval."""
from web3 import Web3

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

funder = "0x314df4f4aa6bcd62a7b444d46af6e7220b6c65bf"

w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
funder = w3.to_checksum_address(funder)
ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)

print(f"Checking CTF approval for funder: {funder}")
print()

is_approved = ctf.functions.isApprovedForAll(funder, EXCHANGE_ADDRESS).call()

print(f"CTF Operator Approval: {'✅ APPROVED' if is_approved else '❌ NOT APPROVED'}")
print(f"  Owner (Funder): {funder}")
print(f"  Operator (Exchange): {EXCHANGE_ADDRESS}")

if not is_approved:
    print("\n❌ The funder address does NOT have CTF approval!")
    print("   This is why sells are failing.")
    print("\n   To fix this, you need the private key for the funder address.")
    print("   Without it, you cannot sell tokens from that address.")
