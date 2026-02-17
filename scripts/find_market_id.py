#!/usr/bin/env python3
"""
Helper script to find Polymarket market IDs from browser network requests.

USAGE:
1. Open the market page in your browser (e.g., https://polymarket.com/event/elon-musk-of-tweets-december-30-january-6)
2. Open browser DevTools (F12) > Network tab
3. Refresh the page
4. Look for requests to "gamma-api.polymarket.com/events/"
5. Click on the request and check the Response tab
6. Find the "id" field - that's your market ID
7. Add the ID to the list below

Alternatively, run this script and it will try to find Elon tweet markets:
    python find_market_id.py
"""

import requests
import sys

def scan_for_elon_markets():
    """Scan Polymarket API for Elon tweet markets."""
    gamma_api = 'https://gamma-api.polymarket.com'

    print("Scanning Polymarket for Elon tweet markets...")
    print("=" * 80)

    found_markets = []

    # Strategy 1: Scan known ID ranges
    print("\n1. Scanning ID ranges 103800-104200...")
    for market_id in range(103800, 104200):
        try:
            response = requests.get(f'{gamma_api}/events/{market_id}', timeout=2)

            if response.ok and response.status_code == 200:
                event = response.json()
                title = event.get('title', '')

                if 'elon' in title.lower() and 'tweet' in title.lower():
                    found_markets.append(event)
                    print(f"\n✓ Found: {title}")
                    print(f"  ID: {event.get('id')}")
                    print(f"  Active: {event.get('active')}")
                    print(f"  Volume: ${event.get('volume', 0):,.0f}")
        except:
            pass

        if market_id % 50 == 0:
            sys.stdout.write('.')
            sys.stdout.flush()

    print("\n\n" + "=" * 80)
    print(f"Found {len(found_markets)} Elon tweet markets\n")

    if found_markets:
        print("Add these IDs to known_market_ids in src/scrapers/polymarket_scraper.py:")
        print("known_market_ids = [")
        for m in found_markets:
            print(f"    {m.get('id')},  # {m.get('title')}")
        print("]")
    else:
        print("No markets found via API scan.")
        print("\nTo manually find market IDs:")
        print("1. Open the market in your browser")
        print("2. Open DevTools (F12) > Network tab")
        print("3. Refresh the page")
        print("4. Look for requests to 'gamma-api.polymarket.com/events/<ID>'")
        print("5. The ID in the URL is your market ID")

if __name__ == '__main__':
    scan_for_elon_markets()
