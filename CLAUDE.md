# Hermes Trading Bot — Agent Instructions

This is the Hermes Trading Bot monorepo. Read this file before touching any code.

## What this is

Three independent portfolio scanners (long / mid / intraday) with a shared
FastAPI + APScheduler backend, SvelteKit frontend, and Alpaca + IBKR execution.
Production target: Hetzner CX32, behind Caddy, deployed via GitHub Actions.

## Architecture: event sourcing

The core pattern is an internal async message bus (asyncio.Queue, upgradeable to
Redis Streams / FastStream without changing service contracts).

Event flow:
  Scanner → SignalDetected → Scorer → SignalScored → PortfolioManager
  → PositionRequested → BrokerAdapter → OrderFilled → OutcomeTracker
  → OutcomeRecorded

All events are appended to the `events` table (Postgres). Materialised views
feed the dashboard. The event stream IS the ML training set.

Key files:
  backend/hermes/events/types.py   — Pydantic event models
  backend/hermes/events/bus.py     — async pub/sub dispatcher
  backend/hermes/main.py           — FastAPI entrypoint, bus startup
  backend/hermes/config.py         — Pydantic Settings (reads .env)
  backend/hermes/scheduler.py      — APScheduler jobs

## Stack

Backend:  Python 3.12, FastAPI, APScheduler, SQLAlchemy 2 (async), Alembic,
          Pydantic v2, yfinance, alpaca-py, python-dotenv
Frontend: SvelteKit, TypeScript, Tailwind, shadcn-svelte, TanStack Table,
          TradingView Lightweight Charts, ECharts
DB:       Supabase Postgres (dev: docker-compose local)
Broker:   Alpaca paper (day 1) → IBKR via ib_async (Phase 5+)

## Running locally

```bash
# Backend
cd backend && uv run uvicorn hermes.main:app --reload --port 8090

# Frontend
cd frontend && npm run dev

# DB (first time)
docker-compose up -d db redis
cd backend && uv run alembic upgrade head
```

## Tests

```bash
cd backend && uv run pytest                    # unit tests only
cd backend && uv run pytest -m integration     # needs live network
```

## Conventions

- Never commit secrets. Use .env (gitignored). .env.example has placeholders.
- One event type = one Pydantic model in events/types.py
- Never query the events table directly from API routes — use materialised views
- Kill switch: set HERMES_HALTED=true in .env or via POST /api/settings/halt
- Phase discipline: don't build phase N+1 logic until phase N is shipped

## Phases

0  Bootstrap (current) — hello world, CI green
1  Data layer — yfinance / Alpaca / CoinGecko providers
2  Indicators + setups + scoring
3  Backtest + outcome tracker
4  Frontend
5  Paper trading via Alpaca + IBKR + Telegram
6  30-day evaluation gate
7  ML pipeline (XGBoost, ≥500 labelled signals)
