"""
ExecutionBroker protocol — all brokers implement this interface.
AlpacaBroker (Phase 5), IBKRBroker (Phase 5+), PaperBroker (testing).
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class OrderRequest:
    symbol: str
    qty: float
    side: str           # "buy" | "sell"
    order_type: str     # "market" | "limit" | "bracket"
    time_in_force: str  # "day" | "gtc" | "ioc"
    limit_price: float | None = None
    stop_price: float | None = None      # bracket stop-loss
    take_profit_price: float | None = None  # bracket take-profit
    client_order_id: str | None = None


@dataclass
class OrderResult:
    broker_order_id: str
    symbol: str
    status: str         # "accepted" | "filled" | "rejected" | "cancelled"
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0
    message: str = ""


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealised_pnl: float
    side: str           # "long" | "short"


@dataclass
class AccountInfo:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    today_pnl: float
    today_pnl_pct: float


@runtime_checkable
class ExecutionBroker(Protocol):
    name: str

    @abstractmethod
    async def get_account(self) -> AccountInfo: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def submit_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool: ...

    @abstractmethod
    async def close_position(self, symbol: str) -> OrderResult: ...

    @abstractmethod
    async def is_market_open(self) -> bool: ...
