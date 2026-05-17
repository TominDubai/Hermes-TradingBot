from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Portfolio(StrEnum):
    LONG = "long"
    MID = "mid"
    INTRA = "intra"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"


class OutcomeStatus(StrEnum):
    OPEN = "open"
    WIN = "win"
    LOSS = "loss"
    EXPIRED = "expired"


class BaseEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1


# ── Scanner events ────────────────────────────────────────────

class SignalDetected(BaseEvent):
    """Raw signal from a scanner before scoring."""
    symbol: str
    portfolio: Portfolio
    direction: Direction
    setup_name: str
    timeframe: str
    raw_score: float
    entry_price: float
    stop_price: float
    target_price: float
    features: dict[str, Any] = Field(default_factory=dict)


# ── Scoring events ────────────────────────────────────────────

class SignalScored(BaseEvent):
    """Signal after the rule scorer has run."""
    signal_id: UUID
    symbol: str
    portfolio: Portfolio
    direction: Direction
    setup_name: str
    confluence_score: int          # 1–6 (number of confirming factors)
    entry_price: float
    stop_price: float
    target_price: float
    features: dict[str, Any] = Field(default_factory=dict)


# ── Portfolio / execution events ──────────────────────────────

class PositionRequested(BaseEvent):
    """Portfolio manager approved a signal for execution."""
    signal_id: UUID
    symbol: str
    portfolio: Portfolio
    direction: Direction
    entry_price: float
    stop_price: float
    target_price: float
    quantity: float
    notional_value: float


class OrderSubmitted(BaseEvent):
    """Broker adapter accepted the order."""
    signal_id: UUID
    broker_order_id: str
    symbol: str
    quantity: float
    order_type: str


class OrderFilled(BaseEvent):
    """Order confirmed filled by broker."""
    signal_id: UUID
    broker_order_id: str
    symbol: str
    filled_price: float
    filled_qty: float
    commission: float = 0.0


class OrderFailed(BaseEvent):
    """Order rejected or errored at broker."""
    signal_id: UUID
    symbol: str
    reason: str


# ── Outcome events ────────────────────────────────────────────

class OutcomeRecorded(BaseEvent):
    """Position closed — win, loss, or expired."""
    signal_id: UUID
    symbol: str
    portfolio: Portfolio
    status: OutcomeStatus
    entry_price: float
    exit_price: float
    realised_pnl: float
    hold_duration_hours: float


# ── System events ─────────────────────────────────────────────

class KillSwitchTripped(BaseEvent):
    """Daily loss limit hit — all new orders halted."""
    portfolio: Portfolio
    daily_pnl: float
    threshold: float
    reason: str
