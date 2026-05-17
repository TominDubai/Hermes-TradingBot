"""
PaperBroker — pure in-memory ExecutionBroker for testing and simulation.

No network calls are made. Orders fill immediately at the requested price.
Useful for unit tests and dry-run strategy evaluation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from hermes.execution.base import (
    AccountInfo,
    OrderRequest,
    OrderResult,
    Position,
)


@dataclass
class _PendingOrder:
    order_id: str
    req: OrderRequest


@dataclass
class _ClosedTrade:
    symbol: str
    qty: float
    entry_price: float
    exit_price: float

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty


class PaperBroker:
    """
    In-memory broker that fills orders immediately at the requested price.

    Parameters
    ----------
    initial_equity:
        Starting cash / equity balance (default $10,000).
    """

    name: str = "paper"

    def __init__(self, initial_equity: float = 10_000.0) -> None:
        self._initial_equity = initial_equity
        self._cash: float = initial_equity
        # symbol -> Position
        self._positions: dict[str, Position] = {}
        # order_id -> _PendingOrder (orders not yet acted on — here immediately filled
        # but kept briefly so cancel_order can find them before "fill" is finalised)
        self._pending: dict[str, _PendingOrder] = {}
        # realised P&L history
        self._closed_trades: list[_ClosedTrade] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_price(self, req: OrderRequest) -> float:
        """Return the price at which the order should be filled."""
        if req.limit_price is not None:
            return req.limit_price
        # Market orders — use stop_price as a fallback for test scenarios
        if req.stop_price is not None:
            return req.stop_price
        # Final fallback: 0.0 means the test should always set a price
        return 0.0

    def _realised_pnl(self) -> float:
        return sum(t.pnl for t in self._closed_trades)

    def _unrealised_pnl(self) -> float:
        return sum(p.unrealised_pnl for p in self._positions.values())

    # ------------------------------------------------------------------
    # ExecutionBroker interface
    # ------------------------------------------------------------------

    async def get_account(self) -> AccountInfo:
        total_pnl = self._realised_pnl() + self._unrealised_pnl()
        equity = self._cash + sum(
            p.qty * p.current_price for p in self._positions.values()
        )
        return AccountInfo(
            equity=equity,
            cash=self._cash,
            buying_power=self._cash,
            portfolio_value=equity,
            today_pnl=total_pnl,
            today_pnl_pct=(total_pnl / self._initial_equity * 100) if self._initial_equity else 0.0,
        )

    async def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    async def submit_order(self, req: OrderRequest) -> OrderResult:
        order_id = req.client_order_id or str(uuid.uuid4())
        fill_price = self._fill_price(req)

        # Register as pending (allows cancel_order to find it)
        pending = _PendingOrder(order_id=order_id, req=req)
        self._pending[order_id] = pending

        # Immediately fill
        self._apply_fill(req, fill_price)
        # Remove from pending after fill
        self._pending.pop(order_id, None)

        return OrderResult(
            broker_order_id=order_id,
            symbol=req.symbol,
            status="filled",
            filled_qty=req.qty,
            filled_avg_price=fill_price,
        )

    def _apply_fill(self, req: OrderRequest, fill_price: float) -> None:
        """Update positions and cash for a filled order."""
        symbol = req.symbol
        qty = req.qty
        cost = fill_price * qty

        if req.side.lower() == "buy":
            if symbol in self._positions:
                pos = self._positions[symbol]
                total_qty = pos.qty + qty
                avg_price = (pos.avg_entry_price * pos.qty + fill_price * qty) / total_qty
                self._positions[symbol] = Position(
                    symbol=symbol,
                    qty=total_qty,
                    avg_entry_price=avg_price,
                    current_price=fill_price,
                    unrealised_pnl=(fill_price - avg_price) * total_qty,
                    side="long",
                )
            else:
                self._positions[symbol] = Position(
                    symbol=symbol,
                    qty=qty,
                    avg_entry_price=fill_price,
                    current_price=fill_price,
                    unrealised_pnl=0.0,
                    side="long",
                )
            self._cash -= cost

        else:  # sell
            if symbol in self._positions:
                pos = self._positions[symbol]
                self._closed_trades.append(
                    _ClosedTrade(
                        symbol=symbol,
                        qty=min(qty, pos.qty),
                        entry_price=pos.avg_entry_price,
                        exit_price=fill_price,
                    )
                )
                remaining = pos.qty - qty
                if remaining <= 0:
                    del self._positions[symbol]
                else:
                    self._positions[symbol] = Position(
                        symbol=symbol,
                        qty=remaining,
                        avg_entry_price=pos.avg_entry_price,
                        current_price=fill_price,
                        unrealised_pnl=(fill_price - pos.avg_entry_price) * remaining,
                        side="long",
                    )
            self._cash += cost

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Remove a pending order. Always succeeds (returns True)."""
        self._pending.pop(broker_order_id, None)
        return True

    async def close_position(self, symbol: str) -> OrderResult:
        """Close an open position at current_price, recording P&L."""
        if symbol not in self._positions:
            return OrderResult(
                broker_order_id=str(uuid.uuid4()),
                symbol=symbol,
                status="rejected",
                message=f"No open position for {symbol}.",
            )

        pos = self._positions[symbol]
        exit_price = pos.current_price
        qty = pos.qty
        order_id = str(uuid.uuid4())

        self._closed_trades.append(
            _ClosedTrade(
                symbol=symbol,
                qty=qty,
                entry_price=pos.avg_entry_price,
                exit_price=exit_price,
            )
        )
        del self._positions[symbol]
        self._cash += exit_price * qty

        return OrderResult(
            broker_order_id=order_id,
            symbol=symbol,
            status="filled",
            filled_qty=qty,
            filled_avg_price=exit_price,
        )

    async def is_market_open(self) -> bool:
        """Paper broker is always open."""
        return True
