"""
PortfolioManager — receives SignalScored events, gates each signal through
risk checks, sizes the position, and submits a bracket order to the broker.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hermes.config import Settings
from hermes.events.bus import bus
from hermes.events.types import (
    Direction,
    OrderFilled,
    Portfolio,
    PositionRequested,
    SignalScored,
)
from hermes.execution.base import ExecutionBroker, OrderRequest
from hermes.execution.paper_tracker import is_non_us, paper_tracker, get_non_us_broker

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Per-portfolio limits
_MAX_POSITIONS: dict[Portfolio, int] = {
    Portfolio.LONG: 20,
    Portfolio.MID: 15,
    Portfolio.INTRA: 8,
}

_MIN_RR: dict[Portfolio, float] = {
    Portfolio.LONG: 3.0,
    Portfolio.MID: 1.5,
    Portfolio.INTRA: 1.2,
}


@dataclass
class _OpenPosition:
    symbol: str
    portfolio: Portfolio
    direction: Direction
    qty: float
    entry_price: float
    stop_price: float
    target_price: float
    broker_order_id: str


class PortfolioManager:
    """
    Gates signals and submits bracket orders to the broker.

    Gates applied in order:
      1. hermes_halted flag
      2. Minimum confluence score
      3. Market open
      4. Maximum open positions per portfolio
      5. No duplicate symbol already in positions
      6. Risk-reward ratio >= minimum per portfolio
      7. Position size check (qty >= 1 after 2% equity sizing)
    """

    def __init__(self, broker: ExecutionBroker, config: Settings) -> None:
        self._broker = broker
        self._config = config
        # Keyed by symbol; one active position per symbol
        self._positions: dict[str, _OpenPosition] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    async def on_signal_scored(self, event: SignalScored) -> None:
        """Main entry point — called by the EventBus for every SignalScored."""
        symbol = event.symbol
        portfolio = event.portfolio

        # Gate 1: system halted
        if self._config.hermes_halted:
            logger.info("Gate 1 FAIL [%s]: hermes_halted is True", symbol)
            return

        # Gate 2: minimum confluence score
        min_confluence: int = getattr(self._config, "min_confluence", 2)
        if event.confluence_score < min_confluence:
            logger.debug(
                "Gate 2 FAIL [%s]: confluence %d < %d",
                symbol,
                event.confluence_score,
                min_confluence,
            )
            return

        # Gate 3: market must be open
        # IBKR handles all markets — always open for non-US, check clock for US
        broker = get_non_us_broker() if is_non_us(symbol) else self._broker
        if not await broker.is_market_open():
            logger.info("Gate 3 FAIL [%s]: market is closed", symbol)
            return

        # Gate 4: max open positions per portfolio
        portfolio_count = sum(
            1 for p in self._positions.values() if p.portfolio == portfolio
        )
        max_pos = self._max_positions(portfolio)
        if portfolio_count >= max_pos:
            logger.debug(
                "Gate 4 FAIL [%s]: %s at max positions (%d/%d)",
                symbol,
                portfolio,
                portfolio_count,
                max_pos,
            )
            return

        # Gate 5: no duplicate symbol
        if symbol in self._positions:
            logger.info("Gate 5 FAIL [%s]: symbol already in open positions", symbol)
            return

        # Gate 6: risk-reward ratio
        entry = event.entry_price
        stop = event.stop_price
        target = event.target_price

        risk = abs(entry - stop)
        if risk == 0:
            logger.info("Gate 6 FAIL [%s]: zero risk (entry == stop)", symbol)
            return

        reward = abs(target - entry)
        rr = reward / risk
        min_rr = self._min_rr(portfolio)
        if rr < min_rr:
            logger.debug(
                "Gate 6 FAIL [%s]: R:R %.2f < %.2f",
                symbol,
                rr,
                min_rr,
            )
            return

        # Gate 7: position sizing — 2% of equity
        active_broker = get_non_us_broker() if is_non_us(symbol) else self._broker
        account = await active_broker.get_account()
        equity = account.equity
        qty = math.floor((equity * 0.02) / entry)
        if qty < 1:
            logger.debug(
                "Gate 7 FAIL [%s]: qty=%d (equity=%.2f, entry=%.2f)",
                symbol,
                qty,
                equity,
                entry,
            )
            return

        # All gates passed — build and submit bracket order
        notional = qty * entry
        broker_name = "paper_tracker" if is_non_us(symbol) else self._broker.name
        logger.info(
            "All gates PASS [%s] portfolio=%s score=%d qty=%d notional=%.2f rr=%.2f broker=%s",
            symbol,
            portfolio,
            event.confluence_score,
            qty,
            notional,
            rr,
            broker_name,
        )

        # Publish PositionRequested
        position_req = PositionRequested(
            signal_id=event.signal_id,
            symbol=symbol,
            portfolio=portfolio,
            direction=event.direction,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            quantity=float(qty),
            notional_value=notional,
        )
        await bus.publish(position_req)

        # Determine side
        side = "buy" if event.direction == Direction.LONG else "sell"

        order_req = OrderRequest(
            symbol=symbol,
            qty=float(qty),
            side=side,
            order_type="bracket",
            time_in_force="day",
            stop_price=stop,
            take_profit_price=target,
            client_order_id=str(event.signal_id),
        )

        result = await active_broker.submit_order(order_req)

        if result.status in ("accepted", "filled"):
            filled_price = result.filled_avg_price if result.filled_avg_price else entry
            filled_qty = result.filled_qty if result.filled_qty else float(qty)

            # Track open position
            self._positions[symbol] = _OpenPosition(
                symbol=symbol,
                portfolio=portfolio,
                direction=event.direction,
                qty=filled_qty,
                entry_price=filled_price,
                stop_price=stop,
                target_price=target,
                broker_order_id=result.broker_order_id,
            )

            # Publish OrderFilled
            order_filled = OrderFilled(
                signal_id=event.signal_id,
                broker_order_id=result.broker_order_id,
                symbol=symbol,
                filled_price=filled_price,
                filled_qty=filled_qty,
                commission=0.0,
            )
            await bus.publish(order_filled)

            logger.info(
                "Order filled [%s]: qty=%.4f @ %.2f broker_id=%s",
                symbol,
                filled_qty,
                filled_price,
                result.broker_order_id,
            )
        else:
            logger.warning(
                "Order not filled [%s]: status=%s message=%s",
                symbol,
                result.status,
                result.message,
            )

    def remove_position(self, symbol: str) -> _OpenPosition | None:
        """Remove a closed/expired position from tracking."""
        return self._positions.pop(symbol, None)

    @property
    def open_positions(self) -> dict[str, _OpenPosition]:
        """Read-only view of currently tracked positions."""
        return dict(self._positions)

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _max_positions(portfolio: Portfolio) -> int:
        return _MAX_POSITIONS[portfolio]

    @staticmethod
    def _min_rr(portfolio: Portfolio) -> float:
        return _MIN_RR[portfolio]
