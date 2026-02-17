"""FastAPI server exposing bot metrics for the live dashboard."""

from __future__ import annotations

import os
import logging
from functools import lru_cache
from typing import Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from services.runtime import BotRuntime
from services.trading_engine import TradingMode
from services.settings import BotSettingsService
from trading.paper_trader import PaperTradingTrader
from database import DatabaseManager


@lru_cache(maxsize=1)
def get_runtime() -> BotRuntime:
    mode = TradingMode(os.getenv("TRADING_MODE", "paper"))
    auto_trade = os.getenv("AUTO_TRADE", "false").lower() == "true"
    runtime = BotRuntime(
        scheduler_type="background",
        trading_mode=mode,
        enable_auto_trading=auto_trade,
    )
    return runtime


app = FastAPI(title="Polymarket Bot API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    runtime = get_runtime()
    runtime.start()


@app.on_event("shutdown")
def shutdown_event():
    runtime = get_runtime()
    runtime.shutdown()


@app.get("/health")
def health() -> Dict:
    return get_runtime().health()


@app.get("/status")
def status() -> Dict:
    return get_runtime().status()


@app.get("/prediction")
def prediction() -> Dict:
    return get_runtime().latest_prediction()


@app.get("/predictions/all")
def all_predictions() -> Dict:
    """Get predictions for all currently active markets."""
    runtime = get_runtime()
    markets = runtime.trading_engine._get_active_markets()

    predictions = []
    for market in markets:
        try:
            pred = runtime.trading_engine.monitor.calculate_prediction(market_id=market["market_id"])
            if pred and not pred.get('error'):
                predictions.append(pred)
                logger.info(f"✓ Generated prediction for market {market['market_id']}")
            else:
                logger.warning(f"⚠️  No prediction for market {market['market_id']}: {pred.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"❌ Error getting prediction for market {market['market_id']}: {e}", exc_info=True)
            continue

    logger.info(f"Returning {len(predictions)} predictions from {len(markets)} active markets")
    return {"predictions": predictions}


@app.get("/recommendations")
def recommendations() -> Dict:
    return get_runtime().recommendations()


class AutoTradingPayload(BaseModel):
    enabled: bool


@app.get("/portfolio")
def portfolio() -> Dict:
    """Get current portfolio (paper or live depending on TRADING_MODE)."""
    runtime = get_runtime()
    trader = runtime.trading_engine.trader

    return {
        "balance": trader.get_balance(),
        "positions": trader.get_open_positions(),
    }


@app.get("/trades/all")
def all_trades() -> Dict:
    """Get all trades (paper or live depending on TRADING_MODE)."""
    runtime = get_runtime()
    mode = runtime.trading_engine.mode
    db = DatabaseManager()

    with db.get_session() as session:
        trade_list = []

        if mode == TradingMode.PAPER:
            # Get paper trades
            from database.paper_trading_schema import PaperTrade, PaperTradingAccount
            from database import PolymarketMarket

            account = session.query(PaperTradingAccount).first()
            if not account:
                return {"trades": []}

            trades = (
                session.query(PaperTrade)
                .filter(PaperTrade.account_id == account.id)
                .order_by(PaperTrade.timestamp.desc())
                .all()
            )

            for trade in trades:
                # Find market by matching outcome_id
                market_title = None
                market = (
                    session.query(PolymarketMarket)
                    .join(PolymarketMarket.outcomes)
                    .filter(PolymarketMarket.outcomes.any(outcome_id=trade.outcome_id))
                    .first()
                )
                if market:
                    market_title = market.title

                trade_list.append({
                    "id": trade.id,
                    "timestamp": trade.timestamp.isoformat(),
                    "market_title": market_title,
                    "bracket": trade.bracket,
                    "side": trade.side,
                    "shares": round(trade.shares, 2),
                    "price": round(trade.price, 4),
                    "total_cost": round(trade.total_cost, 2),
                    "action": trade.action,
                    "edge": round(trade.edge * 100, 2) if trade.edge else None,
                    "realized_pnl": round(trade.realized_pnl, 2) if trade.realized_pnl else None,
                })

        else:  # LIVE mode
            # Get live trades
            from database import Trade, PolymarketMarket, MarketOutcome

            trades = (
                session.query(Trade)
                .order_by(Trade.timestamp.desc())
                .all()
            )

            for trade in trades:
                # Get market info
                market = session.query(PolymarketMarket).filter(
                    PolymarketMarket.id == trade.market_id
                ).first()
                market_title = market.title if market else None

                # Get bracket info from outcome
                bracket = None
                outcome = session.query(MarketOutcome).filter(
                    MarketOutcome.outcome_id == trade.outcome_id
                ).first()
                if outcome:
                    bracket = outcome.outcome_text

                trade_list.append({
                    "id": trade.id,
                    "timestamp": trade.timestamp.isoformat(),
                    "market_title": market_title,
                    "bracket": bracket,
                    "side": trade.side,
                    "shares": round(trade.size, 2),  # Trade uses 'size' instead of 'shares'
                    "price": round(trade.price, 4),
                    "total_cost": round(trade.total_cost, 2),
                    "action": trade.strategy,  # Use strategy as action for live trades
                    "edge": None,  # Live trades don't store edge
                    "realized_pnl": round(trade.realized_pnl, 2) if trade.realized_pnl else None,
                })

        return {"trades": trade_list}


@app.get("/settings")
def settings() -> Dict:
    service = BotSettingsService(DatabaseManager())
    return service.get_settings()


@app.post("/settings/auto-trading")
def toggle_auto_trading(payload: AutoTradingPayload) -> Dict:
    service = BotSettingsService(DatabaseManager())
    return service.set_auto_trading(payload.enabled)


@app.post("/paper/reset")
def reset_paper() -> Dict:
    trader = PaperTradingTrader(DatabaseManager())
    return trader.reset_for_new_week()


# ─── Scanner Endpoints ─────────────────────────────────────────────────────────

_scanner_runtime = None


def _get_scanner_runtime():
    global _scanner_runtime
    if _scanner_runtime is None:
        try:
            from services.scanner_runtime import ScannerRuntime
            _scanner_runtime = ScannerRuntime(scheduler_type="background")
        except Exception as e:
            logger.error("Failed to init scanner runtime: %s", e)
            return None
    return _scanner_runtime


@app.get("/scanner/health")
def scanner_health() -> Dict:
    """Scanner health and status."""
    runtime = _get_scanner_runtime()
    if not runtime:
        return {"error": "Scanner not initialized"}
    return runtime.health()


@app.get("/scanner/markets")
def scanner_markets() -> Dict:
    """All scanned markets with categories."""
    db = DatabaseManager()
    try:
        from database.scanner_schema import ScannedMarket
        with db.get_session() as session:
            markets = (
                session.query(ScannedMarket)
                .filter(ScannedMarket.is_active == True)
                .order_by(ScannedMarket.best_edge.desc())
                .limit(500)
                .all()
            )
            return {
                "count": len(markets),
                "markets": [
                    {
                        "market_id": m.market_id,
                        "title": m.title,
                        "category": m.category,
                        "liquidity": m.liquidity,
                        "volume_24h": m.volume_24h,
                        "best_edge": m.best_edge,
                        "last_scanned": m.last_scanned.isoformat() if m.last_scanned else None,
                    }
                    for m in markets
                ],
            }
    except Exception as e:
        logger.error("scanner/markets error: %s", e)
        return {"error": str(e), "markets": []}


@app.get("/scanner/opportunities")
def scanner_opportunities() -> Dict:
    """Current opportunities sorted by edge."""
    db = DatabaseManager()
    try:
        from database.scanner_schema import Opportunity
        with db.get_session() as session:
            opps = (
                session.query(Opportunity)
                .filter(Opportunity.status.in_(["pending", "traded", "paper_traded"]))
                .order_by(Opportunity.edge.desc())
                .limit(100)
                .all()
            )
            return {
                "count": len(opps),
                "opportunities": [
                    {
                        "id": o.id,
                        "market_id": o.market_id,
                        "outcome_id": o.outcome_id,
                        "market_title": o.market_title,
                        "category": o.category,
                        "market_price": round(o.market_price, 4),
                        "fair_value": round(o.fair_value, 4),
                        "edge": round(o.edge * 100, 2),
                        "confidence": round(o.confidence, 3) if o.confidence else None,
                        "kelly_fraction": round(o.kelly_fraction, 4) if o.kelly_fraction else None,
                        "position_size_usd": round(o.position_size_usd, 2) if o.position_size_usd else None,
                        "status": o.status,
                        "created_at": o.created_at.isoformat() if o.created_at else None,
                    }
                    for o in opps
                ],
            }
    except Exception as e:
        logger.error("scanner/opportunities error: %s", e)
        return {"error": str(e), "opportunities": []}


@app.get("/scanner/categories")
def scanner_categories() -> Dict:
    """Category breakdown with performance stats."""
    db = DatabaseManager()
    try:
        from database.scanner_schema import CategoryPerformance, ScannedMarket
        from sqlalchemy import func
        with db.get_session() as session:
            # Count markets per category
            category_counts = dict(
                session.query(
                    ScannedMarket.category,
                    func.count(ScannedMarket.id),
                )
                .filter(ScannedMarket.is_active == True)
                .group_by(ScannedMarket.category)
                .all()
            )

            # Get performance stats
            perfs = session.query(CategoryPerformance).all()
            perf_map = {p.category: p for p in perfs}

            categories = []
            for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
                perf = perf_map.get(cat)
                categories.append({
                    "category": cat,
                    "market_count": count,
                    "total_trades": perf.total_trades if perf else 0,
                    "wins": perf.wins if perf else 0,
                    "losses": perf.losses if perf else 0,
                    "hit_rate": round(perf.hit_rate * 100, 1) if perf else 0,
                    "total_pnl": round(perf.total_pnl, 2) if perf else 0,
                    "roi_pct": round(perf.roi_pct, 1) if perf else 0,
                })

            return {"categories": categories}
    except Exception as e:
        logger.error("scanner/categories error: %s", e)
        return {"error": str(e), "categories": []}


@app.get("/scanner/performance")
def scanner_performance() -> Dict:
    """Per-category ROI, hit rate, total P&L."""
    db = DatabaseManager()
    try:
        from database.scanner_schema import CategoryPerformance, Opportunity
        from sqlalchemy import func
        with db.get_session() as session:
            perfs = session.query(CategoryPerformance).order_by(CategoryPerformance.roi_pct.desc()).all()

            total_pnl = sum(p.total_pnl for p in perfs)
            total_trades = sum(p.total_trades for p in perfs)
            total_wins = sum(p.wins for p in perfs)
            total_losses = sum(p.losses for p in perfs)

            return {
                "summary": {
                    "total_pnl": round(total_pnl, 2),
                    "total_trades": total_trades,
                    "overall_hit_rate": round(total_wins / (total_wins + total_losses) * 100, 1) if (total_wins + total_losses) > 0 else 0,
                    "total_wins": total_wins,
                    "total_losses": total_losses,
                },
                "categories": [
                    {
                        "category": p.category,
                        "total_trades": p.total_trades,
                        "wins": p.wins,
                        "losses": p.losses,
                        "hit_rate": round(p.hit_rate * 100, 1),
                        "total_pnl": round(p.total_pnl, 2),
                        "roi_pct": round(p.roi_pct, 1),
                        "avg_edge": round(p.avg_edge * 100, 2),
                        "avg_confidence": round(p.avg_confidence, 3),
                    }
                    for p in perfs
                ],
            }
    except Exception as e:
        logger.error("scanner/performance error: %s", e)
        return {"error": str(e)}
