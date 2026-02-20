"""
Polymarket adapter — wraps the existing PolymarketTrader so MomentumTrader
can talk to Polymarket through the ExchangeAdapter interface.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests
from py_clob_client.clob_types import OrderArgs, BookParams, OrderType
from py_clob_client.order_builder.constants import BUY

from trading.exchange_adapter import ExchangeAdapter
from trading.polymarket_trader import PolymarketTrader

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
POLYGON_RPC_URLS = [
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
]


class PolymarketAdapter(ExchangeAdapter):
    exchange_name = "polymarket"

    def __init__(self):
        self.trader = PolymarketTrader()
        self.client = self.trader.client

    # ---- market discovery (moved from MomentumTrader.discover_markets) ----

    def discover_btc_15m_markets(self, now: datetime) -> List[dict]:
        now_ts = int(now.timestamp())
        current_boundary = now_ts - (now_ts % 900)
        new_markets = []

        for i in range(4):
            window_start = current_boundary + i * 900
            slug = f"btc-updown-15m-{window_start}"

            try:
                resp = requests.get(
                    f"{GAMMA_API}/events",
                    params={"slug": slug},
                    timeout=10,
                )
                resp.raise_for_status()
                events = resp.json()
            except Exception as e:
                logger.debug("Slug %s fetch failed: %s", slug, e)
                continue

            if not events:
                continue

            event = events[0] if isinstance(events, list) else events
            for market in event.get("markets", []):
                if not market.get("active", False):
                    continue
                if market.get("closed", True):
                    continue
                if not market.get("acceptingOrders", False):
                    continue

                raw_ids = market.get("clobTokenIds")
                if not raw_ids:
                    continue
                try:
                    clob_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                except (json.JSONDecodeError, TypeError):
                    continue

                if len(clob_ids) < 2:
                    continue

                up_token, down_token = clob_ids[0], clob_ids[1]

                end_str = market.get("endDate") or market.get("end_date_iso")
                if end_str:
                    try:
                        end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        end_date = datetime.fromtimestamp(window_start + 900, tz=timezone.utc)
                else:
                    end_date = datetime.fromtimestamp(window_start + 900, tz=timezone.utc)

                new_markets.append(
                    {
                        "yes_token": up_token,
                        "no_token": down_token,
                        "question": market.get("question", f"BTC 15m Up/Down ({slug})"),
                        "end_date": end_date,
                        "condition_id": market.get("conditionId", ""),
                        "slug": slug,
                    }
                )

        return new_markets

    # ---- order book ----

    def get_order_book(self, token_id: str) -> dict:
        books = self.client.get_order_books([BookParams(token_id=token_id)])
        if not books:
            return {"asks": [], "bids": []}
        book = books[0]
        asks = [{"price": float(o.price), "size": float(o.size)} for o in (book.asks or [])]
        bids = [{"price": float(o.price), "size": float(o.size)} for o in (book.bids or [])]
        return {"asks": asks, "bids": bids}

    # ---- balance ----

    def get_balance_cash(self) -> float:
        try:
            balance = self.trader.get_balance()
            return float(balance.get("cash", 0))
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return 0.0

    # ---- buy ----

    def buy(self, token_id: str, price: float, shares: int) -> dict:
        self.ensure_ready_to_trade(token_id)

        signed_order = self.client.create_order(
            OrderArgs(
                price=price,
                size=float(shares),
                side=BUY,
                token_id=token_id,
            )
        )
        result = self.client.post_order(signed_order, OrderType.GTC)

        taking = result.get("takingAmount", "")
        making = result.get("makingAmount", "")
        actual_shares = int(float(taking)) if taking else shares
        actual_cost = float(making) if making else shares * price
        actual_pps = actual_cost / actual_shares if actual_shares > 0 else price

        return {
            "shares": actual_shares,
            "cost": actual_cost,
            "price_per_share": actual_pps,
            "raw": result,
        }

    # ---- sell ----

    def sell(self, token_id: str, shares: int, price: float) -> Optional[dict]:
        return self.trader.create_market_sell_order(
            token_id=token_id,
            shares=shares,
            price=price,
        )

    # ---- token allowance ----

    def ensure_ready_to_trade(self, token_id: str) -> bool:
        return self.trader.ensure_token_allowance(token_id)

    # ---- settlement detection ----

    def check_settlement(self, token_id: str) -> Optional[str]:
        """Query Gamma API to determine market resolution. Returns 'yes', 'no', or None."""
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"clobTokenIds": token_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            market = data[0] if isinstance(data, list) else data
            if not market.get("resolved", False):
                return None
            # outcomePrices: ["1","0"] → yes won; ["0","1"] → no won
            prices = market.get("outcomePrices", [])
            if len(prices) >= 2:
                return "yes" if prices[0] == "1" else "no"
            return None
        except Exception as e:
            logger.debug("check_settlement failed: %s", e)
            return None

    # ---- on-chain redemption ----

    def redeem_position(self, condition_id: str, is_yes_token: bool) -> Optional[str]:
        """Submit redeemPositions() tx to ConditionalTokens contract."""
        import os
        from eth_abi import encode
        from eth_account import Account
        from eth_utils import keccak

        # function selector: keccak256("redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
        sig = b"redeemPositions(address,bytes32,bytes32,uint256[])"
        selector = keccak(sig)[:4]

        ctf_addr = self.client.get_conditional_address()
        collateral = self.client.get_collateral_address()
        condition_bytes = bytes.fromhex(condition_id.removeprefix("0x").zfill(64))
        parent = b"\x00" * 32
        index_sets = [1] if is_yes_token else [2]

        calldata = selector + encode(
            ["address", "bytes32", "bytes32", "uint256[]"],
            [collateral, parent, condition_bytes, index_sets],
        )

        pk = self.client.signer.private_key
        account = Account.from_key(pk)
        address = account.address

        rpc = os.getenv("POLYGON_RPC_URL", POLYGON_RPC_URLS[0])

        def rpc_call(method, params):
            r = requests.post(
                rpc,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                timeout=10,
            )
            return r.json().get("result")

        nonce = int(rpc_call("eth_getTransactionCount", [address, "latest"]), 16)
        gas_price = int(rpc_call("eth_gasPrice", []), 16)

        tx = {
            "to": ctf_addr,
            "data": "0x" + calldata.hex(),
            "nonce": nonce,
            "gas": 200_000,
            "gasPrice": gas_price,
            "chainId": 137,
            "value": 0,
        }
        signed = Account.sign_transaction(tx, pk)
        raw = signed.raw_transaction.hex()
        tx_hash = rpc_call("eth_sendRawTransaction", ["0x" + raw])
        if tx_hash:
            logger.info("Redemption tx submitted: %s", tx_hash)
        return tx_hash
