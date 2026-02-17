# Polymarket Automated Trading Bot

An intelligent trading bot for Polymarket prediction markets focused on Elon Musk weekly tweet frequency markets. Uses velocity-based predictions, Kelly Criterion position sizing, and automated execution.

**Current Status:** Fully Automated Trading ✅

## Features

### 🚀 Data Collection (Fast & Reliable)
- ✅ **Live tweet count from Polymarket API** (official source, no scraping!)
- ✅ **Real-time price tracking** from Polymarket Gamma API
- ✅ **1-minute intervals** (configurable 1-10 minutes)
- ✅ **Automatic fallback** to xtracker.io if API fails
- ✅ **SQLite database** for historical data

### 🧠 Prediction Model
- ✅ **Velocity-based forecasting** (tweets/day calculation)
- ✅ **Normal distribution probability** model
- ✅ **Confidence intervals** based on remaining time
- ✅ **Bell curve visualization** of market vs bot probabilities

### 💰 Trading Strategy
- ✅ **Kelly Criterion position sizing** (quarter Kelly for safety)
- ✅ **Time-based capital deployment** (20% early → 90% late week)
- ✅ **Ladder arbitrage** across multiple brackets
- ✅ **Buy/sell arbitrage** (take profits when overvalued)
- ✅ **Floor share strategy** (lottery tickets early week only)
- ✅ **Stop-loss automation** (exit impossible positions)

### 🤖 Automated Trading
- ✅ **Real-time execution** every 1-10 minutes
- ✅ **Minimum edge filter** (only trade when edge > 3%)
- ✅ **Smart sell logic** (hold winners, cut losers)
- ✅ **Paper trading** with full portfolio tracking
- ✅ **Performance monitoring** with P&L tracking

## Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your Polymarket API credentials
# Optional: Adjust SCRAPER_INTERVAL_MINUTES (1-10 recommended)
```

### 3. Initialize Database

```bash
python scripts/manage.py init
```

### 4. Start Auto-Trading

```bash
# Start automated trading with 1-minute updates
python scripts/manage.py schedule --auto-trade
```

The bot will:
- ✅ Collect live tweet counts every minute
- ✅ Update market prices every minute
- ✅ Calculate recommendations with Kelly sizing
- ✅ Execute trades when edge > 3%
- ✅ Automatically sell losing positions (stop-loss)
- ✅ Hold winning positions until payout

## Usage

### Monitor Performance

```bash
python scripts/monitor_cli.py
```

Shows:
- Current tweet count and velocity
- Predicted final count with confidence interval
- Market bell curve vs bot probabilities
- Trading recommendations with Kelly sizing
- Buy/sell signals with timing

### View Paper Trading Results

```bash
python scripts/paper_trade_cli.py --performance
```

Shows:
- Portfolio value and P&L
- Open positions with unrealized gains/losses
- Trade history with realized P&L
- Performance over time

### Manual Data Collection

```bash
# Collect data once
python scripts/manage.py collect
```

### Test Components

```bash
# Test Polymarket API data collection
python scripts/manage.py test-polymarket

# Test xtracker scraper (fallback)
python scripts/manage.py test-scraper
```

## Configuration

### .env Settings

```bash
# Polymarket API (get from https://polymarket.com)
POLYMARKET_API_KEY=your_api_key
POLYMARKET_SECRET=your_secret
POLYMARKET_PASSPHRASE=your_passphrase
POLYMARKET_PRIVATE_KEY=your_private_key

# Data Collection Speed
SCRAPER_INTERVAL_MINUTES=1  # 1-2 min = fast, 5-10 min = conservative

# Paper Trading Defaults
PAPER_TRADING_START_BALANCE=1000

# Trading Thresholds
MIN_EDGE_FOR_TRADE=3.0      # Only trade when edge > 3%
MIN_KELLY_FOR_TRADE=0.05    # Minimum Kelly fraction (5% of max)
```

### Recommended Intervals by Market Phase

| Week Phase | Interval | Rationale |
|------------|----------|-----------|
| Early (0-30%) | 5-10 min | Slow price changes, lottery tickets only |
| Mid (30-70%) | 2-5 min | Increasing activity, scaling positions |
| Late (70-100%) | 1-2 min | Fast price changes, final positioning |

## Trading Strategy Details

### Prediction Model

1. **Calculate Velocity**
   - Daily rate = tweets_this_week / days_elapsed
   - Accounts for day/night cycles (uses daily, not hourly)

2. **Project Total**
   - Predicted = current + (daily_rate × days_remaining)
   - Confidence interval narrows as week progresses

3. **Probability Distribution**
   - Normal distribution centered on prediction
   - CDF integration over bracket ranges
   - Tighter std dev late in week (more certain)

### Position Sizing (Kelly Criterion)

```
Kelly Fraction = (our_prob - market_price) / (1 - market_price)
Position Size = Kelly × Bankroll × Capital_Deployment_Pct
```

- **Quarter Kelly** (max 25%) for safety
- **Capital deployment** scales 20% → 90% through week
- **Floor shares** get fixed $10 allocation early week

### Buy/Sell Logic

**BUY when:**
- Edge > 3% (our prob > market price)
- Kelly > 5% of max Kelly
- Timing = NOW or EARLY (based on week progress)

**SELL when:**
- **STOP LOSS**: Position is mathematically impossible
- **Profit taking**: Price > $0.75 on likely winner
- **Overvalued**: Market price >> our probability (late week only)

**HOLD when:**
- Position is likely winner (prediction in bracket range)
- Week progress < 90% (still time to adjust)
- Price < $0.75 (room to grow to $1.00)

### Risk Management

## Always-On Deployment

The repo includes a Docker image and `docker-compose.yml` so you can deploy the bot to Render/Fly/EC2 and keep it running even when your computer is off.

```bash
# Build & run locally
docker compose up --build

# Or run FastAPI manually (for dashboard + background jobs)
uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

Mount the `data/` and `logs/` directories (already configured in `docker-compose.yml`) so SQLite state persists between deploys. Set `TRADING_MODE=live` and `AUTO_TRADE=true` in `.env` when you're ready for real execution.

### Data Retention

All tweet snapshots are written to `data/polymarket_bot.db` and mirrored into CSV archives under `data/history/`. Keep the `data/` directory intact (or mount it as a persistent volume on Railway/Docker) and the bot will retain every historical reading across restarts so long-horizon models can keep learning.

### Deploying on Railway

Railway can run the same container image:

1. Install the [Railway CLI](https://docs.railway.app/develop/cli) and run `railway login`.
2. From the repo root run `railway init` (or `railway link` to an existing project) and select **Dockerfile** when prompted. Railway automatically builds from `Dockerfile`.
3. Configure environment variables via the Railway dashboard or `railway variables set`:
   - `POLYMARKET_API_KEY`, `POLYMARKET_SECRET`, `POLYMARKET_PASSPHRASE`, `POLYMARKET_PRIVATE_KEY`
   - `TRADING_MODE=paper` (switch to `live` when comfortable)
   - `AUTO_TRADE=true` if you want trades submitted automatically
   - Any additional risk/env settings from `.env.example`
4. Add a persistent volume for `/app/data` so the SQLite DB survives redeploys:
   ```bash
   railway volume create polymarket-data --size 1GiB
   railway volume mount polymarket-data:/app/data
   ```
5. Deploy with `railway up`. Railway exposes port `8000` which surfaces the FastAPI endpoints (`/health`, `/status`, `/recommendations`) for your dashboard or monitoring stack.

Whenever you push changes, run `railway up` again to rebuild/redeploy. Use Railway's logs tab or `railway logs` to watch the scheduler output in real time.

## Next.js Dashboard (frontend/)

A lightweight monitoring UI lives in `dashboard/` using Next.js, Tailwind, and Recharts. It calls the FastAPI endpoints to render status, velocity, bell curve, trading recommendations, account balances, and open positions. You can also start/stop auto-trading from the UI (it flips the `/settings/auto-trading` flag behind the scenes).

```bash
cd dashboard
npm install
NEXT_PUBLIC_BOT_API_URL=http://localhost:8000 npm run dev
```

Deploy the dashboard anywhere you can host static Node apps (Vercel, Railway, Netlify). Set `NEXT_PUBLIC_BOT_API_URL` to the public URL of the API container (e.g., your Railway domain) so the widgets can reach `/status`, `/prediction`, `/recommendations`, `/portfolio`, and `/settings`.

## Architecture Overview

1. **Data ingestion & velocity calculation** – A background worker (scheduler or FastAPI runtime) fetches live tweet data from `https://xtracker.polymarket.com/user/elonmusk` and the Polymarket Gamma API, storing raw timestamps and rolling 1h/6h/24h velocities plus acceleration inside SQLite. This unlocks the time-series insight needed for velocity-based modeling.
2. **Probabilistic modeling** – The `TweetDistributionModel` fits a Normal distribution using `scipy.stats` where `μ = current_count + velocity * remaining_hours` and `σ` scales dynamically with historical volatility and tweet-storm detection. Market bins are priced by integrating CDF mass per Polymarket bracket.
3. **Trading engines** – `services.trading_engine.TradingEngine` applies Kelly sizing with capital deployment curves and routes orders either to the simulated paper ledger or the real `py-clob-client` trader (live mode) with stop-losses and exposure caps.
4. **Live dashboard / API** – `src/api/app.py` exposes FastAPI endpoints (`/status`, `/prediction`, `/recommendations`, `/portfolio`, `/settings`) that a Next.js dashboard can consume for bell-curve visualizations, velocity graphs, live market feeds, portfolio state, and the auto-trading toggle.

This mirrors the implementation plan shared in the design screenshots and keeps the bot cloud-friendly so it can run even when your laptop is off.

1. **No 0-120 brackets** - Never happens historically
2. **Stop-loss automatic** - Exit impossible positions immediately
3. **No forced trades** - Only execute when real edge exists
4. **Time-based deployment** - Don't deploy all capital early
5. **Lottery tickets early only** - Stop buying long-shots after 30% week

## Project Structure

```
polymarket/
├── scripts/
│   ├── manage.py                   # CLI entry point
│   ├── monitor_cli.py              # Terminal dashboard
│   ├── paper_trade_cli.py          # Manual paper trading
│   └── auto_paper_trade.py         # Legacy helper
├── dashboard/                      # Next.js dashboard for bell curves & monitoring
├── requirements.txt
├── Dockerfile / docker-compose.yml # Always-on deployment
├── .env                            # Configuration
├── src/
│   ├── api/app.py                  # FastAPI dashboard service
│   ├── services/
│   │   ├── runtime.py              # Scheduler/orchestrator
│   │   ├── trading_engine.py       # Paper + live execution
│   │   ├── velocity.py             # Rolling velocity + acceleration
│   │   └── settings.py             # Persistent runtime settings (auto-trade toggle)
│   ├── database/
│   │   ├── schema.py               # Main tables (markets, prices, tweets)
│   │   └── paper_trading_schema.py # Paper trading tables
│   ├── scrapers/
│   │   ├── polymarket_scraper.py   # Polymarket API (primary)
│   │   └── xtracker_scraper.py     # xtracker HTTP fallback
│   ├── collectors/
│   │   └── data_collector.py       # Orchestrates data collection
│   ├── analysis/
│   │   └── monitor.py              # Predictions and recommendations
│   └── trading/
│       └── paper_trader.py         # Paper trading engine
└── data/
    └── polymarket_bot.db           # SQLite database
```

## Key Improvements from Original Design

### ✅ Polymarket API Integration
- **Before**: Slow browser automation with Playwright
- **After**: Fast API calls (0.5s vs 8s)
- **Impact**: 16x faster data collection, can run every minute

### ✅ Stop-Loss Automation
- **Before**: Held impossible positions until $0
- **After**: Automatically sells when position becomes impossible
- **Impact**: Recover capital instead of losing 100%

### ✅ Smart Sell Logic
- **Before**: Only bought positions
- **After**: Sells when overvalued, holds likely winners
- **Impact**: Profit taking + better capital rotation

### ✅ Proper Probability Calculation
- **Before**: Crude approximation
- **After**: Normal distribution CDF integration
- **Impact**: Accurate probabilities for each bracket

### ✅ Tighter Confidence Late Week
- **Before**: Same uncertainty throughout week
- **After**: Confidence based on remaining time only
- **Impact**: Better late-week positioning

## Example Session

```bash
# Start auto-trading
$ python scripts/manage.py schedule --auto-trade

# In another terminal, monitor performance
$ python scripts/monitor_cli.py

======================================================================
  BOT PREDICTION (Using Daily Velocity)
======================================================================

Week Progress: 93.1% (6.5 days elapsed)
Current Count: 333 tweets this week
Daily Velocity: 51.1 tweets/day
Days Remaining: 0.4 days

📊 PREDICTED TOTAL: 352 tweets
   Confidence Range: 348 - 356
   Confidence: ±1.2% (narrows as week progresses)
   Week Ends: 2025-12-23 12:00:00

======================================================================
  TRADING RECOMMENDATIONS
======================================================================

💰 Capital Deployment: $851.70 (85.2% of $1,000)

📊 CORE POSITIONS (Kelly-Sized)
Bracket      Action        Edge %    Kelly %     Size $
------------------------------------------------------
340-359      🟢 STRONG BUY  +50.1%    20.0%      $200.42
320-339      🔴 SELL        -3.3%     --         $7.00 (exit)
400-579      🛑 STOP LOSS   --        --         $19.50 (impossible)

✓ Executed 1 BUY: 194 shares of 340-359 @ $0.4835
✓ Executed 8 SELLS: Cleaned up impossible positions
```

## Troubleshooting

### Auto-Trader Not Executing Trades

**Check minimum thresholds:**
```bash
# In .env, lower if needed:
MIN_EDGE_FOR_TRADE=3.0  # Try 2.0 for more trades
MIN_KELLY_FOR_TRADE=0.05  # Try 0.03 for smaller positions
```

### Data Collection Errors

**Polymarket API failing:**
- Check API credentials in .env
- Verify network connection
- Bot will auto-fallback to xtracker scraping

**Rate limiting:**
- Increase SCRAPER_INTERVAL_MINUTES
- 1-2 minutes is safe for Polymarket API

### Wrong Tweet Count

**If bot shows different count than Polymarket:**
1. Check market dates match (Dec 16-23 etc)
2. Run `python scripts/manage.py collect` to refresh
3. Bot uses Polymarket's official tweetCount field

## Performance Tips

1. **Start early in week** - More opportunities, cheaper positions
2. **Use 1-2 min intervals** - Catch price changes fast
3. **Let stop-loss work** - Don't hold impossible positions
4. **Trust the model late week** - Predictions are very accurate with <1 day left
5. **Monitor but don't override** - Bot is faster than manual trading

## Database Queries

```sql
-- View recent trades
SELECT * FROM paper_trades ORDER BY timestamp DESC LIMIT 10;

-- Current portfolio value
SELECT SUM(shares * current_price) FROM paper_positions;

-- P&L by bracket
SELECT bracket, SUM(realized_pnl) as pnl
FROM paper_trades
WHERE side = 'SELL'
GROUP BY bracket;

-- Tweet velocity over time
SELECT timestamp, tweets_per_day
FROM tweet_data
ORDER BY timestamp DESC
LIMIT 20;
```

## Resources

- [Polymarket Documentation](https://docs.polymarket.com/)
- [Polymarket API](https://gamma-api.polymarket.com)
- [Kelly Criterion Explained](https://en.wikipedia.org/wiki/Kelly_criterion)
- [xtracker.io](https://xtracker.polymarket.com) (official tweet counter)

## Disclaimer

This is educational/research software for paper trading only.

**Before live trading:**
- Test thoroughly with paper trading
- Start with very small amounts ($10-50)
- Understand Kelly Criterion and position sizing
- Read Polymarket Terms of Service
- Be aware of market risks and potential losses

The bot makes no guarantees of profitability. Prediction markets involve risk of total loss.

## License

MIT License - Use at your own risk
