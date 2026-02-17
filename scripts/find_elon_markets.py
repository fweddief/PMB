#!/usr/bin/env python3
"""
Automatically discover all active Elon Musk tweet markets on Polymarket.
No browser console needed!
"""

import requests
import re
import json
from typing import List, Dict


def find_elon_tweet_markets() -> List[Dict]:
    """
    Scrape Polymarket search page and fetch all Elon tweet market details.

    Returns:
        List of market dictionaries with id, title, url, etc.
    """
    print("🔍 Searching Polymarket for Elon tweet markets...\n")

    # Step 1: Scrape the search page for event URLs
    search_url = "https://polymarket.com/search"
    params = {"q": "elon tweet"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    try:
        response = requests.get(search_url, params=params, headers=headers, timeout=10)
        html = response.text

        # Extract all URLs matching the pattern /event/elon-musk-of-tweets-*
        pattern = r'/event/(elon-musk-of-tweets-[a-z0-9-]+)'
        matches = re.findall(pattern, html)
        slugs = list(set(matches))  # Remove duplicates

        print(f"✓ Found {len(slugs)} unique event URLs\n")

    except Exception as e:
        print(f"❌ Failed to fetch search page: {e}")
        return []

    # Step 2: Fetch details for each market from the API
    markets = []

    for slug in slugs:
        api_url = f"https://gamma-api.polymarket.com/events?slug={slug}"

        try:
            response = requests.get(api_url, timeout=5)

            if response.ok:
                events = response.json()

                if events and len(events) > 0:
                    event = events[0]

                    market_data = {
                        'id': event['id'],
                        'slug': slug,
                        'title': event['title'],
                        'url': f"https://polymarket.com/event/{slug}",
                        'active': event.get('active'),
                        'closed': event.get('closed'),
                        'end_date': event.get('endDate', '')[:10],
                        'start_date': event.get('startDate', '')[:10],
                        'tweet_count': event.get('tweetCount'),
                        'volume': event.get('volume', 0),
                        'liquidity': event.get('liquidity', 0),
                    }

                    markets.append(market_data)

                    # Status indicator
                    if market_data['active'] and not market_data['closed']:
                        status = "🟢 OPEN"
                    else:
                        status = "🔴 CLOSED"

                    print(f"{status} ID {event['id']}: {event['title']}")
                    print(f"   Ends: {market_data['end_date']}, Current tweets: {market_data['tweet_count']}")
                    print(f"   Volume: ${market_data['volume']:,.0f}, Liquidity: ${market_data['liquidity']:,.0f}\n")

        except Exception as e:
            print(f"❌ Failed to fetch {slug}: {e}\n")

    # Sort by end date
    markets.sort(key=lambda x: x['end_date'])

    return markets


def main():
    """Main entry point."""
    markets = find_elon_tweet_markets()

    if not markets:
        print("❌ No markets found")
        return

    print(f"\n{'='*60}")
    print(f"📊 SUMMARY: Found {len(markets)} markets")
    print(f"{'='*60}\n")

    # Count open vs closed
    open_markets = [m for m in markets if m['active'] and not m['closed']]
    closed_markets = [m for m in markets if not m['active'] or m['closed']]

    print(f"🟢 Open for trading: {len(open_markets)}")
    print(f"🔴 Closed: {len(closed_markets)}\n")

    # Generate config for market_config.json
    print("="*60)
    print("📝 Add these to market_config.json:")
    print("="*60)
    print()

    tracked_markets = []
    for m in markets:
        status = "open" if m['active'] and not m['closed'] else "closed"
        tracked_markets.append({
            "id": m['id'],
            "name": m['title'][:50],
            "url": m['url'],
            "ends": m['end_date'],
            "status": status
        })

    config = {"tracked_markets": tracked_markets}
    print(json.dumps(config, indent=2))

    # Also save to a file
    output_file = "discovered_markets.json"
    with open(output_file, 'w') as f:
        json.dump(config, f, indent=2)

    print(f"\n💾 Saved to {output_file}")


if __name__ == "__main__":
    main()
