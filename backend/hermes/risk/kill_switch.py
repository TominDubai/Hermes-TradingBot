"""
KillSwitch — monitors daily P&L and halts all new orders if the account
loses more than 2% in a single session.

Typical usage with APScheduler:

    kill_switch = KillSwitch(broker=broker, config=settings, alerter=alerter)

    scheduler.add_job(
        kill_switch.check,
        trigger="interval",
        minutes=5,
        id="kill_switch_check",
    )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from hermes.events.bus import bus
from hermes.events.types import KillSwitchTripped, Portfolio
from hermes.execution.base import ExecutionBroker

if TYPE_CHECKING:
    from hermes.config import Settings
    from hermes.telegram.alerts import TelegramAlerter

logger = logging.getLogger(__name__)

DAILY_LOSS_THRESHOLD_PCT: float = -2.0


class KillSwitch:
    """
    Checks daily P&L on every call to :meth:`check`.
    When the account drops >= 2% intraday, it:
      - Sets hermes_halted=True on the config object
      - Publishes a KillSwitchTripped event for each portfolio
      - Sends a Telegram alert
      - Logs at CRITICAL level

    Call :meth:`reset` (e.g., from /api/settings/resume) to re-enable trading.
    """

    def __init__(
        self,
        broker: ExecutionBroker,
        config: Settings,
        alerter: TelegramAlerter | None = None,
    ) -> None:
        self._broker = broker
        self._config = config
        self._alerter = alerter
        self._halted: bool = False

    # ── Core check ──────────────────────────────────────────────────────────

    async def check(self) -> None:
        """
        Fetch current account state and trip the kill switch if the daily P&L
        has crossed the -2% threshold.  Safe to call repeatedly — once halted
        it becomes a no-op until :meth:`reset` is called.
        """
        if self._halted:
            return

        account = await self._broker.get_account()
        pnl_pct = account.today_pnl_pct

        if pnl_pct < DAILY_LOSS_THRESHOLD_PCT:
            await self._trip(pnl_pct)

    # ── State ────────────────────────────────────────────────────────────────

    def is_halted(self) -> bool:
        """Return True if the kill switch has been tripped and not yet reset."""
        return self._halted

    def reset(self) -> None:
        """
        Re-enable trading after manual review.
        Clears the in-memory flag and the config flag.
        """
        self._halted = False
        self._config.hermes_halted = False
        logger.info("Kill switch reset — trading re-enabled")

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _trip(self, pnl_pct: float) -> None:
        """Halt trading and notify all channels."""
        self._halted = True
        self._config.hermes_halted = True

        logger.critical("Kill switch tripped: daily PnL = %.2f%%", pnl_pct)

        # Publish one event per portfolio so downstream handlers can act per-portfolio
        for portfolio in Portfolio:
            event = KillSwitchTripped(
                portfolio=portfolio,
                daily_pnl=pnl_pct,
                threshold=DAILY_LOSS_THRESHOLD_PCT,
                reason=f"Daily P&L {pnl_pct:.2f}% breached {DAILY_LOSS_THRESHOLD_PCT}% threshold",
            )
            await bus.publish(event)

        # Telegram alert (best-effort — don't let a network error block the halt)
        if self._alerter is not None:
            try:
                await self._alerter.send_kill_switch(pnl_pct)
            except Exception:
                logger.exception("Failed to send kill-switch Telegram alert")
