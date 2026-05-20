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
from hermes.api.portfolios import router as portfolios_router
from hermes.api.signals import router as signals_router
from hermes.config import settings
from hermes.events.bus import bus
from hermes.outcome.tracker import run_outcome_check
from hermes.outcome.position_monitor import run_position_monitor
from hermes.db.persistence import setup_db_subscribers, load_recent_signals

logger = logging.getLogger(__name__)

# ── Lazy singletons ───────────────────────────────────────────

def _get_broker():
    """
    Return the configured broker.
    Preference: IBKRBroker (global, all markets) → AlpacaBroker (US fallback) → PaperBroker
    """
    # Try IBKR first — handles all markets including US
    if settings.ibkr_configured:
        try:
            from hermes.execution.ibkr_broker import IBKRBroker
            return IBKRBroker()
        except Exception:
            logger.warning("IBKRBroker unavailable, falling back to Alpaca")

    # Alpaca fallback — US only
    if settings.alpaca_configured:
        from hermes.execution.alpaca_broker import AlpacaBroker
        return AlpacaBroker()

    # Last resort — in-memory paper broker
    from hermes.execution.paper_broker import PaperBroker
    return PaperBroker()

def _get_alerter():
    from hermes.telegram.alerts import TelegramAlerter
    return TelegramAlerter(
        token=settings.hermes_telegram_bot_token,
        chat_id=settings.hermes_telegram_chat_id,
    )

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


# ── Multi-market scanner jobs ─────────────────────────────────────────────

async def _run_long_eu_scan() -> None:
    from hermes.scanners.base import LongEUScanner
    await LongEUScanner().run_once()

async def _run_long_uk_scan() -> None:
    from hermes.scanners.base import LongUKScanner
    await LongUKScanner().run_once()

async def _run_long_hk_scan() -> None:
    from hermes.scanners.base import LongHKScanner
    await LongHKScanner().run_once()

async def _run_long_jp_scan() -> None:
    from hermes.scanners.base import LongJPScanner
    await LongJPScanner().run_once()

async def _run_mid_eu_scan() -> None:
    from hermes.scanners.base import MidEUScanner
    await MidEUScanner().run_once()

async def _run_mid_uk_scan() -> None:
    from hermes.scanners.base import MidUKScanner
    await MidUKScanner().run_once()

async def _run_intra_eu_scan() -> None:
    if settings.hermes_halted:
        logger.info("Halted - skipping intra EU scan")
        return
    from hermes.scanners.base import IntraEUScanner
    await IntraEUScanner().run_once()

async def _run_intra_uk_scan() -> None:
    if settings.hermes_halted:
        logger.info("Halted - skipping intra UK scan")
        return
    from hermes.scanners.base import IntraUKScanner
    await IntraUKScanner().run_once()

async def _run_kill_switch_check() -> None:
    from hermes.risk.kill_switch import KillSwitch
    broker = _get_broker()
    alerter = _get_alerter()
    await KillSwitch(broker=broker, config=settings, alerter=alerter).check()

async def _run_daily_summary() -> None:
    """Send daily summary Telegram message at market close."""
    alerter = _get_alerter()
    broker = _get_broker()
    try:
        account = await broker.get_account()
        positions = await broker.get_positions()
        await alerter.send_daily_summary([{
            "name": "All Portfolios",
            "open_positions": len(positions),
            "today_pnl": account.today_pnl,
            "equity": account.equity,
        }])
    except Exception:
        logger.exception("Daily summary failed")

# ── Event bus: wire scoring → portfolio manager ───────────────

def _setup_event_subscribers() -> None:
    """Wire SignalScored events to the portfolio manager."""
    from hermes.events.types import SignalDetected, SignalScored
    from hermes.portfolio.manager import PortfolioManager
    from hermes.scoring.rule_scorer import RuleScorer

    broker = _get_broker()
    alerter = _get_alerter()
    pm = PortfolioManager(broker=broker, config=settings)  # type: ignore[arg-type]
    RuleScorer()

    @bus.subscribe(SignalDetected)
    async def on_detected(event) -> None:  # type: ignore[type-arg]
        """Score every detected signal and publish SignalScored."""

        from hermes.events.types import SignalScored
        score = 2  # default — full scoring needs live df (Phase 6 enhancement)
        scored = SignalScored(
            signal_id=event.event_id,
            symbol=event.symbol,
            portfolio=event.portfolio,
            direction=event.direction,
            setup_name=event.setup_name,
            confluence_score=score,
            entry_price=event.entry_price,
            stop_price=event.stop_price,
            target_price=event.target_price,
            features=event.features,
        )
        await bus.publish(scored)

    @bus.subscribe(SignalScored)
    async def on_scored(event) -> None:  # type: ignore[type-arg]
        """Route scored signals to portfolio manager and Telegram."""
        await pm.on_signal_scored(event)
        # HIGH signals get immediate Telegram alert
        if event.confluence_score >= 3:
            await alerter.send_signal_alert(event, event.symbol)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Hermes starting up (env=%s, phase=5)", settings.hermes_env)

    # Event bus
    bus_task = asyncio.create_task(bus.run(), name="event-bus")

    # Wire event subscribers (scoring → portfolio manager + DB persistence)
    _setup_event_subscribers()
    setup_db_subscribers()

    # Load recent signals from DB into memory (survive restarts)
    from hermes.api.signals import _signals
    try:
        db_signals = await load_recent_signals(limit=500)
        _signals.extend(db_signals)
        logger.info("Loaded %d signals from DB", len(db_signals))
    except Exception:
        logger.warning("Could not load signals from DB — starting fresh")

    # Load open PaperTracker positions from DB
    from hermes.execution.paper_tracker import paper_tracker
    try:
        await paper_tracker.load_from_db()
    except Exception:
        logger.warning("Could not load paper positions from DB")

    # Scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")

    # ── US Scanners ─────────────────────────────────────────────────────────
    scheduler.add_job(_run_long_scan, CronTrigger(day_of_week="sun", hour=18, minute=0), id="long_us")
    scheduler.add_job(_run_mid_scan, CronTrigger(day_of_week="mon-fri", hour=21, minute=15), id="mid_us")
    scheduler.add_job(_run_intra_scan, CronTrigger(day_of_week="mon-fri", hour="13-19", minute="0,15,30,45"), id="intra_us")

    # ── EU Scanners (EuroStoxx 50) — staggered 5 min after US ───────────────
    scheduler.add_job(_run_long_eu_scan, CronTrigger(day_of_week="sun", hour=18, minute=5), id="long_eu")
    scheduler.add_job(_run_mid_eu_scan, CronTrigger(day_of_week="mon-fri", hour=17, minute=0), id="mid_eu")
    scheduler.add_job(_run_intra_eu_scan, CronTrigger(day_of_week="mon-fri", hour="7-16", minute="3,18,33,48"), id="intra_eu")

    # ── UK Scanners (FTSE 100) — staggered 10 min after US ──────────────────
    scheduler.add_job(_run_long_uk_scan, CronTrigger(day_of_week="sun", hour=18, minute=10), id="long_uk")
    scheduler.add_job(_run_mid_uk_scan, CronTrigger(day_of_week="mon-fri", hour=17, minute=5), id="mid_uk")
    scheduler.add_job(_run_intra_uk_scan, CronTrigger(day_of_week="mon-fri", hour="8-16", minute="6,21,36,51"), id="intra_uk")

    # ── Asian Scanners (long only) ───────────────────────────────────────────
    scheduler.add_job(_run_long_hk_scan, CronTrigger(day_of_week="sun", hour=18, minute=15), id="long_hk")
    scheduler.add_job(_run_long_jp_scan, CronTrigger(day_of_week="sun", hour=18, minute=20), id="long_jp")
    # Outcome tracker: every hour
    scheduler.add_job(run_outcome_check, IntervalTrigger(hours=1), id="outcome_tracker")
    # Kill switch check: every 5 min
    scheduler.add_job(_run_kill_switch_check, IntervalTrigger(minutes=5), id="kill_switch")
    # Position monitor: every 5 min during market hours
    scheduler.add_job(run_position_monitor, IntervalTrigger(minutes=5), id="position_monitor")
    # Daily summary: weekdays 21:05 UTC (just after US close)
    scheduler.add_job(_run_daily_summary, CronTrigger(day_of_week="mon-fri", hour=21, minute=5))

    if settings.hermes_env != "test":
        scheduler.start()
        logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    logger.info(
        "Alpaca: %s | Telegram: %s | Halted: %s",
        "configured" if settings.alpaca_configured else "not configured (PaperBroker)",
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
    allow_origins=["*"],  # open for Phase 6 — lock down per-domain in production
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(performance_router)
app.include_router(signals_router)
app.include_router(portfolios_router)


# ── Routes ────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health() -> dict:
    broker = _get_broker()
    return {
        "status": "ok",
        "version": "0.1.0",
        "phase": "5 — Paper Trading",
        "env": settings.hermes_env,
        "halted": settings.hermes_halted,
        "alpaca_configured": settings.alpaca_configured,
        "ibkr_configured": settings.ibkr_configured,
        "telegram_configured": settings.telegram_configured,
        "broker": broker.name,
    }


@app.get("/api/status", tags=["system"])
async def status() -> dict:
    return {
        "phase": "5 — Paper Trading via Alpaca",
        "message": "Scanners + scoring + execution wired. Fill in .env to activate Alpaca.",
    }


@app.post("/api/scan/{portfolio}", tags=["scanner"])
async def trigger_scan(portfolio: str) -> dict:
    """Manually trigger a scan (dev/testing)."""
    if portfolio == "long":
        asyncio.create_task(_run_long_scan())
    elif portfolio == "mid":
        asyncio.create_task(_run_mid_scan())
    elif portfolio == "intra":
        asyncio.create_task(_run_intra_scan())
    else:
        return {"error": f"Unknown portfolio: {portfolio}"}
    return {"status": "scan_triggered", "portfolio": portfolio}


@app.get("/api/debug/scan", tags=["debug"])
async def debug_scan() -> dict:
    """Debug the scanning pipeline step by step."""
    results = {}
    
    # Test 1: Universe loading
    try:
        from hermes.data.universe import load_universe
        universe = load_universe("mid_us")
        results["universe"] = {
            "status": "success",
            "symbols_count": len(universe.symbols),
            "first_3_symbols": universe.symbols[:3],
            "provider": universe.provider
        }
    except Exception as e:
        results["universe"] = {"status": "error", "error": str(e)}
    
    # Test 2: Data provider
    try:
        from hermes.data.yfinance_provider import YFinanceProvider
        from datetime import date, timedelta
        provider = YFinanceProvider()
        end = date.today()
        start = end - timedelta(days=30)
        from hermes.data.base import Timeframe
        df = await provider.get_ohlcv("AAPL", Timeframe.DAILY, start, end)
        results["data_provider"] = {
            "status": "success" if not df.empty else "empty_data",
            "rows": len(df),
            "columns": list(df.columns) if not df.empty else [],
            "date_range": f"{df.index[0]} to {df.index[-1]}" if not df.empty else "no data"
        }
    except Exception as e:
        results["data_provider"] = {"status": "error", "error": str(e)}
    
    # Test 3: Setup detection
    try:
        from hermes.scanners.base import MidScanner
        scanner = MidScanner()
        results["scanner"] = {
            "status": "success",
            "setups_count": len(scanner.setups),
            "setup_names": [type(s).__name__ for s in scanner.setups]
        }
    except Exception as e:
        results["scanner"] = {"status": "error", "error": str(e)}
    
    # Test 4: Event bus
    try:
        from hermes.events.bus import bus
        results["event_bus"] = {
            "status": "success",
            "is_running": hasattr(bus, '_running') and bus._running,
            "subscriber_count": len(getattr(bus, '_subscribers', {}))
        }
    except Exception as e:
        results["event_bus"] = {"status": "error", "error": str(e)}
    
    return results


@app.get("/api/debug/price/{symbol}", tags=["debug"])
async def debug_price(symbol: str) -> dict:
    """Debug price data for a specific symbol."""
    try:
        from hermes.data.yfinance_provider import YFinanceProvider
        from hermes.data.base import Timeframe
        from datetime import date, timedelta
        
        provider = YFinanceProvider()
        end = date.today()
        start = end - timedelta(days=10)
        
        # Get recent data
        df = await provider.get_ohlcv(symbol, Timeframe.DAILY, start, end)
        
        if df.empty:
            return {"status": "no_data", "symbol": symbol}
        
        # Extract key info
        latest = df.iloc[-1]
        
        return {
            "status": "success",
            "symbol": symbol,
            "data_points": len(df),
            "date_range": f"{df.index[0].date()} to {df.index[-1].date()}",
            "latest_date": str(df.index[-1].date()),
            "latest_ohlc": {
                "open": float(latest['Open']),
                "high": float(latest['High']), 
                "low": float(latest['Low']),
                "close": float(latest['Close']),
                "volume": int(latest['Volume'])
            },
            "price_range_10d": {
                "min": float(df['Low'].min()),
                "max": float(df['High'].max()),
                "avg_close": float(df['Close'].mean())
            }
        }
    except Exception as e:
        return {"status": "error", "symbol": symbol, "error": str(e)}


@app.get("/api/broker/account", tags=["broker"])
async def broker_account() -> dict:
    """Live account info from the configured broker."""
    try:
        broker = _get_broker()
        account = await broker.get_account()
        return {
            "broker": broker.name,
            "equity": account.equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "today_pnl": account.today_pnl,
            "today_pnl_pct": account.today_pnl_pct,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/broker/positions", tags=["broker"])
async def broker_positions() -> dict:
    """Live open positions from the configured broker."""
    try:
        broker = _get_broker()
        positions = await broker.get_positions()
        return {
            "broker": broker.name,
            "count": len(positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_entry": p.avg_entry_price,
                    "current_price": p.current_price,
                    "unrealised_pnl": p.unrealised_pnl,
                    "side": p.side,
                }
                for p in positions
            ],
        }
    except Exception as e:
        return {"error": str(e)}


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
