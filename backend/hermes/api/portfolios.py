"""
Portfolios, settings, and kill-switch API routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["portfolios"])


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


# ── Portfolio stats (live from brokers + DB) ───────────────────

async def _get_live_portfolio_stats() -> dict[str, dict[str, Any]]:
    """Fetch real equity, positions and P&L from Alpaca + PaperTracker + DB."""
    from hermes.config import settings
    from hermes.execution.paper_tracker import paper_tracker
    from hermes.api.signals import _signals

    stats: dict[str, dict[str, Any]] = {
        "long":  {"open_positions": 0, "today_pnl": 0.0, "win_rate_30d": 0.0, "equity": 0.0},
        "mid":   {"open_positions": 0, "today_pnl": 0.0, "win_rate_30d": 0.0, "equity": 0.0},
        "intra": {"open_positions": 0, "today_pnl": 0.0, "win_rate_30d": 0.0, "equity": 0.0},
    }

    # Alpaca account (US)
    try:
        if settings.alpaca_configured:
            from hermes.execution.alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()
            account = await broker.get_account()
            positions = await broker.get_positions()
            # Distribute Alpaca equity across portfolios by position count
            # For now attribute all to intra (US focus) — improve when we track per-portfolio
            us_positions = len(positions)
            pnl = account.today_pnl
            for pid in ["long", "mid", "intra"]:
                stats[pid]["equity"] = account.equity / 3
            stats["intra"]["open_positions"] = us_positions
            stats["intra"]["today_pnl"] = round(pnl, 2)
    except Exception:
        for pid in ["long", "mid", "intra"]:
            stats[pid]["equity"] = 10000.0

    # PaperTracker (EU/UK/HK/JP virtual positions)
    try:
        pt_account = await paper_tracker.get_account()
        pt_positions = await paper_tracker.get_positions()
        stats["mid"]["equity"] += pt_account.equity - 10000.0  # add P&L on top
        stats["mid"]["open_positions"] += len(pt_positions)
        stats["mid"]["today_pnl"] += pt_account.today_pnl
    except Exception:
        pass

    # Win rate from DB signals with outcomes
    try:
        from sqlalchemy import select, func
        from hermes.db.models import Signal
        from hermes.db.session import AsyncSessionFactory
        async with AsyncSessionFactory() as session:
            for pid in ["long", "mid", "intra"]:
                stmt = select(
                    func.count(Signal.id).label("total"),
                    func.sum(
                        (Signal.outcome == "WIN").cast(type_=__import__("sqlalchemy").Integer)
                    ).label("wins")
                ).where(
                    Signal.portfolio == pid,
                    Signal.outcome.isnot(None)
                )
                result = await session.execute(stmt)
                row = result.one()
                total = row.total or 0
                wins = int(row.wins or 0)
                stats[pid]["win_rate_30d"] = round(wins / total, 3) if total > 0 else 0.0
    except Exception:
        pass

    return stats


# ── Endpoints ──────────────────────────────────────────────────

@router.get("/portfolios")
async def list_portfolios() -> dict[str, Any]:
    stats = await _get_live_portfolio_stats()
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
                **s,
            }
            for pid, s in stats.items()
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


@router.get("/portfolios/{portfolio_id}/positions")
async def get_portfolio_positions(portfolio_id: str) -> dict[str, Any]:
    """Live open positions for a portfolio — Alpaca (US) + PaperTracker (EU/UK/HK/JP)."""
    positions = []

    # Alpaca positions (intra/long/mid — for now show all US positions on each)
    from hermes.config import settings as cfg
    if cfg.alpaca_configured:
        try:
            from hermes.execution.alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()
            alpaca_positions = await broker.get_positions()
            for p in alpaca_positions:
                positions.append({
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_entry": p.avg_entry_price,
                    "current_price": p.current_price,
                    "unrealised_pnl": round(p.unrealised_pnl, 2),
                    "unrealised_pnl_pct": round((p.current_price - p.avg_entry_price) / p.avg_entry_price * 100, 2) if p.avg_entry_price else 0,
                    "side": p.side,
                    "broker": "alpaca",
                    "market": "US",
                })
        except Exception:
            pass

    # PaperTracker positions (mid portfolio — EU/UK/HK/JP)
    if portfolio_id == "mid":
        from hermes.execution.paper_tracker import paper_tracker
        try:
            pt_positions = await paper_tracker.get_positions()
            for p in pt_positions:
                pnl_pct = round((p.current_price - p.avg_entry_price) / p.avg_entry_price * 100, 2) if p.avg_entry_price else 0
                positions.append({
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_entry": p.avg_entry_price,
                    "current_price": p.current_price,
                    "unrealised_pnl": round(p.unrealised_pnl, 2),
                    "unrealised_pnl_pct": pnl_pct,
                    "side": p.side,
                    "broker": "paper_tracker",
                    "market": "EU/UK/HK/JP",
                })
        except Exception:
            pass

    return {
        "portfolio": portfolio_id,
        "count": len(positions),
        "positions": positions,
    }


@router.get("/portfolios/{portfolio_id}")
async def get_portfolio(portfolio_id: str) -> dict[str, Any]:
    stats = await _get_live_portfolio_stats()
    if portfolio_id not in stats:
        return {"error": f"Unknown portfolio: {portfolio_id}"}
    return {"id": portfolio_id, **stats[portfolio_id]}


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
    return {"status": "halted", "message": "All new signal entries halted. Existing positions unaffected."}


@router.post("/settings/resume")
async def resume() -> dict[str, Any]:
    _settings["halted"] = False
    return {"status": "resumed", "message": "Kill switch cleared. New signals will be processed."}
