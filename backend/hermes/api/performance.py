"""
Performance and backtest API routes.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api", tags=["performance"])


@router.get("/performance/summary")
async def performance_summary() -> dict[str, Any]:
    """Aggregate win rates, PnL, and R:R across all portfolios."""
    from hermes.outcome.tracker import tracker
    return {
        "open_positions": tracker.open_count,
        "message": "Full performance aggregation available after DB wire-up in Phase 5.",
    }


@router.post("/backtest/run")
async def run_backtest(
    portfolio: str = Query(..., description="long | mid | intra"),
    setup_name: str = Query(..., description="e.g. ema_trend_follow"),
    symbols: str = Query("AAPL,MSFT,GOOGL", description="Comma-separated tickers"),
    lookback_days: int = Query(365, ge=60, le=1500),
) -> dict[str, Any]:
    """
    Trigger a backtest for a specific setup and return summary stats.
    NOTE: This runs synchronously in-request for dev. Long universes will be slow.
    """
    from hermes.backtest.engine import BacktestEngine
    from hermes.backtest.report import generate_report

    # Resolve setup
    setup = _get_setup(portfolio, setup_name)
    if setup is None:
        raise HTTPException(status_code=404, detail=f"Setup '{setup_name}' not found in '{portfolio}'")

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    end = date.today()
    from datetime import timedelta
    start = end - timedelta(days=lookback_days)

    engine = BacktestEngine(max_hold_bars=20 if portfolio == "intra" else 60)
    result = await engine.run(setup, symbol_list, _tf(portfolio), start, end, portfolio)

    report_path = generate_report(result)

    return {
        "portfolio": portfolio,
        "setup": setup_name,
        "symbols": symbol_list,
        "period": f"{start} → {end}",
        "total_trades": result.total_trades,
        "passing_setups": result.passing_setups,
        "failing_setups": result.failing_setups,
        "report_path": str(report_path),
        "stats": {
            name: {
                "trades": s.total_trades,
                "win_rate": round(s.win_rate, 3),
                "avg_pnl_pct": round(s.avg_pnl_pct, 3),
                "avg_rr": round(s.avg_rr_realised, 3),
                "passes_gate": s.passes_gate,
            }
            for name, s in result.setup_stats.items()
        },
    }


def _get_setup(portfolio: str, name: str):
    setups_map: dict[str, Any] = {}
    try:
        if portfolio == "long":
            from hermes.setups.ema_trend_follow import EMATrendFollow
            from hermes.setups.fundamental_quality import FundamentalQuality
            setups_map = {"ema_trend_follow": EMATrendFollow, "fundamental_quality": FundamentalQuality}
        elif portfolio == "mid":
            from hermes.setups.breakout_consolidation import BreakoutConsolidation
            from hermes.setups.cup_and_handle import CupAndHandle
            from hermes.setups.mean_reversion import MeanReversion
            setups_map = {"cup_and_handle": CupAndHandle, "mean_reversion": MeanReversion,
                          "breakout_consolidation": BreakoutConsolidation}
        elif portfolio == "intra":
            from hermes.setups.momentum_continuation import MomentumContinuation
            from hermes.setups.opening_range_breakout import OpeningRangeBreakout
            from hermes.setups.vwap_reversion import VWAPReversion
            setups_map = {"opening_range_breakout": OpeningRangeBreakout,
                          "vwap_reversion": VWAPReversion, "momentum_continuation": MomentumContinuation}
        cls = setups_map.get(name)
        return cls() if cls else None
    except Exception:
        return None


def _tf(portfolio: str) -> str:
    return {"long": "1wk", "mid": "1d", "intra": "15m"}.get(portfolio, "1d")
