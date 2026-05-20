"""
Position monitor — polls open positions every 5 minutes and closes them
when stop or target is hit.

Runs as an APScheduler job. Handles both:
- Alpaca positions (US stocks via AlpacaBroker)
- PaperTracker positions (EU/UK/HK/JP virtual positions)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from hermes.events.bus import bus
from hermes.events.types import OutcomeRecorded, OutcomeStatus, Portfolio

logger = logging.getLogger(__name__)


async def run_position_monitor() -> None:
    """Check all open positions and close those that hit stop or target."""
    await _monitor_alpaca()
    await _monitor_paper_tracker()


async def _monitor_alpaca() -> None:
    """Check Alpaca paper positions against current prices."""
    from hermes.config import settings
    if not settings.alpaca_configured:
        return

    try:
        from hermes.execution.alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()

        if not await broker.is_market_open():
            return

        positions = await broker.get_positions()
        if not positions:
            return

        for pos in positions:
            symbol = pos.symbol
            current = pos.current_price
            entry = pos.avg_entry_price

            # Look up stop/target from our in-memory signal store
            stop, target = _get_levels(symbol)
            if stop is None or target is None:
                # No levels tracked — use simple trailing approach
                # Stop at -3% from entry, target at +6%
                stop = entry * 0.97
                target = entry * 1.06

            outcome = None
            if current <= stop:
                outcome = OutcomeStatus.LOSS
            elif current >= target:
                outcome = OutcomeStatus.WIN

            if outcome:
                logger.info(
                    "PositionMonitor: closing %s at %.4f (entry=%.4f) outcome=%s",
                    symbol, current, entry, outcome.value,
                )
                result = await broker.close_position(symbol)
                if result.status in ("filled", "accepted"):
                    pnl_pct = (current - entry) / entry * 100
                    await bus.publish(OutcomeRecorded(
                        signal_id=_get_signal_id(symbol),  # type: ignore[arg-type]
                        symbol=symbol,
                        portfolio=Portfolio.MID,  # best guess — improve with tracking
                        status=outcome,
                        entry_price=entry,
                        exit_price=current,
                        realised_pnl=round(pnl_pct, 4),
                        hold_duration_hours=0,
                    ))

                    # Send Telegram alert
                    from hermes.config import settings as cfg
                    from hermes.telegram.alerts import TelegramAlerter
                    alerter = TelegramAlerter(
                        token=cfg.hermes_telegram_bot_token,
                        chat_id=cfg.hermes_telegram_chat_id,
                    )
                    await alerter.send_position_closed(symbol, pnl_pct, outcome.value)

    except Exception:
        logger.exception("PositionMonitor: Alpaca check failed")


async def _monitor_paper_tracker() -> None:
    """Check PaperTracker virtual positions against current prices."""
    try:
        from hermes.execution.paper_tracker import paper_tracker
        if paper_tracker.open_count == 0:
            return

        positions = await paper_tracker.get_positions()
        if not positions:
            return

        # Fetch current prices for all open symbols
        symbols = [p.symbol for p in positions]
        prices = await _fetch_prices(symbols)

        for pos in positions:
            symbol = pos.symbol
            current = prices.get(symbol)
            if current is None:
                continue

            # Update price in tracker
            paper_tracker.update_price(symbol, current)

            entry = pos.avg_entry_price
            stop, target = _get_levels(symbol)
            if stop is None:
                stop = entry * 0.97
            if target is None:
                target = entry * 1.06

            outcome = None
            if current <= stop:
                outcome = OutcomeStatus.LOSS
            elif current >= target:
                outcome = OutcomeStatus.WIN

            if outcome:
                logger.info(
                    "PositionMonitor: closing virtual %s at %.4f outcome=%s",
                    symbol, current, outcome.value,
                )
                await paper_tracker.close_position(symbol)
                pnl_pct = (current - entry) / entry * 100
                await bus.publish(OutcomeRecorded(
                    signal_id=_get_signal_id(symbol),  # type: ignore[arg-type]
                    symbol=symbol,
                    portfolio=Portfolio.MID,
                    status=outcome,
                    entry_price=entry,
                    exit_price=current,
                    realised_pnl=round(pnl_pct, 4),
                    hold_duration_hours=0,
                ))

    except Exception:
        logger.exception("PositionMonitor: PaperTracker check failed")


async def _fetch_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch latest prices for a list of symbols using intraday data."""
    import asyncio
    import yfinance as yf

    prices: dict[str, float] = {}

    async def fetch_one(sym: str) -> None:
        try:
            # Use yfinance fast_info for real-time last price
            ticker = await asyncio.to_thread(lambda: yf.Ticker(sym))
            info = await asyncio.to_thread(lambda: ticker.fast_info)
            price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
            if price and price > 0:
                prices[sym] = float(price)
        except Exception:
            pass

    await asyncio.gather(*[fetch_one(s) for s in symbols])
    return prices


def _get_levels(symbol: str) -> tuple[float | None, float | None]:
    """Look up stop/target from the in-memory signal store."""
    try:
        from hermes.api.signals import _signals
        for sig in _signals:
            if sig["symbol"] == symbol and sig["outcome"] is None:
                return sig.get("stop_price"), sig.get("target_price")
    except Exception:
        pass
    return None, None


def _get_signal_id(symbol: str):
    """Get the UUID of the most recent open signal for a symbol."""
    import uuid
    try:
        from hermes.api.signals import _signals
        for sig in _signals:
            if sig["symbol"] == symbol and sig["outcome"] is None:
                return uuid.UUID(sig["id"])
    except Exception:
        pass
    return uuid.uuid4()
