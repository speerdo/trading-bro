"""
FastAPI Backend — REST API for the SvelteKit dashboard.

Runs on localhost:8000, CORS allowed for localhost:5173.
"""

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from loguru import logger

import config
from agent.database import get_db
from agent.executor import Executor
from agent.risk_manager import RiskManager
from agent.screener import Screener

app = FastAPI(title="TradeBrain API", version="1.0.0")

# CORS: allow SvelteKit dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class ConfigUpdate(BaseModel):
    key: str
    value: str


# ------------------------------------------------------------------
# State (injected at startup)
# ------------------------------------------------------------------

_executor: Executor | None = None
_risk_manager: RiskManager | None = None
_screener: Screener | None = None


def set_agent_state(executor: Executor, risk_manager: RiskManager, screener: Screener) -> None:
    global _executor, _risk_manager, _screener
    _executor = executor
    _risk_manager = risk_manager
    _screener = screener


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@app.get("/api/status")
async def get_status() -> dict:
    cfg = config.get_config()
    rm = _risk_manager
    return {
        "paper_trading": cfg.paper_trading,
        "strategy": cfg.default_strategy,
        "leverage": cfg.default_leverage,
        "risk_per_trade": cfg.default_risk_per_trade,
        "daily_loss_limit": cfg.default_daily_loss_limit,
        "signal_interval": cfg.default_signal_interval,
        "max_watchlist": cfg.default_max_watchlist,
        "circuit_breaker_active": rm.state.circuit_breaker_active if rm else False,
        "daily_loss_usdc": rm.state.daily_loss_usdc if rm else 0.0,
        "manual_pause": rm.state.manual_pause if rm else False,
        "open_positions_count": len(_executor.get_open_positions()) if _executor else 0,
    }


@app.patch("/api/config")
async def update_config(update: ConfigUpdate) -> dict:
    try:
        config.set_config_key(update.key, update.value)
        db = await get_db()
        await db.set_config(update.key, update.value)
        return {"success": True, "key": update.key, "value": update.value}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/trades")
async def get_trades(limit: int = 50) -> list[dict]:
    db = await get_db()
    rows = await db.get_recent_trades(limit)
    return [_record_to_dict(r) for r in rows]


@app.get("/api/signals")
async def get_signals(limit: int = 100) -> list[dict]:
    db = await get_db()
    rows = await db.get_recent_signals(limit)
    return [_record_to_dict(r) for r in rows]


@app.get("/api/stats")
async def get_stats() -> dict:
    db = await get_db()
    stats = await db.get_today_stats()
    return stats


@app.get("/api/watchlist")
async def get_watchlist() -> dict:
    db = await get_db()
    run = await db.get_last_screener_run()
    if run is None:
        return {"coins": [], "scores": {}}
    return {
        "coins": run.get("selected_coins", []),
        "scores": run.get("scores", {}),
        "run_at": run.get("created_at"),
    }


@app.post("/api/screener/run")
async def run_screener() -> dict:
    if _screener is None:
        raise HTTPException(status_code=503, detail="Screener not initialized")
    try:
        coins = await _screener.run()
        return {"success": True, "coins": coins}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/positions")
async def get_positions() -> list[dict]:
    if _executor is None:
        return []
    positions = _executor.get_open_positions()
    return [
        {
            "symbol": p.symbol,
            "direction": p.direction,
            "entry_price": p.entry_price,
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
            "size_usdc": p.size_usdc,
            "margin_usdc": p.margin_usdc,
            "leverage": p.leverage,
            "is_paper": p.is_paper,
            "opened_at": p.opened_at,
            "strategy": p.strategy,
            "confidence": p.confidence,
        }
        for p in positions
    ]


@app.post("/api/positions/{symbol}/close")
async def close_position(symbol: str) -> dict:
    if _executor is None:
        raise HTTPException(status_code=503, detail="Executor not initialized")
    result = await _executor.close_position(symbol)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {"success": True, "symbol": symbol}


@app.post("/api/circuit-breaker/reset")
async def reset_circuit_breaker() -> dict:
    if _risk_manager is None:
        raise HTTPException(status_code=503, detail="Risk manager not initialized")
    _risk_manager.reset_circuit_breaker()
    return {"success": True}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _record_to_dict(record: Any) -> dict:
    """Convert asyncpg Record to plain dict."""
    return dict(record)
