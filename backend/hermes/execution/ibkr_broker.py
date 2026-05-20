"""
IBKRBroker — executes orders on Interactive Brokers via ib_async.
Handles EU/UK/HK/JP stocks that Alpaca can't trade.

Connects to IB Gateway running on Goshi PC via Tailscale forwarder.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from hermes.config import settings
from hermes.data.base import ProviderError
from hermes.execution.base import (
    AccountInfo,
    ExecutionBroker,
    OrderRequest,
    OrderResult,
    Position,
)

logger = logging.getLogger(__name__)


def _symbol_to_ib_contract(symbol: str):
    """Convert our symbol format to an ib_async Contract."""
    from ib_async import Contract, Stock, Forex

    symbol = symbol.upper()

    # Exchange suffix mapping
    exchange_map = {
        ".L":   ("LSE",   "GBp"),   # London Stock Exchange (pence)
        ".PA":  ("ENEXT.BE", "EUR"), # Euronext Paris
        ".DE":  ("XETRA", "EUR"),    # Deutsche Boerse Xetra
        ".AS":  ("ENEXT.BE", "EUR"), # Euronext Amsterdam
        ".MI":  ("BVME",  "EUR"),    # Borsa Italiana
        ".HK":  ("SEHK",  "HKD"),   # Hong Kong Stock Exchange
        ".T":   ("TSEJ",  "JPY"),    # Tokyo Stock Exchange
    }

    for suffix, (exchange, currency) in exchange_map.items():
        if symbol.endswith(suffix):
            ticker = symbol[:-len(suffix)]
            return Stock(ticker, exchange, currency)

    # Default: US stock on SMART router
    return Stock(symbol, "SMART", "USD")


class IBKRBroker:
    """
    Execution broker using Interactive Brokers via ib_async.
    Routes EU/UK/HK/JP orders through IB Gateway.
    """

    name = "ibkr_paper"

    def __init__(self) -> None:
        self._ib = None
        self._connected = False

    async def _get_ib(self):
        """Lazy connect to IB Gateway."""
        from ib_async import IB
        if self._ib is None or not self._ib.isConnected():
            self._ib = IB()
            # Use clientId+1 to avoid collision with any persistent connection
            client_id = settings.ibkr_client_id + 1
            try:
                await self._ib.connectAsync(
                    host=settings.ibkr_host,
                    port=settings.ibkr_port,
                    clientId=client_id,
                    timeout=15,
                )
                self._connected = True
                logger.info("IBKRBroker: connected to IB Gateway at %s:%d (clientId=%d)",
                            settings.ibkr_host, settings.ibkr_port, client_id)
            except Exception as e:
                self._connected = False
                raise ProviderError("ibkr", "connection", str(e)) from e
        return self._ib

    async def get_account(self) -> AccountInfo:
        ib = await self._get_ib()
        account = settings.ibkr_account or (ib.managedAccounts()[0] if ib.managedAccounts() else "")
        summary = await ib.reqAccountSummaryAsync()

        def get_val(tag: str) -> float:
            for item in summary:
                if item.tag == tag and item.currency == "USD":
                    try:
                        return float(item.value)
                    except (ValueError, TypeError):
                        pass
            return 0.0

        equity = get_val("NetLiquidation")
        cash = get_val("TotalCashValue")
        buying_power = get_val("BuyingPower")

        return AccountInfo(
            equity=equity,
            cash=cash,
            buying_power=buying_power,
            portfolio_value=equity,
            today_pnl=0.0,
            today_pnl_pct=0.0,
        )

    async def get_positions(self) -> list[Position]:
        ib = await self._get_ib()
        ib_positions = await ib.reqPositionsAsync()
        positions = []
        for pos in ib_positions:
            if pos.position == 0:
                continue
            try:
                ticker = await asyncio.wait_for(
                    ib.reqMktDataAsync(pos.contract, "", True, False),
                    timeout=5,
                )
                current = float(ticker.last or ticker.close or pos.avgCost)
            except Exception:
                current = float(pos.avgCost)

            positions.append(Position(
                symbol=pos.contract.symbol,
                qty=float(abs(pos.position)),
                avg_entry_price=float(pos.avgCost),
                current_price=current,
                unrealised_pnl=(current - float(pos.avgCost)) * float(pos.position),
                side="long" if pos.position > 0 else "short",
            ))
        return positions

    async def submit_order(self, req: OrderRequest) -> OrderResult:
        from ib_async import MarketOrder, LimitOrder, BracketOrder
        ib = await self._get_ib()
        contract = _symbol_to_ib_contract(req.symbol)

        try:
            if req.order_type == "bracket" and req.stop_price and req.take_profit_price:
                qty = req.qty
                bracket = ib.bracketOrder(
                    req.side.upper(),
                    qty,
                    req.limit_price or 0,  # 0 = market
                    req.take_profit_price,
                    req.stop_price,
                )
                trades = []
                for order in bracket:
                    trade = ib.placeOrder(contract, order)
                    trades.append(trade)
                await asyncio.sleep(1)  # give IB a moment
                parent_trade = trades[0]
                return OrderResult(
                    broker_order_id=str(parent_trade.order.orderId),
                    symbol=req.symbol,
                    status="accepted",
                    filled_qty=0.0,
                    filled_avg_price=0.0,
                    message="Bracket order submitted to IBKR",
                )
            else:
                order = MarketOrder(req.side.upper(), req.qty)
                trade = ib.placeOrder(contract, order)
                await asyncio.sleep(1)
                return OrderResult(
                    broker_order_id=str(trade.order.orderId),
                    symbol=req.symbol,
                    status="accepted",
                    message="Market order submitted to IBKR",
                )
        except Exception as e:
            logger.exception("IBKRBroker: order failed for %s", req.symbol)
            return OrderResult(
                broker_order_id="",
                symbol=req.symbol,
                status="rejected",
                message=str(e),
            )

    async def cancel_order(self, broker_order_id: str) -> bool:
        try:
            from ib_async import Order
            ib = await self._get_ib()
            order = Order()
            order.orderId = int(broker_order_id)
            ib.cancelOrder(order)
            return True
        except Exception:
            return False

    async def close_position(self, symbol: str) -> OrderResult:
        from ib_async import MarketOrder
        try:
            ib = await self._get_ib()
            positions = await self.get_positions()
            for pos in positions:
                if pos.symbol == symbol:
                    contract = _symbol_to_ib_contract(symbol)
                    side = "SELL" if pos.side == "long" else "BUY"
                    order = MarketOrder(side, pos.qty)
                    trade = ib.placeOrder(contract, order)
                    return OrderResult(
                        broker_order_id=str(trade.order.orderId),
                        symbol=symbol,
                        status="accepted",
                    )
            return OrderResult(broker_order_id="", symbol=symbol,
                               status="rejected", message="Position not found")
        except Exception as e:
            return OrderResult(broker_order_id="", symbol=symbol,
                               status="rejected", message=str(e))

    async def is_market_open(self) -> bool:
        """IBKR handles global market hours — return True and let IB Gateway reject bad orders."""
        return True

    async def disconnect(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            self._connected = False


# Singleton — reuse one connection across the app
ibkr_broker = IBKRBroker()
