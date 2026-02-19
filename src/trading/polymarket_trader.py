"""
Polymarket trader for executing buy/sell orders using the CLOB API.
"""

import os
import time
import logging
from typing import Dict, List, Optional
from decimal import Decimal
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs,
    MarketOrderArgs,
    ApiCreds,
    OrderType,
    BalanceAllowanceParams,
    AssetType,
)
from py_clob_client.constants import POLYGON
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.exceptions import PolyApiException
from py_clob_client.exceptions import PolyApiException

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class PolymarketTrader:
    """
    Executes trades on Polymarket using the CLOB (Central Limit Order Book) API.
    """

    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        api_passphrase: str = None,
        private_key: str = None,
        chain_id: int = POLYGON,
        max_position_size: float = 100.0,
        max_total_exposure: float = 500.0,
    ):
        """
        Initialize Polymarket trader.

        Args:
            api_key: Polymarket API key (from .env)
            api_secret: Polymarket API secret (from .env)
            api_passphrase: Polymarket API passphrase (from .env)
            private_key: Ethereum private key for signing transactions
            chain_id: Chain ID (POLYGON for Polymarket)
            max_position_size: Maximum size for a single position in USD
            max_total_exposure: Maximum total exposure across all positions in USD
        """
        # Load from environment if not provided
        self.api_key = api_key or os.getenv('POLYMARKET_API_KEY')
        self.api_secret = api_secret or os.getenv('POLYMARKET_SECRET')
        self.api_passphrase = api_passphrase or os.getenv('POLYMARKET_PASSPHRASE')
        self.private_key = private_key or os.getenv('POLYMARKET_PRIVATE_KEY')
        self.signature_type = int(os.getenv('POLYMARKET_SIGNATURE_TYPE', '1'))
        self.funder_address = os.getenv('POLYMARKET_FUNDER_ADDRESS')

        # Risk management
        self.max_position_size = max_position_size
        self.max_total_exposure = max_total_exposure

        # Cache for approved tokens to avoid rate limiting
        self._approved_tokens: set[str] = set()

        # Initialize CLOB client
        try:
            # Remove 0x prefix from private key if present
            private_key = self.private_key
            if private_key and private_key.startswith("0x"):
                private_key = private_key[2:]

            # Initialize client with GNOSIS_SAFE signature type
            # This is required for Polymarket proxy wallet trading
            # The funder is your EOA address derived from the private key
            from py_clob_client.signer import Signer
            signer = Signer(private_key, chain_id)
            funder_address = self.funder_address or signer.address()

            self.client = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=chain_id,
                key=private_key,
                signature_type=self.signature_type,
                funder=funder_address,  # Your EOA address that funds the trades
            )
            self.positions_address = os.getenv("POLYMARKET_POSITIONS_ADDRESS") or self.client.get_address()

            logger.info(
                "PolymarketTrader initialized signer=%s sig_type=%s",
                self.client.get_address(),
                getattr(self.client.builder, "sig_type", None),
            )

            # Use explicit API credentials if provided, otherwise derive from private key
            try:
                creds = None
                using_env_creds = False

                if self.api_key and self.api_secret and self.api_passphrase:
                    logger.info("Loading API credentials from environment...")
                    creds = ApiCreds(
                        api_key=self.api_key,
                        api_secret=self.api_secret,
                        api_passphrase=self.api_passphrase,
                    )
                    using_env_creds = True
                else:
                    logger.info("Deriving API credentials from private key...")
                    creds = self.client.create_or_derive_api_creds()

                if creds:
                    self.client.set_api_creds(creds)

                    if using_env_creds:
                        # Validate the creds actually belong to this wallet; fallback if not
                        try:
                            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

                            self.client.get_balance_allowance(
                                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                            )
                            logger.info("✓ Polymarket CLOB client authenticated with env credentials")
                        except PolyApiException as api_err:
                            if api_err.status_code == 401:
                                logger.warning("Env API creds rejected (401). Falling back to derived creds...")
                                creds = self.client.create_or_derive_api_creds()
                                if creds:
                                    self.client.set_api_creds(creds)
                                    logger.info("✓ Polymarket CLOB client authenticated with derived creds")
                                else:
                                    logger.warning("Could not derive API credentials after env failure")
                            else:
                                raise
                    else:
                        logger.info("✓ Polymarket CLOB client authenticated")
                else:
                    logger.warning("Could not obtain API credentials - client stays in read-only mode")
            except Exception as cred_error:
                logger.warning(f"Could not configure API credentials: {cred_error}")
                logger.info("✓ Polymarket CLOB client initialized (public methods only)")

        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            raise

    def ensure_allowance(self, min_allowance: float = 1000.0) -> bool:
        """
        Ensure CLOB contract has enough allowance to spend USDC.

        Args:
            min_allowance: Minimum allowance required in USDC

        Returns:
            True if allowance is sufficient, False otherwise
        """
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

            # Check current allowance
            balance_result = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )

            allowance_raw = balance_result.get('allowance', '0') if balance_result else '0'
            current_allowance = float(allowance_raw) / 1e6  # Convert from micro-USDC

            logger.info(f"Current CLOB allowance: ${current_allowance:,.2f}")

            if current_allowance < min_allowance:
                logger.warning(f"Allowance too low (${current_allowance:.2f}). Setting to $1,000,000...")
                # Calling update_balance_allowance without a token_id bumps allowance to a large default
                params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                self.client.update_balance_allowance(params)
                logger.info("✓ Allowance set successfully")
                return True

            return True

        except Exception as e:
            logger.error(f"Failed to check/set allowance: {e}")
            return False

    def get_balance(self) -> Dict:
        """
        Get current USDC balance and positions value.

        Returns:
            Dictionary with balance information
        """
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

            # Get USDC collateral balance
            balance_result = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )

            # Balance is returned in USDC base units (6 decimals), convert to dollars
            balance_raw = balance_result.get('balance', '0') if balance_result else '0'
            allowance_raw = balance_result.get('allowance', '0') if balance_result else '0'

            usdc_balance = float(balance_raw) / 1e6  # Convert from micro-USDC to USDC
            allowance = float(allowance_raw) / 1e6

            # Get positions value
            positions = self.get_open_positions()
            positions_value = sum(p.get('current_value', 0) for p in positions)
            realized_pnl = sum(p.get('realized_pnl', 0) for p in positions)
            unrealized_pnl = sum(p.get('unrealized_pnl', 0) for p in positions)
            total_pnl = realized_pnl + unrealized_pnl
            total_value = usdc_balance + positions_value
            invested_capital = total_value - total_pnl
            pnl_pct = (total_pnl / invested_capital * 100) if invested_capital else 0

            return {
                'cash': usdc_balance,
                'allowance': allowance,
                'position_value': positions_value,
                'total_value': total_value,
                'pnl': total_pnl,
                'pnl_pct': pnl_pct,
                'realized_pnl': realized_pnl,
                'unrealized_pnl': unrealized_pnl,
            }
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {'cash': 0, 'allowance': 0, 'position_value': 0, 'total_value': 0, 'pnl': 0, 'pnl_pct': 0}

    def get_open_positions(self) -> List[Dict]:
        """
        Get all current open positions using Data API.

        Returns:
            List of open positions with format matching paper trader
        """
        try:
            import requests

            # Get address to query positions for (override to funder if needed)
            user_address = self.positions_address

            # Query Data API for positions
            url = "https://data-api.polymarket.com/positions"
            params = {"user": user_address}

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            positions_data = response.json()

            # Transform to match paper trader format
            positions = []
            for pos in positions_data:
                shares = float(pos.get('size') or pos.get('totalBought') or 0)
                if shares <= 0:
                    continue

                # NOTE: Elon-only filter removed to support all market types.
                # Previously filtered positions to only show Elon-related markets.

                total_spent = (
                    pos.get('invested')
                    or pos.get('valueBought')
                    or pos.get('totalSpent')
                    or pos.get('initial_value')
                    or pos.get('initialValue')
                    or 0
                )
                try:
                    total_spent = float(total_spent)
                except Exception:
                    total_spent = 0.0

                avg_cost = pos.get('avgPrice')
                if avg_cost is not None:
                    try:
                        avg_cost = float(avg_cost)
                    except Exception:
                        avg_cost = None

                if avg_cost is None:
                    avg_cost = (total_spent / shares) if shares else 0.0
                current_price = float(
                    pos.get('price')
                    or pos.get('curPrice')
                    or (
                        float(pos.get('current_value', pos.get('currentValue', 0))) / shares
                        if shares else 0
                    )
                )
                current_value = float(
                    pos.get('value')
                    or pos.get('currentValue')
                    or (current_price * shares)
                )
                unrealized = float(pos.get('pnl') or pos.get('cashPnl') or 0)
                realized = float(pos.get('realized_pnl') or pos.get('realizedPnl') or 0)

                market_title = (
                    pos.get('market', {}).get('question')
                    or pos.get('title')
                    or 'Unknown'
                )
                positions.append({
                    'outcome_id': pos.get('asset_id') or pos.get('asset'),
                    'bracket': market_title,
                    'shares': shares,
                    'average_cost': avg_cost,
                    'current_price': current_price,
                    'current_value': current_value,
                    'unrealized_pnl': unrealized,
                    'realized_pnl': realized,
                    'market_id': pos.get('market', {}).get('condition_id') or pos.get('conditionId'),
                    'market_title': market_title,
                })

            logger.info(f"✓ Retrieved {len(positions)} open positions")
            return positions

        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def create_market_buy_order(
        self,
        token_id: str,
        amount_usd: float,
        price: float = None,
        slippage_tolerance: float = 0.05,
    ) -> Optional[Dict]:
        """
        Create a market buy order for a specific outcome token.

        Args:
            token_id: Token ID (outcome ID from Polymarket)
            amount_usd: Amount in USD to spend
            price: Expected price (for slippage check), if None uses current market price
            slippage_tolerance: Maximum allowed slippage (default 5%)

        Returns:
            Order result dictionary or None if failed
        """
        try:
            # Check position size limits
            if amount_usd > self.max_position_size:
                logger.warning(f"Position size ${amount_usd} exceeds max ${self.max_position_size}")
                amount_usd = self.max_position_size

            # Get current market price if not provided
            if price is None:
                market_data = self.client.get_market(token_id)
                price = float(market_data.get('price', 0))

            if price <= 0:
                logger.error(f"Invalid price {price} for token {token_id}")
                return None

            # Calculate shares to buy
            shares = int(amount_usd / price)

            if shares == 0:
                logger.warning(f"Amount ${amount_usd} too small to buy shares at price ${price}")
                return None

            # Use LIMIT ORDER (GTC) instead of market order
            # Set price at recommendation price (with small slippage for fills)
            limit_price = price * (1 + slippage_tolerance)  # Slightly above market for better fill

            logger.info(f"Creating LIMIT buy order: {shares} shares of {token_id}")
            logger.info(f"  Limit price: ${limit_price:.4f} (total: ${amount_usd:.2f})")

            # Create and sign the order
            order_args = OrderArgs(
                price=float(limit_price),
                size=float(shares),
                side=BUY,
                token_id=str(token_id),
            )

            signed_order = self.client.create_order(order_args)

            # Post as GTC (Good-Till-Cancelled) order
            resp = self.client.post_order(signed_order, OrderType.GTC)

            logger.info(f"✓ Limit order posted: {resp.get('orderID', 'unknown')}")
            # Pre-approve conditional tokens so the position can be sold later
            self.ensure_token_allowance(token_id)
            return resp

        except Exception as e:
            logger.error(f"Failed to create buy order: {e}", exc_info=True)
            return None

    def create_market_sell_order(
        self,
        token_id: str,
        shares: int,
        price: float = None,
        slippage_tolerance: float = 0.05,
    ) -> Optional[Dict]:
        """
        Create a market sell order for a specific outcome token.

        Args:
            token_id: Token ID (outcome ID from Polymarket)
            shares: Number of shares to sell
            price: Expected price (for slippage check), if None uses current market price
            slippage_tolerance: Maximum allowed slippage (default 5%)

        Returns:
            Order result dictionary or None if failed
        """
        try:
            # Get current market price if not provided
            if price is None:
                market_data = self.client.get_market(token_id)
                price = float(market_data.get('price', 0))

            if price <= 0:
                logger.error(f"Invalid price {price} for token {token_id}")
                return None

            if shares == 0:
                logger.warning(f"No shares to sell")
                return None

            # Force re-approve token for sell
            if token_id in self._approved_tokens:
                self._approved_tokens.discard(token_id)
            self.ensure_token_allowance(token_id)
            time.sleep(5)  # let approval settle on-chain before submitting sell

            # Use LIMIT ORDER (GTC) instead of market order
            limit_price = price * (1 - slippage_tolerance)
            tick_size = float(self.client.get_tick_size(token_id))
            limit_price = max(tick_size, round(limit_price / tick_size) * tick_size)

            logger.info(f"Creating LIMIT sell order: {shares} shares of {token_id}")
            logger.info(f"  Limit price: ${limit_price:.4f} (total: ${shares * price:.2f})")

            order_args = OrderArgs(
                price=float(limit_price),
                size=float(shares),
                side=SELL,
                token_id=str(token_id),
            )

            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)

            logger.info(f"✓ Limit sell order posted: {resp.get('orderID', 'unknown')}")
            return resp

        except Exception as e:
            logger.error(f"Failed to create sell order: {e}", exc_info=True)
            return None

    def execute_recommendation(
        self,
        recommendation: Dict,
        dry_run: bool = True,
    ) -> Dict:
        """
        Execute a single trading recommendation (buy or sell).

        Args:
            recommendation: Recommendation dictionary from BotMonitor
            dry_run: If True, don't actually place orders (default True for safety)

        Returns:
            Execution result dictionary
        """
        bracket = recommendation['bracket']
        token_id = recommendation.get('outcome_id')
        amount = recommendation['position_size']
        price = recommendation['market_price']
        shares = recommendation['shares']
        is_sell = recommendation.get('is_sell', False)
        action = recommendation['action']

        side = "SELL" if is_sell else "BUY"
        logger.info(f"{'[DRY RUN] ' if dry_run else ''}{side}: {bracket} - "
                   f"{shares:.0f} shares @ ${price:.4f} (${amount:.2f} value)")

        if dry_run:
            return {
                'success': True,
                'dry_run': True,
                'bracket': bracket,
                'action': action,
                'side': side,
                'amount': amount,
                'shares': shares,
                'message': 'Dry run - no order placed'
            }

        if not token_id:
            logger.error(f"No token_id for bracket {bracket}")
            return {
                'success': False,
                'bracket': bracket,
                'error': 'Missing token_id'
            }

        # Execute the order (buy or sell)
        if is_sell:
            order = self.create_market_sell_order(
                token_id=token_id,
                shares=int(shares),
                price=price,
            )
        else:
            order = self.create_market_buy_order(
                token_id=token_id,
                amount_usd=amount,
                price=price,
            )

        if order:
            return {
                'success': True,
                'bracket': bracket,
                'action': action,
                'side': side,
                'amount': amount,
                'shares': shares,
                'order_id': order.get('id'),
                'message': f'{side} order placed successfully'
            }
        else:
            return {
                'success': False,
                'bracket': bracket,
                'action': action,
                'side': side,
                'error': f'{side} order creation failed'
            }

    def execute_recommendations(
        self,
        recommendations: List[Dict],
        dry_run: bool = True,
        max_total: float = None,
    ) -> Dict:
        """
        Execute multiple trading recommendations with safety checks.

        Args:
            recommendations: List of recommendation dictionaries
            dry_run: If True, don't actually place orders
            max_total: Maximum total USD to spend (uses max_total_exposure if None)

        Returns:
            Execution summary
        """
        if max_total is None:
            max_total = self.max_total_exposure

        # Filter recommendations with position_size > 0
        actionable = [r for r in recommendations if r.get('position_size', 0) > 0]

        # Calculate total allocation
        total_allocation = sum(r['position_size'] for r in actionable)

        # Check limits
        if total_allocation > max_total:
            logger.warning(f"Total allocation ${total_allocation:.2f} exceeds max ${max_total:.2f}")
            # Scale down proportionally
            scale_factor = max_total / total_allocation
            for rec in actionable:
                rec['position_size'] *= scale_factor
            total_allocation = max_total

        logger.info(f"{'[DRY RUN] ' if dry_run else ''}Executing {len(actionable)} orders, "
                   f"total: ${total_allocation:.2f}")

        results = []
        total_spent = 0
        successful = 0
        failed = 0

        for rec in actionable:
            result = self.execute_recommendation(rec, dry_run=dry_run)
            results.append(result)

            if result['success']:
                successful += 1
                total_spent += rec['position_size']
            else:
                failed += 1

        return {
            'dry_run': dry_run,
            'total_orders': len(actionable),
            'successful': successful,
            'failed': failed,
            'total_spent': total_spent,
            'results': results,
        }

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """
        Get status of a specific order.

        Args:
            order_id: Order ID from Polymarket

        Returns:
            Order status dictionary
        """
        try:
            order = self.client.get_order(order_id)
            return order
        except Exception as e:
            logger.error(f"Failed to get order status: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.cancel_order(order_id)
            logger.info(f"✓ Cancelled order {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False

    def ensure_token_allowance(self, token_id: str) -> bool:
        """
        Ensure the CLOB has allowance to transfer a specific conditional token.
        """
        token_id = str(token_id)
        if token_id in self._approved_tokens:
            return True
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=self.signature_type,
            )
            self.client.update_balance_allowance(params)
            logger.info(f"✓ Token allowance approved for {token_id[:20]}...")
            self._approved_tokens.add(token_id)
            return True
        except PolyApiException as api_exc:
            logger.warning(f"Token allowance API call failed for {token_id}: status={api_exc.status_code} {api_exc.error_message if hasattr(api_exc, 'error_message') else ''}")
            if api_exc.status_code in (429, 1015):
                # Rate limited — approval likely already exists
                self._approved_tokens.add(token_id)
                return True
            # For 403 (geoblock) or other errors, do NOT cache — the approval didn't go through
            return False
        except Exception as exc:
            logger.warning(f"Failed to set conditional allowance for {token_id}: {exc}")
            return False
