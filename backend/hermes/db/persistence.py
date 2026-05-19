"""
Signal persistence layer — writes signals and outcomes to Postgres.
Subscribed to the event bus at startup.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from hermes.db.models import Signal
from hermes.db.session import AsyncSessionFactory
from hermes.events.bus import bus
from hermes.events.types import OutcomeRecorded, SignalScored

logger = logging.getLogger(__name__)


def setup_db_subscribers() -> None:
    """Register event bus subscribers that persist to DB."""

    @bus.subscribe(SignalScored)
    async def persist_signal(event) -> None:  # type: ignore[type-arg]
        """Persist every scored signal to the signals table."""
        try:
            async with AsyncSessionFactory() as session:
                signal = Signal(
                    event_id=str(event.signal_id),
                    symbol=event.symbol,
                    portfolio=event.portfolio.value,
                    direction=event.direction.value,
                    setup_name=event.setup_name,
                    timeframe="1d",
                    confluence_score=event.confluence_score,
                    entry_price=event.entry_price,
                    stop_price=event.stop_price,
                    target_price=event.target_price,
                    features_json=json.dumps(event.features) if event.features else None,
                    detected_at=datetime.now(timezone.utc),
                )
                session.add(signal)
                await session.commit()
                logger.debug("DB: persisted signal %s %s", event.symbol, event.setup_name)
        except Exception:
            logger.exception("DB: failed to persist signal %s", event.symbol)

    @bus.subscribe(OutcomeRecorded)
    async def persist_outcome(event) -> None:  # type: ignore[type-arg]
        """Update signal row with outcome when position closes."""
        try:
            from sqlalchemy import select
            async with AsyncSessionFactory() as session:
                stmt = select(Signal).where(Signal.event_id == str(event.signal_id))
                result = await session.execute(stmt)
                signal = result.scalar_one_or_none()
                if signal:
                    signal.outcome = event.status.value
                    signal.outcome_at = datetime.now(timezone.utc)
                    signal.realised_pnl = event.realised_pnl
                    await session.commit()
                    logger.info(
                        "DB: outcome %s for %s pnl=%.2f%%",
                        event.status.value, event.symbol, event.realised_pnl,
                    )
        except Exception:
            logger.exception("DB: failed to persist outcome %s", event.signal_id)


async def load_recent_signals(limit: int = 500) -> list[dict]:
    """Load recent signals from DB for in-memory cache on startup."""
    try:
        from sqlalchemy import select, desc
        async with AsyncSessionFactory() as session:
            stmt = select(Signal).order_by(desc(Signal.detected_at)).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            signals = []
            for r in rows:
                signals.append({
                    "id": r.event_id,
                    "type": "signal_detected",
                    "symbol": r.symbol,
                    "portfolio": r.portfolio,
                    "direction": r.direction,
                    "setup_name": r.setup_name,
                    "timeframe": r.timeframe,
                    "raw_score": 0.7,
                    "confluence_score": r.confluence_score,
                    "entry_price": r.entry_price,
                    "stop_price": r.stop_price,
                    "target_price": r.target_price,
                    "rr_ratio": round(
                        abs(r.target_price - r.entry_price) /
                        max(abs(r.entry_price - r.stop_price), 0.0001), 2
                    ),
                    "detected_at": r.detected_at.isoformat() if r.detected_at else None,
                    "outcome": r.outcome,
                    "features": json.loads(r.features_json) if r.features_json else {},
                })
            logger.info("DB: loaded %d signals from database", len(signals))
            return signals
    except Exception:
        logger.exception("DB: failed to load signals")
        return []
