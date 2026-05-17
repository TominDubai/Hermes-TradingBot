"""Phase 0 smoke tests — no external dependencies."""
import pytest
from fastapi.testclient import TestClient

from hermes.events.bus import EventBus
from hermes.events.types import Direction, Portfolio, SignalDetected
from hermes.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "halted" in data


def test_status_endpoint():
    response = client.get("/api/status")
    assert response.status_code == 200


def test_signal_detected_event():
    event = SignalDetected(
        symbol="AAPL",
        portfolio=Portfolio.INTRA,
        direction=Direction.LONG,
        setup_name="opening_range_breakout",
        timeframe="15min",
        raw_score=0.75,
        entry_price=175.50,
        stop_price=173.00,
        target_price=180.00,
    )
    assert event.symbol == "AAPL"
    assert event.portfolio == Portfolio.INTRA
    assert event.event_id is not None


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    received: list[SignalDetected] = []
    bus = EventBus()

    @bus.subscribe(SignalDetected)
    async def handler(event):  # type: ignore[override]
        received.append(event)

    event = SignalDetected(
        symbol="TSLA",
        portfolio=Portfolio.MID,
        direction=Direction.LONG,
        setup_name="cup_and_handle",
        timeframe="1d",
        raw_score=0.8,
        entry_price=250.0,
        stop_price=240.0,
        target_price=275.0,
    )

    await bus.publish(event)
    await bus._dispatch(event)  # dispatch directly for test (no running loop)

    assert len(received) == 1
    assert received[0].symbol == "TSLA"
