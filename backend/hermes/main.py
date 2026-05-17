from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hermes.config import settings
from hermes.events.bus import bus

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info("Hermes starting up (env=%s)", settings.hermes_env)

    # Start the event bus dispatcher in the background
    bus_task = asyncio.create_task(bus.run(), name="event-bus")

    logger.info(
        "Alpaca: %s | Telegram: %s | Halted: %s",
        "configured" if settings.alpaca_configured else "not configured",
        "configured" if settings.telegram_configured else "not configured",
        settings.hermes_halted,
    )

    yield

    # Graceful shutdown
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
        "phase": "0 — Bootstrap",
        "message": "Hermes is alive. No scanners running yet.",
    }


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
