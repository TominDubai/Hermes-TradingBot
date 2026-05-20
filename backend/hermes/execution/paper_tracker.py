"""
PaperTracker — virtual trade tracker for non-US markets (EU/UK/HK/JP).

Used when no real broker is available for a symbol. Tracks entries/exits
in-memory exactly like the real broker, feeds the same dashboard endpoints,
and records outcomes for Phase 6 evaluation.

All non-US symbols route here until IBKR is connected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from hermes.execution.base import (
    AccountInfo,
    ExecutionBroker,
    OrderRequest,
    OrderResult,
    Position,
)

logger = logging.getLogger(__name__)

# Markets that route to PaperTracker instead of Alpaca
NON_US_SUFFIXES = (".L", ".PA", ".DE", ".AS", ".MI", ".HK", ".T", ".AX", ".TO")


def is_non_us(symbol: str) -> bool:
    """Return True if this symbol can't be traded on Alpaca."""
    return any(symbol.upper().endswith(s) for s in NON_US_SUFFIXES)


def get_non_us_broker():
    """Return IBKRBroker singleton if configured, else PaperTracker."""
    from hermes.config import settings
    if settings.ibkr_configured:
        try:
            from hermes.execution.ibkr_broker import ibkr_broker
            return ibkr_broker
        except Exception:
            logger.warning("IBKRBroker unavailable, falling back to PaperTracker")
    return paper_tracker


@dataclass
class VirtualTrade:
    trade_id: str
    symbol: str
    qty: float
    entry_price: float
    stop_price: float
    target_price: float
    side: str
    opened_at: datetime
    market: str  # "EU" | "UK" | "HK" | "JP"
    current_price: float = 0.0
    closed: bool = False
    exit_price: float = 0.0
    closed_at: datetime | None = None
    pnl: float = 0.0

    @property
    def unrealised_pnl(self) -> float:
        if self.closed:
            return 0.0
        price = self.current_price or self.entry_price
        return (price - self.entry_price) * self.qty

    @property
    def market_value(self) -> float:
        price = self.current_price or self.entry_price
        return price * self.qty


def _detect_market(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".L"):
        return "UK"
    if s.endswith((".PA", ".DE", ".AS", ".MI")):
        return "EU"
    if s.endswith(".HK"):
        return "HK"
    if s.endswith(".T"):
        return "JP"
    return "OTHER"


class PaperTracker:
    """
    Virtual broker for non-US markets.
    Implements ExecutionBroker interface so it's a drop-in replacement.
    """

    name = "paper_tracker"

    def __init__(self, initial_equity: float = 10_000.0) -> None:
        self._equity = initial_equity
        self._trades: dict[str, VirtualTrade] = {}  # trade_id -> trade
        self._closed: list[VirtualTrade] = []

    async def get_account(self) -> AccountInfo:
        open_value = sum(t.market_value for t in self._trades.values())
        realised = sum(t.pnl for t in self._closed)
        today_pnl = sum(
            t.pnl for t in self._closed
            if t.closed_at and t.closed_at.date() == datetime.now(timezone.utc).date()
        )
        equity = self._equity + open_value + realised
        return AccountInfo(
            equity=equity,
            cash=self._equity,
            buying_power=self._equity,
            portfolio_value=equity,
            today_pnl=today_pnl,
            today_pnl_pct=today_pnl / equity * 100 if equity > 0 else 0.0,
        )

    async def get_positions(self) -> list[Position]:
        return [
            Position(
                symbol=t.symbol,
                qty=t.qty,
                avg_entry_price=t.entry_price,
                current_price=t.current_price or t.entry_price,
                unrealised_pnl=t.unrealised_pnl,
                side="long",
            )
            for t in self._trades.values()
            if not t.closed
        ]

    async def submit_order(self, req: OrderRequest) -> OrderResult:
        trade_id = str(uuid4())
        price = req.limit_price or req.stop_price or 0.0

        trade = VirtualTrade(
            trade_id=trade_id,
            symbol=req.symbol,
            qty=req.qty,
            entry_price=price,
            stop_price=req.stop_price or price * 0.97,
            target_price=req.take_profit_price or price * 1.06,
            side=req.side,
            opened_at=datetime.now(timezone.utc),
            market=_detect_market(req.symbol),
            current_price=price,
        )
        self._trades[trade_id] = trade

        # Persist to DB
        await self._save_position(trade)

        logger.info(
            "PaperTracker: VIRTUAL %s %s x%.0f @ %.4f [%s] — no real broker",
            req.side.upper(), req.symbol, req.qty, price, trade.market,
        )

        return OrderResult(
            broker_order_id=trade_id,
            symbol=req.symbol,
            status="filled",
            filled_qty=req.qty,
            filled_avg_price=price,
            message=f"Virtual fill — {trade.market} market (no broker connected)",
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        if broker_order_id in self._trades:
            del self._trades[broker_order_id]
        return True

    async def close_position(self, symbol: str) -> OrderResult:
        for trade_id, trade in list(self._trades.items()):
            if trade.symbol == symbol and not trade.closed:
                trade.closed = True
                trade.exit_price = trade.current_price or trade.entry_price
                trade.closed_at = datetime.now(timezone.utc)
                trade.pnl = (trade.exit_price - trade.entry_price) * trade.qty
                self._closed.append(trade)
                del self._trades[trade_id]
                # Persist close to DB
                await self._update_position_closed(trade)
                return OrderResult(
                    broker_order_id=trade_id,
                    symbol=symbol,
                    status="filled",
                    filled_qty=trade.qty,
                    filled_avg_price=trade.exit_price,
                )
        return OrderResult(broker_order_id="", symbol=symbol, status="rejected",
                           message="Position not found")

    async def is_market_open(self) -> bool:
        """Non-US markets have their own hours — always return True here,
        the scanner schedule handles timing."""
        return True

    def update_price(self, symbol: str, price: float) -> None:
        """Update current price for a tracked position."""
        for trade in self._trades.values():
            if trade.symbol == symbol:
                trade.current_price = price

    @property
    def open_count(self) -> int:
        return len(self._trades)

    def summary(self) -> dict:
        return {
            "open_trades": self.open_count,
            "closed_trades": len(self._closed),
            "markets": list({t.market for t in self._trades.values()}),
            "total_realised_pnl": round(sum(t.pnl for t in self._closed), 2),
        }

    # ── DB persistence ────────────────────────────────────────

    async def _save_position(self, trade: VirtualTrade) -> None:
        try:
            from hermes.db.session import AsyncSessionFactory
            from hermes.db.models import PaperPosition
            async with AsyncSessionFactory() as session:
                row = PaperPosition(
                    trade_id=trade.trade_id,
                    symbol=trade.symbol,
                    qty=trade.qty,
                    entry_price=trade.entry_price,
                    stop_price=trade.stop_price,
                    target_price=trade.target_price,
                    side=trade.side,
                    market=trade.market,
                    opened_at=trade.opened_at,
                    closed=False,
                    pnl=0.0,
                )
                session.add(row)
                await session.commit()
        except Exception:
            logger.exception("PaperTracker: failed to save position %s", trade.symbol)

    async def _update_position_closed(self, trade: VirtualTrade) -> None:
        try:
            from sqlalchemy import select
            from hermes.db.session import AsyncSessionFactory
            from hermes.db.models import PaperPosition
            async with AsyncSessionFactory() as session:
                stmt = select(PaperPosition).where(PaperPosition.trade_id == trade.trade_id)
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    row.closed = True
                    row.exit_price = trade.exit_price
                    row.closed_at = trade.closed_at
                    row.pnl = trade.pnl
                    await session.commit()
        except Exception:
            logger.exception("PaperTracker: failed to update closed position %s", trade.symbol)

    async def load_from_db(self) -> None:
        """Load open positions from DB on startup — skip any already in memory."""
        try:
            from sqlalchemy import select
            from hermes.db.session import AsyncSessionFactory
            from hermes.db.models import PaperPosition
            async with AsyncSessionFactory() as session:
                stmt = select(PaperPosition).where(PaperPosition.closed == False)  # noqa: E712
                result = await session.execute(stmt)
                rows = result.scalars().all()
                loaded = 0
                for row in rows:
                    if row.trade_id in self._trades:
                        continue  # already in memory, skip
                    trade = VirtualTrade(
                        trade_id=row.trade_id,
                        symbol=row.symbol,
                        qty=row.qty,
                        entry_price=row.entry_price,
                        stop_price=row.stop_price,
                        target_price=row.target_price,
                        side=row.side,
                        opened_at=row.opened_at,
                        market=row.market,
                        current_price=row.entry_price,
                    )
                    self._trades[row.trade_id] = trade
                    loaded += 1
                if loaded:
                    logger.info("PaperTracker: loaded %d open positions from DB", loaded)
        except Exception:
            logger.exception("PaperTracker: failed to load positions from DB")


# Singleton
paper_tracker = PaperTracker(initial_equity=10_000.0)
