# Hermes Trading Bot

Three-portfolio algorithmic trading system: long-term, mid-term, and intraday scanners with a shared FastAPI backend, SvelteKit dashboard, and Alpaca + IBKR execution.

**Status:** Phase 0 — Bootstrap

## Quick start

```bash
cp .env.example .env   # fill in your keys
docker-compose up -d   # Postgres + Redis
cd backend && uv run uvicorn hermes.main:app --reload --port 8090
cd frontend && npm run dev
```

## Docs

- [Build plan](https://github.com/TominDubai/Hermes-TradingBot/blob/main/CLAUDE.md)
- Dashboard: http://localhost:5173
- API: http://localhost:8090/docs
