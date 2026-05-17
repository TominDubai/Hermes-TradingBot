from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hermes.api.performance import router as performance_router
from hermes.config import settings
from hermes.events.bus import bus
from hermes.outcome.tracker import run_outcome_check

logger = logging.getLogger(__name__)

# ── Scheduler jobs ────────────────────────────────────────────

async def _run_long_scan() -> None:
    from hermes.scanners.base import LongScanner
    await LongScanner().run_once()

async def _run_mid_scan() -> None:
    from hermes.scanners.base import MidScanner
    await MidScanner().run_once()

async def _run_intra_scan() -> None:
    if settings.hermes_halted:
        logger.info("Halted — skipping intra scan")
        return
    from hermes.scanners.base import IntraScanner
    await IntraScanner().run_once()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Hermes starting up (env=%s)", settings.hermes_env)

    # Event bus
    bus_task = asyncio.create_task(bus.run(), name="event-bus")

    # Scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Long: weekly Sunday 18:00 UTC
    scheduler.add_job(_run_long_scan, CronTrigger(day_of_week="sun", hour=18, minute=0))
    # Mid: weekdays at market close per region (start with US close 21:00 UTC)
    scheduler.add_job(_run_mid_scan, CronTrigger(day_of_week="mon-fri", hour=21, minute=15))
    # Intra: every 15 min, US market hours Mon-Fri 13:30-20:00 UTC
    scheduler.add_job(
        _run_intra_scan,
        IntervalTrigger(minutes=15),
        id="intra_scan",
    )
    # Outcome tracker: every hour
    scheduler.add_job(run_outcome_check, IntervalTrigger(hours=1), id="outcome_tracker")

    if settings.hermes_env != "test":
        scheduler.start()
        logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    logger.info(
        "Alpaca: %s | Telegram: %s | Halted: %s",
        "configured" if settings.alpaca_configured else "not configured",
        "configured" if settings.telegram_configured else "not configured",
        settings.hermes_halted,
    )

    yield

    scheduler.shutdown(wait=False)
    await bus.stop()
    bus_task.cancel()
    with suppress(asyncio.CancelledError):
        await bus_task
    logger.info("Hermes shut down cleanly")


app = FastAPI(
    title="Hermes Trading Bot",
    version="0.1.0",
    description="Three-portfolio algorithmic trading system",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(performance_router)


# ── Routes ────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health() -> dict:
    return {
        "status": "ok",
        "version": "0.1.0",
        "env": settings.hermes_env,
        "halted": settings.hermes_halted,
        "alpaca_configured": settings.alpaca_configured,
        "telegram_configured": settings.telegram_configured,
    }


@app.get("/api/status", tags=["system"])
async def status() -> dict:
    return {
        "phase": "2 — Indicators + Setups + Scoring",
        "message": "Scanners wired. Three portfolios: long (weekly), mid (daily), intra (15 min).",
    }


@app.post("/api/scan/{portfolio}", tags=["scanner"])
async def trigger_scan(portfolio: str) -> dict:
    """Manually trigger a scan for testing (dev only)."""
    if portfolio == "long":
        asyncio.create_task(_run_long_scan())
    elif portfolio == "mid":
        asyncio.create_task(_run_mid_scan())
    elif portfolio == "intra":
        asyncio.create_task(_run_intra_scan())
    else:
        return {"error": f"Unknown portfolio: {portfolio}"}
    return {"status": "scan_triggered", "portfolio": portfolio}


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=settings.hermes_log_level)
    uvicorn.run(
        "hermes.main:app",
        host="0.0.0.0",
        port=settings.hermes_port,
        reload=settings.hermes_env == "development",
        log_level=settings.hermes_log_level.lower(),
    )
