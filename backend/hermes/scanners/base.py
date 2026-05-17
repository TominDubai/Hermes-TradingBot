"""
Scanner base class and the three portfolio scanners.
Each scanner:
  1. Loads its universe
  2. Fetches OHLCV data for each symbol
  3. Runs its setups against each symbol
  4. Publishes SignalDetected events to the event bus
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date, timedelta

from hermes.data.base import Timeframe
from hermes.data.universe import Universe, load_universe
from hermes.data.yfinance_provider import YFinanceProvider
from hermes.events.bus import bus
from hermes.events.types import Portfolio, SignalDetected
from hermes.setups.base import Setup, SetupResult

logger = logging.getLogger(__name__)


class BaseScanner(ABC):
    portfolio: Portfolio
    universe_name: str
    timeframe: Timeframe
    lookback_days: int = 400

    def __init__(self) -> None:
        self.provider = YFinanceProvider()
        self._universe: Universe | None = None

    @property
    def universe(self) -> Universe:
        if self._universe is None:
            self._universe = load_universe(self.universe_name)
        return self._universe

    @property
    @abstractmethod
    def setups(self) -> list[Setup]:
        ...

    async def run_once(self) -> int:
        """Scan the full universe. Returns number of signals published."""
        signals = 0
        end = date.today()
        start = end - timedelta(days=self.lookback_days)

        for symbol in self.universe.symbols:
            try:
                df = await self.provider.get_ohlcv(symbol, self.timeframe, start, end)
                if df.empty:
                    continue
                for setup in self.setups:
                    result = setup.detect(df)
                    if result is not None:
                        await self._publish(symbol, result)
                        signals += 1
            except Exception:
                logger.exception("Scanner error for %s", symbol)

        logger.info("%s scan complete: %d signals", self.portfolio.value, signals)
        return signals

    async def _publish(self, symbol: str, result: SetupResult) -> None:
        event = SignalDetected(
            symbol=symbol,
            portfolio=self.portfolio,
            direction=result.direction,
            setup_name=result.setup_name,
            timeframe=result.timeframe,
            raw_score=result.score,
            entry_price=result.entry,
            stop_price=result.stop,
            target_price=result.target,
            features=result.metadata,
        )
        await bus.publish(event)
        logger.info("Signal: %s %s %s score=%.2f", symbol, result.setup_name,
                    result.direction.value, result.score)


class LongScanner(BaseScanner):
    portfolio = Portfolio.LONG
    universe_name = "long_us"
    timeframe = Timeframe.W1
    lookback_days = 1500  # 5 years for EMA200

    @property
    def setups(self) -> list[Setup]:
        from hermes.setups.ema_trend_follow import EMATrendFollow
        from hermes.setups.fundamental_quality import FundamentalQuality
        return [EMATrendFollow(), FundamentalQuality()]


class MidScanner(BaseScanner):
    portfolio = Portfolio.MID
    universe_name = "mid_us"
    timeframe = Timeframe.D1
    lookback_days = 400

    @property
    def setups(self) -> list[Setup]:
        from hermes.setups.breakout_consolidation import BreakoutConsolidation
        from hermes.setups.cup_and_handle import CupAndHandle
        from hermes.setups.mean_reversion import MeanReversion
        return [CupAndHandle(), MeanReversion(), BreakoutConsolidation()]


class IntraScanner(BaseScanner):
    portfolio = Portfolio.INTRA
    universe_name = "intra_us"
    timeframe = Timeframe.M5
    lookback_days = 5  # only need recent intraday data

    @property
    def setups(self) -> list[Setup]:
        from hermes.setups.momentum_continuation import MomentumContinuation
        from hermes.setups.opening_range_breakout import OpeningRangeBreakout
        from hermes.setups.vwap_reversion import VWAPReversion
        return [OpeningRangeBreakout(), VWAPReversion(), MomentumContinuation()]
