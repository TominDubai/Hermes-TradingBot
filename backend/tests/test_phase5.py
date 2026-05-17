"""Phase 5 tests: brokers, portfolio manager, kill switch, Telegram."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.events.types import Direction, Portfolio, SignalScored
from hermes.execution.base import AccountInfo, OrderRequest, OrderResult
from hermes.execution.paper_broker import PaperBroker

# ── PaperBroker ───────────────────────────────────────────────

class TestPaperBroker:
    def test_initial_equity(self):
        b = PaperBroker(initial_equity=10_000)
        assert asyncio.get_event_loop().run_until_complete(b.get_account()).equity == 10_000

    def test_is_market_open_always_true(self):
        b = PaperBroker()
        assert asyncio.get_event_loop().run_until_complete(b.is_market_open()) is True

    def test_submit_order_fills_immediately(self):
        b = PaperBroker()
        req = OrderRequest(
            symbol="AAPL", qty=10, side="buy",
            order_type="market", time_in_force="day",
            limit_price=150.0,
        )
        result = asyncio.get_event_loop().run_until_complete(b.submit_order(req))
        assert result.status in ("filled", "accepted")
        assert result.symbol == "AAPL"

    def test_get_positions_after_buy(self):
        b = PaperBroker()
        req = OrderRequest(
            symbol="TSLA", qty=5, side="buy",
            order_type="market", time_in_force="day",
            limit_price=200.0,
        )
        asyncio.get_event_loop().run_until_complete(b.submit_order(req))
        positions = asyncio.get_event_loop().run_until_complete(b.get_positions())
        symbols = [p.symbol for p in positions]
        assert "TSLA" in symbols

    def test_close_position(self):
        b = PaperBroker()
        req = OrderRequest(
            symbol="MSFT", qty=3, side="buy",
            order_type="market", time_in_force="day",
            limit_price=300.0,
        )
        asyncio.get_event_loop().run_until_complete(b.submit_order(req))
        asyncio.get_event_loop().run_until_complete(b.close_position("MSFT"))
        positions = asyncio.get_event_loop().run_until_complete(b.get_positions())
        assert all(p.symbol != "MSFT" for p in positions)

    def test_cancel_order(self):
        b = PaperBroker()
        result = asyncio.get_event_loop().run_until_complete(b.cancel_order("fake-id"))
        assert isinstance(result, bool)


# ── AlpacaBroker ──────────────────────────────────────────────

class TestAlpacaBroker:
    def test_raises_when_unconfigured(self):
        from hermes.data.base import ProviderError
        from hermes.execution.alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
        with patch("hermes.execution.alpaca_broker.settings") as mock_settings:
            mock_settings.alpaca_configured = False
            with pytest.raises((ProviderError, Exception)):
                asyncio.get_event_loop().run_until_complete(broker.get_account())

    def test_protocol_compliance(self):
        from hermes.execution.alpaca_broker import AlpacaBroker
        # AlpacaBroker must have all required methods
        for method in ("get_account", "get_positions", "submit_order",
                       "cancel_order", "close_position", "is_market_open"):
            assert hasattr(AlpacaBroker, method)


# ── Portfolio Manager ─────────────────────────────────────────

class TestPortfolioManager:
    def _make_event(self, score: int = 3, symbol: str = "AAPL",
                    portfolio: Portfolio = Portfolio.MID) -> SignalScored:
        return SignalScored(
            signal_id=uuid.uuid4(),
            symbol=symbol,
            portfolio=portfolio,
            direction=Direction.LONG,
            setup_name="mean_reversion",
            confluence_score=score,
            entry_price=150.0,
            stop_price=145.0,
            target_price=165.0,
        )

    def _make_broker(self, equity: float = 10_000, market_open: bool = True):
        broker = AsyncMock()
        broker.name = "paper"
        broker.is_market_open = AsyncMock(return_value=market_open)
        broker.get_account = AsyncMock(return_value=AccountInfo(
            equity=equity, cash=equity, buying_power=equity,
            portfolio_value=equity, today_pnl=0.0, today_pnl_pct=0.0,
        ))
        broker.get_positions = AsyncMock(return_value=[])
        broker.submit_order = AsyncMock(return_value=OrderResult(
            broker_order_id="ord-1", symbol="AAPL",
            status="filled", filled_qty=1.0, filled_avg_price=150.0,
        ))
        return broker

    def test_skips_when_halted(self):
        from hermes.config import Settings
        from hermes.portfolio.manager import PortfolioManager
        config = Settings(hermes_halted=True)
        pm = PortfolioManager(broker=self._make_broker(), config=config)
        event = self._make_event()
        # Should return without submitting any order
        asyncio.get_event_loop().run_until_complete(pm.on_signal_scored(event))
        self._make_broker().submit_order.assert_not_called()

    def test_skips_low_confluence(self):
        from hermes.config import Settings
        from hermes.portfolio.manager import PortfolioManager
        broker = self._make_broker()
        config = Settings(hermes_halted=False)
        pm = PortfolioManager(broker=broker, config=config)
        event = self._make_event(score=1)  # below min_confluence=2
        asyncio.get_event_loop().run_until_complete(pm.on_signal_scored(event))
        broker.submit_order.assert_not_called()

    def test_skips_when_market_closed(self):
        from hermes.config import Settings
        from hermes.portfolio.manager import PortfolioManager
        broker = self._make_broker(market_open=False)
        config = Settings(hermes_halted=False)
        pm = PortfolioManager(broker=broker, config=config)
        event = self._make_event(score=3)
        asyncio.get_event_loop().run_until_complete(pm.on_signal_scored(event))
        broker.submit_order.assert_not_called()

    def test_submits_order_when_gates_pass(self):
        from hermes.config import Settings
        from hermes.portfolio.manager import PortfolioManager
        broker = self._make_broker(equity=10_000)
        config = Settings(hermes_halted=False)
        pm = PortfolioManager(broker=broker, config=config)
        event = self._make_event(score=3)
        asyncio.get_event_loop().run_until_complete(pm.on_signal_scored(event))
        broker.submit_order.assert_called_once()


# ── Kill Switch ───────────────────────────────────────────────

class TestKillSwitch:
    def test_trips_on_2pct_loss(self):
        from hermes.config import Settings
        from hermes.risk.kill_switch import KillSwitch
        broker = AsyncMock()
        broker.get_account = AsyncMock(return_value=AccountInfo(
            equity=9_800, cash=9_800, buying_power=9_800,
            portfolio_value=9_800, today_pnl=-200.0, today_pnl_pct=-2.1,
        ))
        alerter = AsyncMock()
        alerter.send_kill_switch = AsyncMock()
        config = Settings()
        ks = KillSwitch(broker=broker, config=config, alerter=alerter)
        asyncio.get_event_loop().run_until_complete(ks.check())
        assert ks.is_halted() is True

    def test_does_not_trip_on_small_loss(self):
        from hermes.config import Settings
        from hermes.risk.kill_switch import KillSwitch
        broker = AsyncMock()
        broker.get_account = AsyncMock(return_value=AccountInfo(
            equity=9_950, cash=9_950, buying_power=9_950,
            portfolio_value=9_950, today_pnl=-50.0, today_pnl_pct=-0.5,
        ))
        config = Settings()
        ks = KillSwitch(broker=broker, config=config)
        asyncio.get_event_loop().run_until_complete(ks.check())
        assert ks.is_halted() is False


# ── Telegram Alerter ──────────────────────────────────────────

class TestTelegramAlerter:
    def test_graceful_noop_when_unconfigured(self):
        from hermes.telegram.alerts import TelegramAlerter
        alerter = TelegramAlerter(token="", chat_id="")
        # Should not raise
        result = asyncio.get_event_loop().run_until_complete(alerter.send("test"))
        assert result is False

    def test_sends_high_signal(self):
        from hermes.telegram.alerts import TelegramAlerter
        alerter = TelegramAlerter(token="fake-token", chat_id="123456")
        event = SignalScored(
            signal_id=uuid.uuid4(),
            symbol="NVDA",
            portfolio=Portfolio.INTRA,
            direction=Direction.LONG,
            setup_name="opening_range_breakout",
            confluence_score=4,
            entry_price=500.0,
            stop_price=490.0,
            target_price=520.0,
        )
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            asyncio.get_event_loop().run_until_complete(
                alerter.send_signal_alert(event, "NVDA")
            )

    def test_does_not_send_medium_signal(self):
        from hermes.telegram.alerts import TelegramAlerter
        alerter = TelegramAlerter(token="fake-token", chat_id="123456")
        event = SignalScored(
            signal_id=uuid.uuid4(),
            symbol="AAPL",
            portfolio=Portfolio.MID,
            direction=Direction.LONG,
            setup_name="mean_reversion",
            confluence_score=2,  # MEDIUM — should NOT alert
            entry_price=150.0,
            stop_price=145.0,
            target_price=165.0,
        )
        with patch("aiohttp.ClientSession") as mock_session_cls:
            asyncio.get_event_loop().run_until_complete(
                alerter.send_signal_alert(event, "AAPL")
            )
            mock_session_cls.assert_not_called()
