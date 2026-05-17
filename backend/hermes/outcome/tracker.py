"""
Outcome tracker — polls open signals and marks WIN / LOSS / EXPIRED.

Runs as an APScheduler job every hour.
For now works against the in-memory signal store (Phase 5 will wire to DB).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hermes.events.bus import bus
from hermes.events.types import OutcomeRecorded, OutcomeStatus, Portfolio

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    signal_id: str
    symbol: str
    portfolio: Portfolio
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    max_hold_hours: float = 72.0   # expire after 3 days by default


class OutcomeTracker:
    """
    In-memory outcome tracker.
    Phase 5 will replace this with DB-backed persistence.
    """

    def __init__(self) -> None:
        self._positions: dict[str, OpenPosition] = {}

    def open(self, position: OpenPosition) -> None:
        self._positions[position.signal_id] = position
        logger.info("OutcomeTracker: opened %s %s @ %.4f",
                    position.symbol, position.direction, position.entry_price)

    def close(self, signal_id: str) -> OpenPosition | None:
        return self._positions.pop(signal_id, None)

    @property
    def open_count(self) -> int:
        return len(self._positions)

    async def check_all(self) -> int:
        """
        Poll current prices for all open positions and record outcomes.
        Returns number of positions resolved this cycle.
        """
        if not self._positions:
            return 0

        resolved = 0
        now = datetime.now(UTC)

        # Fetch prices for all open symbols
        symbols = list({p.symbol for p in self._positions.values()})
        prices = await _fetch_prices(symbols)

        to_close: list[str] = []

        for sig_id, pos in self._positions.items():
            price = prices.get(pos.symbol)
            if price is None:
                continue

            age_hours = (now - pos.entry_time).total_seconds() / 3600

            # Determine outcome
            if price >= pos.target_price:
                outcome = OutcomeStatus.WIN
                exit_price = pos.target_price
            elif price <= pos.stop_price:
                outcome = OutcomeStatus.LOSS
                exit_price = pos.stop_price
            elif age_hours >= pos.max_hold_hours:
                outcome = OutcomeStatus.EXPIRED
                exit_price = price
            else:
                continue

            pnl = (exit_price - pos.entry_price) / pos.entry_price * 100

            event = OutcomeRecorded(
                signal_id=sig_id,  # type: ignore[arg-type]
                symbol=pos.symbol,
                portfolio=pos.portfolio,
                status=outcome,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                realised_pnl=round(pnl, 4),
                hold_duration_hours=round(age_hours, 2),
            )
            await bus.publish(event)
            to_close.append(sig_id)
            resolved += 1

            logger.info(
                "OutcomeTracker: %s %s %s pnl=%.2f%%",
                pos.symbol, outcome.value, pos.portfolio.value, pnl,
            )

        for sig_id in to_close:
            self._positions.pop(sig_id, None)

        return resolved


async def _fetch_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch last prices for a list of symbols via yfinance."""
    from datetime import date, timedelta

    from hermes.data.base import Timeframe
    from hermes.data.yfinance_provider import YFinanceProvider

    provider = YFinanceProvider()
    prices: dict[str, float] = {}
    end = date.today()
    start = end - timedelta(days=3)

    async def fetch_one(symbol: str) -> None:
        try:
            df = await provider.get_ohlcv(symbol, Timeframe.D1, start, end)
            if not df.empty:
                prices[symbol] = float(df["close"].iloc[-1])
        except Exception:
            pass

    await asyncio.gather(*[fetch_one(s) for s in symbols])
    return prices


# Singleton used by the scheduler
tracker = OutcomeTracker()


async def run_outcome_check() -> None:
    """APScheduler entry point."""
    resolved = await tracker.check_all()
    if resolved:
        logger.info("OutcomeTracker: resolved %d positions this cycle", resolved)
