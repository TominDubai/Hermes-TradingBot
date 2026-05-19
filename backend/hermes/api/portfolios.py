"""
Portfolios, settings, and kill-switch API routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["portfolios"])


# ── In-memory state (Phase 5 moves this to DB) ────────────────

_portfolio_stats: dict[str, dict[str, Any]] = {
    "long":  {"open_positions": 0, "today_pnl": 0.0, "win_rate_30d": 0.0, "equity": 10000.0},
    "mid":   {"open_positions": 0, "today_pnl": 0.0, "win_rate_30d": 0.0, "equity": 10000.0},
    "intra": {"open_positions": 0, "today_pnl": 0.0, "win_rate_30d": 0.0, "equity": 10000.0},
}

_settings: dict[str, Any] = {
    "halted": False,
    "min_confluence": 2,
    "max_positions_long": 20,
    "max_positions_mid": 15,
    "max_positions_intra": 8,
    "daily_loss_limit_pct": 2.0,
    "long_universe": "long_us",
    "mid_universe": "mid_us",
    "intra_universe": "intra_us",
}


# ── Portfolios ────────────────────────────────────────────────

@router.get("/portfolios")
async def list_portfolios() -> dict[str, Any]:
    return {
        "portfolios": [
            {
                "id": pid,
                "name": pid.capitalize(),
                "description": {
                    "long": "6–18 month holds, weekly scan",
                    "mid": "1–8 week holds, daily scan",
                    "intra": "Same-day to 3-day, 15-min scan",
                }[pid],
                **stats,
            }
            for pid, stats in _portfolio_stats.items()
        ]
    }


@router.get("/broker/paper-tracker")
async def paper_tracker_status() -> dict:
    """Virtual positions for EU/UK/HK/JP markets (no real broker yet)."""
    from hermes.execution.paper_tracker import paper_tracker
    positions = await paper_tracker.get_positions()
    account = await paper_tracker.get_account()
    return {
        "broker": "paper_tracker",
        "note": "Virtual tracking for EU/UK/HK/JP — no real execution until IBKR connected",
        "equity": account.equity,
        "today_pnl": account.today_pnl,
        "open_positions": len(positions),
        "positions": [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_entry": p.avg_entry_price,
                "current_price": p.current_price,
                "unrealised_pnl": p.unrealised_pnl,
                "side": p.side,
            }
            for p in positions
        ],
        "summary": paper_tracker.summary(),
    }


@router.get("/portfolios/{portfolio_id}")
async def get_portfolio(portfolio_id: str) -> dict[str, Any]:
    if portfolio_id not in _portfolio_stats:
        return {"error": f"Unknown portfolio: {portfolio_id}"}
    return {"id": portfolio_id, **_portfolio_stats[portfolio_id]}


# ── Settings ──────────────────────────────────────────────────

@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    return _settings


class SettingsUpdate(BaseModel):
    min_confluence: int | None = None
    daily_loss_limit_pct: float | None = None
    max_positions_long: int | None = None
    max_positions_mid: int | None = None
    max_positions_intra: int | None = None


@router.patch("/settings")
async def update_settings(update: SettingsUpdate) -> dict[str, Any]:
    for field, value in update.model_dump(exclude_none=True).items():
        _settings[field] = value
    return {"status": "updated", "settings": _settings}


# ── Kill switch ───────────────────────────────────────────────

@router.post("/settings/halt")
async def halt() -> dict[str, Any]:
    _settings["halted"] = True
    # Note: In production this writes to .env / DB so it survives restarts
    return {"status": "halted", "message": "All new signal entries halted. Existing positions unaffected."}


@router.post("/settings/resume")
async def resume() -> dict[str, Any]:
    _settings["halted"] = False
    return {"status": "resumed", "message": "Kill switch cleared. New signals will be processed."}
