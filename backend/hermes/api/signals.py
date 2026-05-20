"""
Signals API — list, filter, and retrieve signals.
Also hosts the WebSocket endpoint for live signal feed.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from hermes.events.bus import bus
from hermes.events.types import SignalDetected, SignalScored

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["signals"])

# ── In-memory signal store (replaced by DB in Phase 5) ───────

_signals: list[dict[str, Any]] = []
_MAX_SIGNALS = 500

# ── WebSocket connection manager ──────────────────────────────

class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept(headers=[(b"access-control-allow-origin", b"*")])
        self.active.append(ws)
        logger.info("WS client connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        self.active.remove(ws)
        logger.info("WS client disconnected (%d total)", len(self.active))

    async def broadcast(self, data: dict) -> None:
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


manager = ConnectionManager()


# ── Event bus subscribers ─────────────────────────────────────

@bus.subscribe(SignalDetected)
async def _on_signal_detected(event: SignalDetected) -> None:  # type: ignore[type-arg]
    record = {
        "id": str(event.event_id),
        "type": "signal_detected",
        "symbol": event.symbol,
        "portfolio": event.portfolio.value,
        "direction": event.direction.value,
        "setup_name": event.setup_name,
        "timeframe": event.timeframe,
        "raw_score": event.raw_score,
        "confluence_score": None,  # filled when scored
        "entry_price": event.entry_price,
        "stop_price": event.stop_price,
        "target_price": event.target_price,
        "rr_ratio": round(
            abs(event.target_price - event.entry_price) /
            max(abs(event.entry_price - event.stop_price), 0.0001), 2
        ),
        "detected_at": event.occurred_at.isoformat(),
        "outcome": None,
        "features": event.features,
    }
    _signals.insert(0, record)
    if len(_signals) > _MAX_SIGNALS:
        _signals.pop()
    await manager.broadcast({"event": "signal_detected", "data": record})


@bus.subscribe(SignalScored)
async def _on_signal_scored(event: SignalScored) -> None:  # type: ignore[type-arg]
    sig_id = str(event.signal_id)
    for s in _signals:
        if s["id"] == sig_id:
            s["confluence_score"] = event.confluence_score
            break
    await manager.broadcast({
        "event": "signal_scored",
        "data": {"id": sig_id, "confluence_score": event.confluence_score},
    })


# ── REST endpoints ────────────────────────────────────────────

@router.get("/signals")
async def list_signals(
    portfolio: str | None = Query(None, description="long | mid | intra"),
    min_score: int = Query(2, ge=1, le=6, description="Minimum confluence score"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    filtered = _signals
    if portfolio:
        filtered = [s for s in filtered if s["portfolio"] == portfolio]
    if min_score:
        filtered = [
            s for s in filtered
            if s.get("confluence_score") is None or (s.get("confluence_score") or 0) >= min_score
        ]
    return {
        "total": len(filtered),
        "signals": filtered[:limit],
    }


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict[str, Any]:
    for s in _signals:
        if s["id"] == signal_id:
            return s
    return {"error": "Signal not found", "id": signal_id}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    # Accept from any origin (dashboard is served from same host)
    await manager.connect(ws)
    # Send current signal backlog on connect
    try:
        await ws.send_json({"event": "backlog", "data": _signals[:50]})
        while True:
            # Keep alive — client can send ping
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)
