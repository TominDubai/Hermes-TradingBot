"""
TelegramAlerter — sends structured alert messages to a Telegram chat
via the Bot API using aiohttp.

If token or chat_id are empty the alerter is a graceful no-op; it logs a
warning and returns without raising.  One retry is attempted on transient
network errors to avoid spamming the API on persistent failures.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from hermes.events.types import SignalScored

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramAlerter:
    """
    Thin async wrapper around the Telegram Bot sendMessage API.

    Parameters
    ----------
    token:    Bot token from BotFather (e.g. "123456:ABC-DEF...")
    chat_id:  Target chat / channel ID as a string (e.g. "-1001234567890")
    """

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    # ── Low-level send ───────────────────────────────────────────────────────

    async def send(self, message: str) -> bool:
        """
        Send a plain-text message.

        Returns True if the message was delivered, False otherwise.
        Retries once on network errors; does not raise.
        """
        if not self._token or not self._chat_id:
            logger.warning(
                "TelegramAlerter: token or chat_id not configured — skipping send"
            )
            return False

        url = _BASE_URL.format(token=self._token)
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        for attempt in range(2):  # one retry
            try:
                async with aiohttp.ClientSession() as session:  # noqa: SIM117
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            return True
                        body = await resp.text()
                        logger.warning(
                            "Telegram API error status=%d body=%s", resp.status, body
                        )
                        return False  # don't retry on API-level errors
            except aiohttp.ClientError as exc:
                if attempt == 0:
                    logger.warning("Telegram network error (will retry): %s", exc)
                else:
                    logger.error("Telegram network error (retry failed): %s", exc)
            except Exception as exc:
                logger.error("Unexpected error sending Telegram message: %s", exc)
                return False

        return False

    # ── Structured alerts ────────────────────────────────────────────────────

    async def send_signal_alert(self, event: SignalScored, symbol: str) -> None:
        """
        Send a HIGH CONFIDENCE SIGNAL alert.
        Only fires when confluence_score >= 3; silently skips lower scores.
        """
        if event.confluence_score < 3:
            return

        entry = event.entry_price
        stop = event.stop_price
        target = event.target_price

        risk = abs(entry - stop)
        rr = abs(target - entry) / risk if risk else 0.0

        message = (
            f"🚨 <b>HIGH CONFIDENCE SIGNAL</b>\n"
            f"{symbol} • {event.portfolio} • {event.direction.upper()}\n"
            f"Setup: {event.setup_name}\n"
            f"Score: {event.confluence_score}/6\n"
            f"Entry: ${entry:.2f} | Stop: ${stop:.2f} | Target: ${target:.2f}\n"
            f"R:R: {rr:.1f}:1"
        )
        await self.send(message)

    async def send_position_opened(
        self,
        symbol: str,
        qty: float,
        price: float,
        portfolio: str,
    ) -> None:
        """Alert when a new position is opened."""
        message = (
            f"📈 Position opened: {symbol} x{qty} @ ${price:.2f} [{portfolio}]"
        )
        await self.send(message)

    async def send_position_closed(
        self,
        symbol: str,
        pnl_pct: float,
        outcome: str,
    ) -> None:
        """
        Alert when a position is closed.

        outcome should be one of: "WIN", "LOSS", "EXPIRED"
        """
        outcome_upper = outcome.upper()
        if outcome_upper == "WIN":
            icon = "✅"
        elif outcome_upper == "LOSS":
            icon = "❌"
        else:
            icon = "⏰"

        message = (
            f"{icon} {symbol} closed {outcome_upper}: {pnl_pct:+.2f}%"
        )
        await self.send(message)

    async def send_kill_switch(self, daily_pnl_pct: float) -> None:
        """Alert when the daily loss kill-switch is tripped."""
        message = (
            f"🛑 <b>KILL SWITCH TRIPPED</b>\n"
            f"Daily P&amp;L: {daily_pnl_pct:.2f}%\n"
            f"All new orders halted. Resume via dashboard or /api/settings/resume"
        )
        await self.send(message)

    async def send_daily_summary(self, portfolios: list[dict]) -> None:
        """
        Send end-of-day portfolio summary.

        Each dict in `portfolios` should have:
            name (str), open_positions (int), today_pnl (float), equity (float)
        """
        lines = ["📊 <b>HERMES DAILY SUMMARY</b>\n"]
        total_equity = 0.0

        for p in portfolios:
            name = p.get("name", "?")
            open_pos = p.get("open_positions", 0)
            today_pnl = p.get("today_pnl", 0.0)
            equity = p.get("equity", 0.0)
            total_equity += equity
            sign = "+" if today_pnl >= 0 else ""
            lines.append(
                f"<b>{name}</b>: {open_pos} open | P&amp;L today: {sign}{today_pnl:.2f}%"
            )

        lines.append(f"\nTotal equity: ${total_equity:,.2f}")
        await self.send("\n".join(lines))
