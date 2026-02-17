#!/usr/bin/env python3
"""
Fix token IDs in the database by fetching correct CLOB token IDs from Polymarket.
Run this once to update all market outcomes with proper token IDs for trading.
"""
import sys
sys.path.insert(0, 'src')

import logging
from database import DatabaseManager, PolymarketMarket, MarketOutcome
from scrapers import PolymarketScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_token_ids():
    """Update all market outcomes with correct CLOB token IDs."""
    db = DatabaseManager()
    scraper = PolymarketScraper()

    with db.get_session() as session:
        # Get all markets
        markets = session.query(PolymarketMarket).all()

        logger.info(f"Found {len(markets)} markets to update")

        for market in markets:
            try:
                # Fetch market data from Polymarket
                market_data = scraper.get_market_by_id(str(market.market_id))

                if not market_data:
                    logger.warning(f"Could not fetch data for market {market.market_id}")
                    continue

                # Get outcomes (brackets)
                outcomes_data = market_data.get('markets', [])

                # Update each outcome with correct token ID
                for outcome_data in outcomes_data:
                    outcome_text = outcome_data.get('groupItemTitle', '')

                    # Get the CLOB token ID
                    clob_token_ids_str = outcome_data.get('clobTokenIds', '[]')
                    try:
                        clob_token_ids = eval(clob_token_ids_str)
                        token_id = clob_token_ids[0] if clob_token_ids else outcome_data.get('id')
                    except:
                        token_id = outcome_data.get('id')

                    # Find and update the outcome in database
                    outcome = session.query(MarketOutcome).filter_by(
                        market_id=market.id,
                        outcome_text=outcome_text
                    ).first()

                    if outcome:
                        old_id = outcome.outcome_id
                        outcome.outcome_id = str(token_id)
                        logger.info(f"  Updated {outcome_text}: {old_id} -> {token_id}")

                session.commit()
                logger.info(f"✓ Updated market {market.market_id}: {market.title}")

            except Exception as e:
                logger.error(f"Error updating market {market.market_id}: {e}")
                session.rollback()
                continue

        logger.info("✓ Token ID migration complete!")

if __name__ == "__main__":
    print("Fixing token IDs in database...")
    fix_token_ids()
