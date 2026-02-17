"""
Scraper for Polymarket API to collect market data, prices, and order book information.

Uses the official Polymarket CLOB and Gamma APIs.
"""

import logging
import requests
import json
import os
import re
from datetime import datetime
from typing import Optional, Dict, List
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# Load environment variables from .env file
load_dotenv()


logger = logging.getLogger(__name__)


class PolymarketScraper:
    """
    Scrapes Polymarket market data using official APIs.
    """

    def __init__(self, api_key: str = None):
        """
        Initialize Polymarket scraper.

        Args:
            api_key: Polymarket Gamma API key (for read-only market data)
        """
        self.api_key = api_key

        # Initialize CLOB client (for order books and trading)
        # CLOB client needs your wallet private key, not the Gamma API key
        self.clob_client = None  # Default to None

        try:
            private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
            if not private_key:
                # Read-only mode - no order book access
                logger.warning("⚠ POLYMARKET_PRIVATE_KEY not found in environment - CLOB client disabled")
                logger.warning("  Order books and trading will NOT work without this key")
            else:
                # Remove 0x prefix if present
                if private_key.startswith("0x"):
                    private_key = private_key[2:]

                logger.info("Initializing CLOB client with private key...")

                signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))
                funder_override = os.getenv("POLYMARKET_FUNDER_ADDRESS")

                # Get funder address from private key for GNOSIS_SAFE signature type
                from py_clob_client.signer import Signer
                signer = Signer(private_key, POLYGON)
                funder_address = funder_override or signer.address()

                self.clob_client = ClobClient(
                    host="https://clob.polymarket.com",
                    key=private_key,  # This is your wallet private key
                    chain_id=POLYGON,
                    signature_type=signature_type,
                    funder=funder_address,  # Your EOA address that pays gas
                )
                logger.info(f"✓ CLOB client initialized (sig_type={signature_type}, funder: {funder_address})")
        except Exception as e:
            logger.error(f"✗ Failed to initialize CLOB client: {e}")
            logger.error(f"  Error type: {type(e).__name__}")
            logger.error(f"  POLYMARKET_PRIVATE_KEY exists: {bool(os.getenv('POLYMARKET_PRIVATE_KEY'))}")
            self.clob_client = None

        # Gamma API endpoint (public, no auth required)
        self.gamma_api_url = os.getenv("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")

        # Config file path
        self.config_path = os.path.join(os.path.dirname(__file__), '../../market_config.json')

        # Load tracked market IDs from config file
        self.known_market_ids = self._load_market_config()

        # Track last discovery time to avoid too frequent checks
        self.last_discovery_time = None

    def _load_market_config(self) -> List[str]:
        """
        Load tracked market IDs from market_config.json file.

        Returns:
            List of market IDs to track
        """
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)

            # Extract IDs from tracked_markets
            market_ids = []
            for market in config.get('tracked_markets', []):
                market_id = market.get('id')
                if market_id and market_id != 'PASTE_ID_HERE':
                    market_ids.append(str(market_id))
                    logger.info(f"Tracking market {market_id}: {market.get('name', 'Unknown')}")

            if not market_ids:
                logger.warning("No market IDs found in market_config.json - will auto-discover")
                return []

            return market_ids

        except FileNotFoundError:
            logger.warning(f"Config file not found at {self.config_path}, will auto-discover")
            return []
        except Exception as e:
            logger.error(f"Error loading market config: {e}")
            return []

    def search_markets(self, query: str = "Elon Musk tweets", active_only: bool = False) -> List[Dict]:
        """
        Search for markets (events) matching a query.

        Args:
            query: Search query string

        Returns:
            List of event dictionaries
        """
        logger.info(f"Searching for markets: {query}")

        try:
            from datetime import datetime, timedelta
            import calendar

            found_markets = []
            today = datetime.utcnow()

            # Strategy 0: Use Gamma API search with status filter for bulk results
            try:
                params = {
                    "limit": 200,
                    "search": query,
                    "status": "active" if active_only else None,
                }
                params = {k: v for k, v in params.items() if v is not None}
                response = requests.get(f"{self.gamma_api_url}/events", params=params, timeout=10)
                if response.ok:
                    events = response.json()
                    for event in events:
                        title = event.get('title', '').lower()
                        if 'elon' in query.lower():
                            if 'elon' not in title or 'tweet' not in title:
                                continue
                        if active_only and not event.get('active', True):
                            continue
                        found_markets.append(event)
                    if found_markets:
                        logger.info(f"Gamma API returned {len(found_markets)} events for '{query}'")
            except Exception as e:
                logger.warning(f"Direct search failed: {e}")

            # Strategy 1: Try date-specific slugs for multiple weeks (fallback)
            if not found_markets:
                for week_offset in range(-1, 3):
                    days_since_monday = today.weekday()
                    monday = today - timedelta(days=days_since_monday) + timedelta(weeks=week_offset)
                    sunday = monday + timedelta(days=6)

                    start_month = calendar.month_name[monday.month].lower()
                    end_month = calendar.month_name[sunday.month].lower()

                    slug_patterns = [
                        f"elon-musk-of-tweets-{start_month}-{monday.day}-{end_month}-{sunday.day}",
                        f"elon-musk-tweets-{start_month}-{monday.day}-{end_month}-{sunday.day}",
                        f"elon-musk-number-of-tweets-{start_month}-{monday.day}-{end_month}-{sunday.day}",
                    ]

                    for slug in slug_patterns:
                        try:
                            response = requests.get(f"{self.gamma_api_url}/events/{slug}", timeout=5)
                            if response.ok:
                                event = response.json()
                                title = event.get('title', '').lower()
                                if 'elon' in title and 'tweet' in title:
                                    found_markets.append(event)
                                    logger.info(f"Found market: {event.get('title')}")
                        except:
                            continue

            # Strategy 2: Try known IDs from config (still fallback)
            for market_id in self.known_market_ids:
                try:
                    response = requests.get(f"{self.gamma_api_url}/events/{market_id}", timeout=5)
                    if response.ok:
                        event = response.json()
                        title = event.get('title', '').lower()
                        if 'elon' in title and 'tweet' in title and not any(m.get('id') == event.get('id') for m in found_markets):
                            found_markets.append(event)
                            logger.info(f"Found market by ID {market_id}: {event.get('title')}")
                except:
                    continue

            # Strategy 3: Query all events (limit 200) and filter by name
            try:
                response = requests.get(
                    f"{self.gamma_api_url}/events",
                    params={"limit": 200},
                    timeout=10
                )
                if response.ok:
                    events = response.json()
                    for event in events:
                        title = event.get('title', '').lower()
                        if 'elon' in title and 'tweet' in title:
                            if active_only and not event.get('active', True):
                                continue
                            if not any(m.get('id') == event.get('id') for m in found_markets):
                                found_markets.append(event)
                                logger.info(f"Found market via bulk search: {event.get('title')}")
            except Exception as e:
                logger.warning(f"Bulk search failed: {e}")

            logger.info(f"Found {len(found_markets)} Elon tweet events total")

            return found_markets

        except Exception as e:
            logger.error(f"Failed to search markets: {e}", exc_info=True)
            return []

    def get_market_by_id(self, market_id: str) -> Optional[Dict]:
        """
        Get detailed market/event information by ID.

        Args:
            market_id: Polymarket event ID

        Returns:
            Event data dictionary or None
        """
        logger.info(f"Fetching market: {market_id}")

        try:
            # Try events endpoint first
            response = requests.get(f"{self.gamma_api_url}/events",  params={"id": market_id})
            response.raise_for_status()
            data = response.json()

            # Return first result if it's a list
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return data

        except Exception as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    def get_active_elon_tweet_markets(self) -> List[Dict]:
        """
        Get currently active Elon Musk tweet count markets.

        Returns:
            List of active market dictionaries
        """
        logger.info("Fetching active Elon Musk tweet markets")

        markets = self.search_markets("Elon Musk tweets", active_only=True)

        logger.info(f"Found {len(markets)} active Elon tweet markets")
        return markets

    def get_market_prices(self, market_id: str) -> List[Dict]:
        """
        Get current prices for all outcome brackets in an event.

        Args:
            market_id: Polymarket event ID

        Returns:
            List of price dictionaries for each outcome bracket
        """
        logger.info(f"Fetching prices for market: {market_id}")

        try:
            # Get event data which includes all market brackets
            event_data = self.get_market_by_id(market_id)

            if not event_data:
                return []

            # In the events API, brackets are in the 'markets' array
            markets = event_data.get('markets', [])
            prices = []

            for market in markets:
                # Parse outcome prices from JSON string
                outcome_prices_str = market.get('outcomePrices', '["0.5", "0.5"]')
                try:
                    outcome_prices = eval(outcome_prices_str)  # Convert string to list
                    # Use the "Yes" price (first one) for the bracket
                    price = float(outcome_prices[0]) if outcome_prices else 0.0
                except:
                    price = 0.0

                # Get the CLOB token ID for the "Yes" outcome (first token)
                clob_token_ids_str = market.get('clobTokenIds', '[]')
                try:
                    clob_token_ids = eval(clob_token_ids_str)  # Convert string to list
                    # Use the first token ID which corresponds to "Yes" outcome
                    token_id = clob_token_ids[0] if clob_token_ids else market.get('id')
                except:
                    # Fallback to market ID if clobTokenIds parsing fails
                    token_id = market.get('id')

                prices.append({
                    'outcome_id': token_id,  # Use CLOB token ID instead of market ID
                    'outcome_text': market.get('groupItemTitle', market.get('question', '')),
                    'price': price,
                    'timestamp': datetime.utcnow(),
                })

            logger.info(f"Fetched prices for {len(prices)} outcome brackets")
            return prices

        except Exception as e:
            logger.error(f"Failed to fetch prices: {e}", exc_info=True)
            return []

    def get_order_book(self, token_id: str) -> Optional[Dict]:
        """
        Get order book for a specific outcome token.

        Args:
            token_id: Token ID for the outcome

        Returns:
            Order book data with bids and asks
        """
        if not self.clob_client:
            logger.warning("CLOB client not initialized - cannot fetch order book")
            return None

        try:
            order_book = self.clob_client.get_order_book(token_id)

            # OrderBookSummary has .bids and .asks as attributes (list of OrderSummary objects)
            # Convert to dict format for compatibility
            return {
                'token_id': token_id,
                'bids': [{'price': bid.price, 'size': bid.size} for bid in (order_book.bids if order_book else [])],
                'asks': [{'price': ask.price, 'size': ask.size} for ask in (order_book.asks if order_book else [])],
                'timestamp': datetime.utcnow(),
            }

        except Exception as e:
            # 404 errors are normal - many outcomes don't have order books yet
            error_str = str(e)
            if '404' in error_str or 'No orderbook exists' in error_str:
                logger.debug(f"No order book for token {token_id} (using estimated spread)")
            else:
                logger.error(f"Failed to fetch order book for {token_id}: {e}")
            return None

    def parse_tweet_range_from_outcome(self, outcome_text: str) -> tuple:
        """
        Parse tweet count range from outcome text.

        Examples:
            "Less than 300" -> (None, 300)
            "300-350" -> (300, 350)
            "450-500" -> (450, 500)
            "More than 500" -> (500, None)

        Args:
            outcome_text: Outcome description text

        Returns:
            Tuple of (min_tweets, max_tweets)
        """
        import re

        # Look for patterns like "300-350", "<300", ">500"
        if '-' in outcome_text:
            # Range format: "300-350"
            numbers = re.findall(r'\d+', outcome_text)
            if len(numbers) >= 2:
                return int(numbers[0]), int(numbers[1])

        elif 'less' in outcome_text.lower() or '<' in outcome_text:
            # Less than format
            numbers = re.findall(r'\d+', outcome_text)
            if numbers:
                return None, int(numbers[0])

        elif 'more' in outcome_text.lower() or '>' in outcome_text:
            # More than format
            numbers = re.findall(r'\d+', outcome_text)
            if numbers:
                return int(numbers[0]), None

        # Try to extract any numbers
        numbers = re.findall(r'\d+', outcome_text)
        if numbers:
            return int(numbers[0]), int(numbers[0])

        return None, None

    def get_market_snapshot(self, market_id: str) -> Optional[Dict]:
        """
        Get a complete snapshot of a market including prices and metadata.

        Args:
            market_id: Polymarket market ID

        Returns:
            Complete market snapshot dictionary
        """
        logger.info(f"Getting market snapshot: {market_id}")

        market_data = self.get_market_by_id(market_id)
        if not market_data:
            return None

        prices = self.get_market_prices(market_id)

        return {
            'market_id': market_id,
            'title': market_data.get('question', ''),
            'description': market_data.get('description', ''),
            'outcomes': market_data.get('outcomes', []),
            'prices': prices,
            'is_active': market_data.get('active', True),
            'end_date': market_data.get('end_date_iso'),
            'volume': market_data.get('volume', 0),
            'liquidity': market_data.get('liquidity', 0),
            'timestamp': datetime.utcnow(),
        }

    def get_live_tweet_count(self) -> Optional[dict]:
        """
        Get live tweet count directly from Polymarket API.
        This is faster and more reliable than scraping xtracker.

        Returns:
            Dictionary with tweet data or None if failed
        """
        logger.info("Fetching live tweet count from Polymarket API...")

        try:
            # Get the active Elon tweet market
            markets = self.get_active_elon_tweet_markets()

            if not markets:
                logger.error("No active Elon tweet markets found")
                return None

            # Get the first active market
            market = markets[0]

            # Extract live tweet count from the event data
            tweet_count = market.get('tweetCount')

            if tweet_count is None:
                logger.warning("tweetCount field not found in market data")
                return None

            return {
                'timestamp': datetime.utcnow(),
                'cumulative_count': tweet_count,  # This is already the week count from Polymarket
                'source': 'polymarket_api',
            }

        except Exception as e:
            logger.error(f"Failed to get live tweet count from Polymarket: {e}", exc_info=True)
            return None

    def get_tweet_count_for_market(self, market_id: str) -> Optional[int]:
        """
        Get the current tweet count for a specific market.

        Args:
            market_id: Polymarket market/event ID

        Returns:
            Tweet count for that market's time period, or None if failed
        """
        try:
            market_data = self.get_market_by_id(market_id)
            if not market_data:
                return None

            tweet_count = market_data.get('tweetCount')
            return tweet_count if tweet_count is not None else None

        except Exception as e:
            logger.error(f"Failed to get tweet count for market {market_id}: {e}")
            return None

    def discover_new_markets(self, force: bool = False) -> List[Dict]:
        """
        Automatically discover all active Elon Musk tweet markets from Polymarket.

        This scrapes the Polymarket search page and fetches market details from the API.
        New markets are automatically added to the config file.

        Args:
            force: If True, run discovery even if recently checked

        Returns:
            List of newly discovered market dictionaries
        """
        from datetime import timedelta

        # Don't check too frequently (once per hour unless forced)
        if not force and self.last_discovery_time:
            time_since_last = datetime.utcnow() - self.last_discovery_time
            if time_since_last < timedelta(hours=1):
                logger.info(f"Skipping market discovery - last checked {time_since_last.total_seconds()/60:.1f} minutes ago")
                return []

        logger.info("🔍 Auto-discovering Elon Musk tweet markets...")

        try:
            # Step 1: Query Gamma events API directly for active Elon markets
            params = {
                "search": "Elon tweets",
                "status": "active",
                "limit": 200,
            }
            response = requests.get(f"{self.gamma_api_url}/events", params=params, timeout=10)
            response.raise_for_status()
            events = response.json()

            logger.info(f"Gamma returned {len(events)} events for Elon tweets")

            if not events:
                logger.warning("Gamma returned no Elon tweet markets")
                return []

            # Step 2: Fetch market details for each slug
            discovered_markets = []

            for event in events:
                title = event.get('title', '').lower()
                if 'elon' not in title or 'tweet' not in title:
                    continue

                slug = event.get('slug') or event.get('marketSlug') or ''
                discovered_markets.append({
                    'id': event.get('id'),
                    'slug': slug,
                    'title': event.get('title'),
                    'url': f"https://polymarket.com/event/{slug}" if slug else event.get('url'),
                    'active': event.get('active'),
                    'closed': event.get('closed'),
                    'end_date': event.get('endDate', '')[:10],
                    'start_date': event.get('startDate', '')[:10],
                    'tweet_count': event.get('tweetCount'),
                })

            # Step 3: Update config file with new markets
            if discovered_markets:
                self._update_market_config(discovered_markets)

            self.last_discovery_time = datetime.utcnow()

            logger.info(f"✓ Discovered {len(discovered_markets)} markets")
            return discovered_markets

        except Exception as e:
            logger.error(f"Market discovery failed: {e}", exc_info=True)
            return []

    def _update_market_config(self, discovered_markets: List[Dict]) -> None:
        """
        Update market_config.json with newly discovered markets.

        Args:
            discovered_markets: List of market dictionaries from discovery
        """
        try:
            # Load existing config
            try:
                with open(self.config_path, 'r') as f:
                    config = json.load(f)
            except FileNotFoundError:
                config = {
                    "comment": "Auto-updated by Polymarket bot",
                    "instructions": "Markets are automatically discovered - no manual updates needed",
                    "tracked_markets": []
                }

            # Get existing market IDs
            existing_ids = set(m.get('id') for m in config.get('tracked_markets', []))

            # Add new markets that aren't already tracked
            new_markets_added = 0

            for market in discovered_markets:
                market_id = market['id']

                if market_id not in existing_ids:
                    # Add new market
                    config['tracked_markets'].append({
                        'id': market_id,
                        'name': market['title'],
                        'url': market['url'],
                        'ends': market['end_date'],
                        'status': 'open' if market['active'] and not market['closed'] else 'closed',
                        'discovered_at': datetime.utcnow().isoformat()
                    })

                    logger.info(f"➕ Added new market {market_id}: {market['title'][:50]}...")
                    new_markets_added += 1
                    existing_ids.add(market_id)

            # Save updated config
            if new_markets_added > 0:
                with open(self.config_path, 'w') as f:
                    json.dump(config, f, indent=2)

                # Reload market IDs
                self.known_market_ids = self._load_market_config()

                logger.info(f"✓ Config updated: {new_markets_added} new markets added")
            else:
                logger.info("No new markets to add - all markets already tracked")

        except Exception as e:
            logger.error(f"Failed to update market config: {e}", exc_info=True)
