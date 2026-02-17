#!/usr/bin/env python3
"""
Simple script to add a market ID to the bot's tracking list.

USAGE:
1. Open the market page in your browser (e.g., https://polymarket.com/event/elon-musk-of-tweets-december-30-january-6)
2. Press F12 to open DevTools
3. Go to the Console tab
4. Paste this command and press Enter:

   window.__NEXT_DATA__?.props?.pageProps?.event?.id

5. Copy the number it shows (e.g., "104567")
6. Run this script:

   python add_market.py 104567 "Dec 30-Jan 6"

The market will be automatically added to market_config.json and the bot will start tracking it!
"""

import json
import sys
from pathlib import Path
import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / 'src'))

def add_market_to_config(market_id: str, name: str = ""):
    """Add a market ID to the tracking config."""

    config_path = 'market_config.json'

    # Load existing config
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: {config_path} not found!")
        return False

    # Verify the market ID exists via API
    print(f"Verifying market ID {market_id}...")
    try:
        response = requests.get(f'https://gamma-api.polymarket.com/events/{market_id}', timeout=10)

        if response.ok:
            event = response.json()
            title = event.get('title', 'Unknown')
            volume = event.get('volume', 0)
            active = event.get('active', False)

            print(f"\n✓ Market found!")
            print(f"  Title: {title}")
            print(f"  Volume: ${volume:,.0f}")
            print(f"  Active: {active}")

            if not name:
                name = title

        else:
            print(f"\n⚠ Warning: Could not verify market ID via API")
            print(f"  Status: {response.status_code}")
            print(f"  Adding anyway (you can remove it later if it doesn't work)")

            if not name:
                name = "Unknown Market"

            title = name

    except Exception as e:
        print(f"\n⚠ Warning: API check failed: {e}")
        print(f"  Adding anyway")

        if not name:
            name = "Unknown Market"

        title = name

    # Check if already in config
    for market in config.get('tracked_markets', []):
        if market.get('id') == str(market_id):
            print(f"\n⚠ Market ID {market_id} is already being tracked!")
            return False

    # Add to tracked markets
    config['tracked_markets'].append({
        'id': str(market_id),
        'name': name,
        'note': title
    })

    # Save config
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    print(f"\n✓ Added market {market_id} to config!")
    print(f"\nCurrent tracked markets:")
    for market in config['tracked_markets']:
        print(f"  - {market['id']}: {market['name']}")

    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nQuick instructions:")
        print("1. Open market in browser, press F12, go to Console tab")
        print("2. Run: window.__NEXT_DATA__?.props?.pageProps?.event?.id")
        print("3. Copy the ID number")
        print("4. Run: python add_market.py <ID> '<optional name>'")
        print("\nExample:")
        print("  python add_market.py 104567 'Dec 30-Jan 6'")
        sys.exit(1)

    market_id = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else ""

    if add_market_to_config(market_id, name):
        print("\n✓ Done! The bot will now track this market.")
        print("  Restart the bot to start collecting data: python main.py schedule --auto-trade")
