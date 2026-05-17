"""
Backtest engine — walk-forward simulation.

Architecture:
  - For each symbol in a universe, fetch full history
  - Walk bar-by-bar (no lookahead: setup only sees bars up to current)
  - When a setup fires, record a SimulatedTrade
  - Advance until stop or target is hit, or max_hold_bars exceeded (EXPIRED)
  - Aggregate into BacktestResult

This is NOT a proper event-driven backtest (that's Phase 7 territory).
It's a fast vectorised pre-filter to kill setups with no edge before live paper trading.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from hermes.setups.base import Setup

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────

@dataclass
class SimulatedTrade:
    symbol: str
    setup_name: str
    portfolio: str
    direction: str
    entry_bar: int          # index in the full df
    entry_price: float
    stop_price: float
    target_price: float
    raw_score: float
    exit_bar: int | None = None
    exit_price: float | None = None
    outcome: str | None = None   # WIN / LOSS / EXPIRED
    hold_bars: int = 0
    pnl_pct: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward(self) -> float:
        return abs(self.target_price - self.entry_price)

    @property
    def rr_ratio(self) -> float:
        return self.reward / self.risk if self.risk > 0 else 0.0


@dataclass
class SetupStats:
    setup_name: str
    portfolio: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    expired: int = 0
    total_pnl_pct: float = 0.0
    avg_rr_realised: float = 0.0
    max_drawdown_pct: float = 0.0

    @property
    def win_rate(self) -> float:
        closed = self.wins + self.losses
        return self.wins / closed if closed > 0 else 0.0

    @property
    def avg_pnl_pct(self) -> float:
        return self.total_pnl_pct / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def passes_gate(self) -> bool:
        """True if this setup has enough edge to go live."""
        return (
            self.win_rate >= 0.40
            and self.avg_rr_realised >= 1.0
            and self.total_trades >= 5  # need enough samples
        )


@dataclass
class BacktestResult:
    portfolio: str
    start_date: date
    end_date: date
    trades: list[SimulatedTrade] = field(default_factory=list)
    setup_stats: dict[str, SetupStats] = field(default_factory=dict)

    def add_trade(self, trade: SimulatedTrade) -> None:
        self.trades.append(trade)
        name = trade.setup_name
        if name not in self.setup_stats:
            self.setup_stats[name] = SetupStats(name, trade.portfolio)
        stats = self.setup_stats[name]
        stats.total_trades += 1
        if trade.outcome == "WIN":
            stats.wins += 1
        elif trade.outcome == "LOSS":
            stats.losses += 1
        else:
            stats.expired += 1
        stats.total_pnl_pct += trade.pnl_pct
        # running avg R:R realised
        if trade.outcome in ("WIN", "LOSS") and trade.rr_ratio > 0:
            n = stats.wins + stats.losses
            stats.avg_rr_realised = (
                (stats.avg_rr_realised * (n - 1) + abs(trade.pnl_pct) / (trade.risk / trade.entry_price * 100))
                / n if n > 0 and trade.risk > 0 and trade.entry_price > 0 else stats.avg_rr_realised
            )

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def passing_setups(self) -> list[str]:
        return [name for name, s in self.setup_stats.items() if s.passes_gate]

    @property
    def failing_setups(self) -> list[str]:
        return [name for name, s in self.setup_stats.items() if not s.passes_gate]


# ── Backtest engine ───────────────────────────────────────────

class BacktestEngine:
    """
    Walk-forward backtester.

    Usage:
        engine = BacktestEngine(max_hold_bars=20, slippage_pct=0.001)
        result = await engine.run(setup, symbols, timeframe, start, end)
    """

    def __init__(
        self,
        max_hold_bars: int = 20,
        slippage_pct: float = 0.001,   # 0.1% slippage on entry
        commission_pct: float = 0.0005, # 0.05% commission each way
    ) -> None:
        self.max_hold_bars = max_hold_bars
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct

    async def run(
        self,
        setup: Setup,
        symbols: list[str],
        timeframe: str,
        start: date,
        end: date,
        portfolio: str = "unknown",
    ) -> BacktestResult:
        from hermes.data.base import Timeframe
        from hermes.data.yfinance_provider import YFinanceProvider

        result = BacktestResult(portfolio=portfolio, start_date=start, end_date=end)
        provider = YFinanceProvider()
        tf = Timeframe(timeframe)

        for symbol in symbols:
            try:
                df = await provider.get_ohlcv(symbol, tf, start, end)
                if df.empty or len(df) < setup.min_bars + self.max_hold_bars:
                    continue
                trades = self._simulate_symbol(setup, df, symbol, portfolio)
                for t in trades:
                    result.add_trade(t)
            except Exception:
                logger.exception("Backtest error for %s", symbol)

        return result

    def _simulate_symbol(
        self,
        setup: Setup,
        df: pd.DataFrame,
        symbol: str,
        portfolio: str,
    ) -> list[SimulatedTrade]:
        trades: list[SimulatedTrade] = []
        n = len(df)
        in_trade = False
        active_trade: SimulatedTrade | None = None

        for i in range(setup.min_bars, n - 1):
            if in_trade and active_trade is not None:
                # Check for exit on this bar
                bar_high = float(df["high"].iloc[i])
                bar_low = float(df["low"].iloc[i])
                bar_close = float(df["close"].iloc[i])

                hit_stop = bar_low <= active_trade.stop_price
                hit_target = bar_high >= active_trade.target_price
                expired = (i - active_trade.entry_bar) >= self.max_hold_bars

                if hit_target and not hit_stop:
                    # Target hit first (optimistic — use target price)
                    exit_price = active_trade.target_price
                    outcome = "WIN"
                elif hit_stop:
                    exit_price = active_trade.stop_price
                    outcome = "LOSS"
                elif expired:
                    exit_price = bar_close
                    outcome = "EXPIRED"
                else:
                    active_trade.hold_bars += 1
                    continue

                active_trade.exit_bar = i
                active_trade.exit_price = exit_price
                active_trade.outcome = outcome
                active_trade.hold_bars = i - active_trade.entry_bar
                pnl = (exit_price - active_trade.entry_price) / active_trade.entry_price
                # Apply slippage and commission
                pnl -= self.slippage_pct + 2 * self.commission_pct
                active_trade.pnl_pct = round(pnl * 100, 4)
                trades.append(active_trade)
                in_trade = False
                active_trade = None
                continue

            # Only look for new setup if not in trade
            window = df.iloc[:i + 1]
            result = setup.detect(window)
            if result is None:
                continue

            # Slippage: entry price slightly worse
            slip = result.entry * self.slippage_pct
            entry_price = result.entry + slip  # long only for now

            trade = SimulatedTrade(
                symbol=symbol,
                setup_name=setup.name,
                portfolio=portfolio,
                direction=result.direction.value,
                entry_bar=i,
                entry_price=entry_price,
                stop_price=result.stop,
                target_price=result.target,
                raw_score=result.score,
                metadata=result.metadata,
            )
            in_trade = True
            active_trade = trade

        # Close any still-open trade at end of data
        if in_trade and active_trade is not None:
            last_close = float(df["close"].iloc[-1])
            active_trade.exit_bar = n - 1
            active_trade.exit_price = last_close
            active_trade.outcome = "EXPIRED"
            active_trade.hold_bars = n - 1 - active_trade.entry_bar
            pnl = (last_close - active_trade.entry_price) / active_trade.entry_price
            pnl -= self.slippage_pct + 2 * self.commission_pct
            active_trade.pnl_pct = round(pnl * 100, 4)
            trades.append(active_trade)

        return trades
