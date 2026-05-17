"""
AlpacaBroker — ExecutionBroker implementation backed by the alpaca-py SDK.

All SDK calls are synchronous; they are dispatched via asyncio.to_thread()
so the async FastAPI / APScheduler loop is never blocked.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from hermes.config import settings
from hermes.data.base import ProviderError
from hermes.execution.base import (
    AccountInfo,
    OrderRequest,
    OrderResult,
    Position,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_BROKER_NAME = "alpaca_broker"


def _map_side(side: str) -> OrderSide:
    return OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL


def _map_tif(tif: str) -> TimeInForce:
    mapping = {
        "day": TimeInForce.DAY,
        "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC,
        "opg": TimeInForce.OPG,
        "cls": TimeInForce.CLS,
        "fok": TimeInForce.FOK,
    }
    return mapping.get(tif.lower(), TimeInForce.DAY)


class AlpacaBroker:
    """
    ExecutionBroker backed by Alpaca paper or live trading API.

    Raises ProviderError immediately on any method call if API keys are not
    configured in settings.
    """

    def __init__(self) -> None:
        self._client: TradingClient | None = None

    # ------------------------------------------------------------------
    # Protocol attribute
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        mode = getattr(settings, "alpaca_mode", "paper")
        return f"alpaca_{mode}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_configured(self) -> None:
        if not settings.alpaca_configured:
            raise ProviderError(
                _BROKER_NAME,
                "N/A",
                "Alpaca API keys are not configured. "
                "Set ALPACA_PAPER_API_KEY / ALPACA_PAPER_SECRET_KEY (paper mode) "
                "or ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY (live mode) in .env.",
            )

    def _get_client(self) -> TradingClient:
        """Return a cached TradingClient, creating it on first call."""
        if self._client is None:
            self._require_configured()
            is_paper = settings.alpaca_mode == "paper"
            if is_paper:
                api_key = settings.alpaca_paper_api_key
                secret_key = settings.alpaca_paper_secret_key
            else:
                api_key = settings.alpaca_live_api_key
                secret_key = settings.alpaca_live_secret_key  # type: ignore[assignment]

            self._client = TradingClient(
                api_key=api_key,
                secret_key=secret_key,
                paper=is_paper,
            )
        return self._client

    # ------------------------------------------------------------------
    # ExecutionBroker interface
    # ------------------------------------------------------------------

    async def get_account(self) -> AccountInfo:
        self._require_configured()
        client = self._get_client()
        account = await asyncio.to_thread(client.get_account)  # type: ignore[func-returns-value]

        equity = float(account.equity)  # type: ignore[union-attr]
        last_equity = float(account.last_equity)  # type: ignore[union-attr]
        today_pnl = equity - last_equity
        today_pnl_pct = (today_pnl / last_equity * 100) if last_equity else 0.0

        return AccountInfo(
            equity=equity,
            cash=float(account.cash),  # type: ignore[union-attr]
            buying_power=float(account.buying_power),  # type: ignore[union-attr]
            portfolio_value=float(account.portfolio_value),  # type: ignore[union-attr]
            today_pnl=today_pnl,
            today_pnl_pct=today_pnl_pct,
        )

    async def get_positions(self) -> list[Position]:
        self._require_configured()
        client = self._get_client()
        raw_positions = await asyncio.to_thread(client.get_all_positions)

        result: list[Position] = []
        for p in raw_positions:
            result.append(
                Position(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    current_price=float(p.current_price),
                    unrealised_pnl=float(p.unrealized_pl),
                    side=str(p.side.value) if hasattr(p.side, "value") else str(p.side),
                )
            )
        return result

    async def submit_order(self, req: OrderRequest) -> OrderResult:
        self._require_configured()
        client = self._get_client()

        try:
            if req.order_type == "bracket":
                # Bracket order requires take-profit and stop-loss legs
                tp_price = req.take_profit_price
                sl_price = req.stop_price
                if tp_price is None or sl_price is None:
                    return OrderResult(
                        broker_order_id=str(uuid.uuid4()),
                        symbol=req.symbol,
                        status="rejected",
                        message="Bracket order requires take_profit_price and stop_price.",
                    )

                order_request = MarketOrderRequest(
                    symbol=req.symbol,
                    qty=req.qty,
                    side=_map_side(req.side),
                    time_in_force=_map_tif(req.time_in_force),
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=tp_price),
                    stop_loss=StopLossRequest(stop_price=sl_price),
                )
            else:
                # Plain market order
                order_request = MarketOrderRequest(
                    symbol=req.symbol,
                    qty=req.qty,
                    side=_map_side(req.side),
                    time_in_force=_map_tif(req.time_in_force),
                )

            if req.client_order_id:
                order_request.client_order_id = req.client_order_id

            order = await asyncio.to_thread(client.submit_order, order_request)

            status = str(order.status.value) if hasattr(order.status, "value") else str(order.status)
            return OrderResult(
                broker_order_id=str(order.id),
                symbol=order.symbol,
                status=status,
                filled_qty=float(order.filled_qty or 0),
                filled_avg_price=float(order.filled_avg_price or 0),
            )

        except APIError as exc:
            log.warning("[alpaca_broker] Order rejected for %s: %s", req.symbol, exc)
            return OrderResult(
                broker_order_id=str(uuid.uuid4()),
                symbol=req.symbol,
                status="rejected",
                message=str(exc),
            )

    async def cancel_order(self, broker_order_id: str) -> bool:
        self._require_configured()
        client = self._get_client()
        try:
            await asyncio.to_thread(client.cancel_order_by_id, broker_order_id)
            return True
        except APIError as exc:
            log.warning("[alpaca_broker] cancel_order(%s) failed: %s", broker_order_id, exc)
            return False

    async def close_position(self, symbol: str) -> OrderResult:
        self._require_configured()
        client = self._get_client()
        try:
            order = await asyncio.to_thread(client.close_position, symbol)
            status = str(order.status.value) if hasattr(order.status, "value") else str(order.status)
            return OrderResult(
                broker_order_id=str(order.id),
                symbol=order.symbol,
                status=status,
                filled_qty=float(order.filled_qty or 0),
                filled_avg_price=float(order.filled_avg_price or 0),
            )
        except APIError as exc:
            log.warning("[alpaca_broker] close_position(%s) failed: %s", symbol, exc)
            return OrderResult(
                broker_order_id=str(uuid.uuid4()),
                symbol=symbol,
                status="rejected",
                message=str(exc),
            )

    async def is_market_open(self) -> bool:
        self._require_configured()
        client = self._get_client()
        clock = await asyncio.to_thread(client.get_clock)
        return bool(clock.is_open)
