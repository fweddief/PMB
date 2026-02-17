#!/usr/bin/env python3
"""Debug database to understand why market curve is empty."""
import sys
sys.path.insert(0, 'src')

from database import DatabaseManager, PolymarketMarket, MarketOutcome, PriceSnapshot
from sqlalchemy import desc

db = DatabaseManager()

with db.get_session() as session:
    # Get all markets
    markets = session.query(PolymarketMarket).all()
    print(f"\n=== MARKETS ({len(markets)}) ===")
    for market in markets[:10]:
        print(f"  market_id={market.market_id}, internal_id={market.id}, title={market.title[:50]}")

    # Get all price snapshots
    total_prices = session.query(PriceSnapshot).count()
    print(f"\n=== PRICE SNAPSHOTS ({total_prices}) ===")

    # Group by market_id
    if total_prices > 0:
        latest_prices = session.query(PriceSnapshot).order_by(desc(PriceSnapshot.timestamp)).limit(20).all()
        print("Latest 20 price snapshots:")
        for price in latest_prices:
            print(f"  market_id={price.market_id}, outcome_id={price.outcome_id[:20]}..., price={price.price:.4f}")

        # Check what market_ids exist in price snapshots
        unique_market_ids = session.query(PriceSnapshot.market_id).distinct().all()
        print(f"\nUnique market_ids in PriceSnapshot: {[m[0] for m in unique_market_ids]}")

    # Get all outcomes
    total_outcomes = session.query(MarketOutcome).count()
    print(f"\n=== MARKET OUTCOMES ({total_outcomes}) ===")
    if total_outcomes > 0:
        sample_outcomes = session.query(MarketOutcome).limit(10).all()
        for outcome in sample_outcomes:
            print(f"  market_id={outcome.market_id}, outcome_id={outcome.outcome_id[:20]}..., text={outcome.outcome_text}")

        # Group by market
        unique_outcome_markets = session.query(MarketOutcome.market_id).distinct().all()
        print(f"\nUnique market_ids in MarketOutcome: {[m[0] for m in unique_outcome_markets]}")

    # Test specific market
    if markets:
        test_market = markets[0]
        print(f"\n=== TEST MARKET {test_market.market_id} (internal_id={test_market.id}) ===")

        # Get prices for this market
        prices = session.query(PriceSnapshot).filter_by(market_id=test_market.id).all()
        print(f"Prices for market.id={test_market.id}: {len(prices)}")

        # Get outcomes for this market
        outcomes = session.query(MarketOutcome).filter_by(market_id=test_market.id).all()
        print(f"Outcomes for market.id={test_market.id}: {len(outcomes)}")

        if outcomes and prices:
            print("\nTrying to match outcome_ids with price outcome_ids:")
            for outcome in outcomes[:3]:
                matching_price = None
                for price in prices:
                    if str(price.outcome_id) == str(outcome.outcome_id):
                        matching_price = price
                        break
                print(f"  Outcome {outcome.outcome_text}: outcome_id={outcome.outcome_id[:30]}... {'MATCHED' if matching_price else 'NO MATCH'}")
