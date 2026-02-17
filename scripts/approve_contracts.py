#!/usr/bin/env python3
"""
Approve USDC and Conditional Token contracts for Polymarket trading.

This script:
1. Approves USDC spending for the CLOB exchange
2. Sets operator approval for the Conditional Token Framework (CTF)

Run this ONCE before trading.
"""
import sys
import os
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from dotenv import load_dotenv
load_dotenv()

try:
    from web3 import Web3
except ImportError:
    print("❌ web3 library not installed")
    print("Install: pip install web3")
    sys.exit(1)

# Contract addresses (from py-clob-client docs)
# Use Alchemy/Infura for better reliability (or public endpoints)
POLYGON_RPC = "https://polygon-rpc.com"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # Main exchange
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Conditional Token Framework

# ERC20 approve ABI
ERC20_ABI = [{
    "inputs": [
        {"name": "spender", "type": "address"},
        {"name": "amount", "type": "uint256"}
    ],
    "name": "approve",
    "outputs": [{"name": "", "type": "bool"}],
    "stateMutability": "nonpayable",
    "type": "function"
}]

# CTF setApprovalForAll ABI
CTF_ABI = [{
    "inputs": [
        {"name": "operator", "type": "address"},
        {"name": "approved", "type": "bool"}
    ],
    "name": "setApprovalForAll",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
}, {
    "inputs": [
        {"name": "owner", "type": "address"},
        {"name": "operator", "type": "address"}
    ],
    "name": "isApprovedForAll",
    "outputs": [{"name": "", "type": "bool"}],
    "stateMutability": "view",
    "type": "function"
}]

def wait_for_receipt_with_retry(w3, tx_hash, attempts=10, delay=10):
    """Wait for tx receipt, retrying on rate-limit errors."""
    last_exc = None
    for _ in range(attempts):
        try:
            return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        except Exception as exc:
            msg = str(exc).lower()
            if 'rate limit' in msg or 'too many requests' in msg:
                last_exc = exc
                print("  ⚠️  Rate limited while fetching receipt, retrying...")
                time.sleep(delay)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Could not fetch transaction receipt")


def approve_usdc(w3, account, gas_price):
    """Approve USDC for exchange contract."""
    print("\n1️⃣  Approving USDC for exchange...")

    usdc = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)

    # Approve max amount (standard practice)
    max_amount = 2**256 - 1

    tx = usdc.functions.approve(EXCHANGE_ADDRESS, max_amount).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 100000,
        'gasPrice': gas_price,
        'chainId': 137,
    })
    
    print(f"  Gas cost: ~${float(w3.from_wei(tx['gas'] * tx['gasPrice'], 'ether')) * 0.50:.4f}")
    
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    
    print(f"  TX: {tx_hash.hex()}")
    print("  Waiting for confirmation...")
    
    receipt = wait_for_receipt_with_retry(w3, tx_hash)
    
    if receipt['status'] == 1:
        print("  ✅ USDC approved!")
        return True
    else:
        print("  ❌ Transaction failed")
        return False

def approve_ctf(w3, account, gas_price):
    """Set operator approval for Conditional Token Framework."""
    print("\n2️⃣  Setting CTF operator approval...")

    ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)

    # Check if already approved (skip if rate limited)
    try:
        is_approved = ctf.functions.isApprovedForAll(account.address, EXCHANGE_ADDRESS).call()

        if is_approved:
            print("  ✅ Already approved - skipping")
            return True
    except Exception as e:
        if 'rate limit' in str(e).lower():
            print("  ⚠️  Rate limited when checking - proceeding with approval anyway")
        else:
            print(f"  ⚠️  Could not check approval status: {e}")

    tx = ctf.functions.setApprovalForAll(EXCHANGE_ADDRESS, True).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 100000,
        'gasPrice': gas_price,
        'chainId': 137,
    })
    
    print(f"  Gas cost: ~${float(w3.from_wei(tx['gas'] * tx['gasPrice'], 'ether')) * 0.50:.4f}")
    
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    
    print(f"  TX: {tx_hash.hex()}")
    print("  Waiting for confirmation...")
    
    receipt = wait_for_receipt_with_retry(w3, tx_hash)
    
    if receipt['status'] == 1:
        print("  ✅ CTF approved!")
        return True
    else:
        print("  ❌ Transaction failed")
        return False

def main():
    print("=" * 60)
    print("Polymarket Contract Approval Script")
    print("=" * 60)
    
    private_key = os.getenv('POLYMARKET_PRIVATE_KEY')
    
    if not private_key:
        print("\n❌ POLYMARKET_PRIVATE_KEY not found in .env")
        return
    
    if private_key.startswith('0x'):
        private_key = private_key[2:]
    
    # Connect to Polygon
    print(f"\n🔗 Connecting to Polygon...")
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

    try:
        # Test connection by getting chain ID
        chain_id = w3.eth.chain_id
        if chain_id != 137:
            print(f"❌ Wrong chain (expected Polygon/137, got {chain_id})")
            return
        print("✅ Connected to Polygon")
    except Exception as e:
        print(f"❌ Could not connect to Polygon: {e}")
        return
    
    # Get account
    account = w3.eth.account.from_key(private_key)
    print(f"\n👛 Wallet: {account.address}")
    
    # Check MATIC balance for gas
    matic_balance = w3.eth.get_balance(account.address)
    print(f"💰 MATIC balance: {float(w3.from_wei(matic_balance, 'ether')):.4f} MATIC")
    
    if matic_balance < w3.to_wei(0.01, 'ether'):
        print("\n⚠️  Low MATIC balance - you need MATIC for gas fees")
        print("   Get some from https://wallet.polygon.technology/")
    
    print("\n" + "=" * 60)
    print("This will send 2 transactions to approve contracts")
    print("=" * 60)

    # Get gas price once to avoid rate limits
    print("\n⛽ Getting gas price...")
    gas_price = w3.eth.gas_price
    print(f"Gas price: {w3.from_wei(gas_price, 'gwei')} gwei")

    confirm = input("\nProceed? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Cancelled")
        return

    # Approve USDC
    if not approve_usdc(w3, account, gas_price):
        print("\n❌ USDC approval failed - stopping")
        return

    # Wait to avoid rate limits
    print("\n⏳ Waiting 15 seconds to avoid rate limits...")
    time.sleep(15)

    # Approve CTF
    if not approve_ctf(w3, account, gas_price):
        print("\n❌ CTF approval failed")
        return
    
    print("\n" + "=" * 60)
    print("✅ ALL APPROVALS COMPLETE!")
    print("=" * 60)
    print("\nYou can now trade on Polymarket 🎉")

if __name__ == "__main__":
    main()
