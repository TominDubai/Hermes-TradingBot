"""Phase 3 tests: backtest engine, report, outcome tracker."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from hermes.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    SetupStats,
    SimulatedTrade,
)
from hermes.events.types import Portfolio

# ── Helpers ───────────────────────────────────────────────────

def _make_df(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="B", tz="UTC")
    base = np.linspace(100, 160, n)
    close = base + rng.normal(0, 1, n)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "open": close - 0.5,
        "high": close + rng.uniform(0.5, 2, n),
        "low": close - rng.uniform(0.5, 2, n),
        "close": close,
        "volume": rng.uniform(1e6, 5e6, n),
    }, index=idx)


def _make_setup_mock(fires_every_n: int = 50):
    """Mock setup that fires every N bars, returning a fixed SetupResult."""
    from hermes.events.types import Direction
    from hermes.setups.base import SetupResult

    call_count = [0]

    def detect(df):
        call_count[0] += 1
        if call_count[0] % fires_every_n == 0:
            close = float(df["close"].iloc[-1])
            return SetupResult(
                score=0.75,
                entry=close,
                stop=close * 0.97,
                target=close * 1.09,
                direction=Direction.LONG,
                setup_name="mock_setup",
                timeframe="1d",
            )
        return None

    mock = MagicMock()
    mock.name = "mock_setup"
    mock.min_bars = 50
    mock.detect = detect
    return mock


# ── SimulatedTrade ────────────────────────────────────────────

class TestSimulatedTrade:
    def test_risk_reward_rr(self):
        t = SimulatedTrade(
            symbol="AAPL", setup_name="x", portfolio="mid", direction="long",
            entry_bar=0, entry_price=100.0, stop_price=97.0, target_price=109.0,
            raw_score=0.7,
        )
        assert t.risk == pytest.approx(3.0)
        assert t.reward == pytest.approx(9.0)
        assert t.rr_ratio == pytest.approx(3.0)

    def test_zero_risk_rr_is_zero(self):
        t = SimulatedTrade(
            symbol="X", setup_name="x", portfolio="mid", direction="long",
            entry_bar=0, entry_price=100.0,
            stop_price=100.0, target_price=110.0, raw_score=0.7,
        )
        assert t.rr_ratio == 0.0

# ── SetupStats ────────────────────────────────────────────────

class TestSetupStats:
    def test_win_rate_no_trades(self):
        s = SetupStats("test", "mid")
        assert s.win_rate == 0.0

    def test_win_rate_calculated(self):
        s = SetupStats("test", "mid", total_trades=3, wins=2, losses=1)
        assert s.win_rate == pytest.approx(2 / 3)

    def test_passes_gate_true(self):
        s = SetupStats("test", "mid", total_trades=10, wins=5, losses=3,
                       expired=2, avg_rr_realised=1.5)
        assert s.passes_gate is True

    def test_fails_gate_low_win_rate(self):
        s = SetupStats("test", "mid", total_trades=10, wins=3, losses=7,
                       avg_rr_realised=1.5)
        assert s.passes_gate is False

    def test_fails_gate_not_enough_trades(self):
        s = SetupStats("test", "mid", total_trades=3, wins=3, avg_rr_realised=2.0)
        assert s.passes_gate is False


# ── BacktestResult ────────────────────────────────────────────

class TestBacktestResult:
    def test_add_trade_win(self):
        result = BacktestResult("mid", date(2023, 1, 1), date(2024, 1, 1))
        t = SimulatedTrade(
            symbol="AAPL", setup_name="cup_and_handle", portfolio="mid",
            direction="long", entry_bar=100, entry_price=150.0,
            stop_price=145.0, target_price=165.0, raw_score=0.75, outcome="WIN", pnl_pct=9.5,
        )
        result.add_trade(t)
        assert result.total_trades == 1
        assert result.setup_stats["cup_and_handle"].wins == 1

    def test_passing_failing_setups(self):
        result = BacktestResult("mid", date(2023, 1, 1), date(2024, 1, 1))
        # Add enough wins to pass gate
        for _ in range(6):
            t = SimulatedTrade(
                symbol="AAPL", setup_name="good_setup", portfolio="mid",
                direction="long", entry_bar=0, entry_price=100.0,
                stop_price=97.0, target_price=109.0, raw_score=0.75, outcome="WIN", pnl_pct=8.9,
            )
            result.add_trade(t)
        # Add a failing setup
        for _ in range(6):
            t = SimulatedTrade(
                symbol="AAPL", setup_name="bad_setup", portfolio="mid",
                direction="long", entry_bar=0, entry_price=100.0,
                stop_price=97.0, target_price=109.0, raw_score=0.75, outcome="LOSS", pnl_pct=-3.1,
            )
            result.add_trade(t)
        assert "good_setup" in result.passing_setups
        assert "bad_setup" in result.failing_setups


# ── BacktestEngine ────────────────────────────────────────────

class TestBacktestEngine:
    def test_simulate_symbol_produces_trades(self):
        engine = BacktestEngine(max_hold_bars=10)
        setup = _make_setup_mock(fires_every_n=30)
        df = _make_df(n=300)
        trades = engine._simulate_symbol(setup, df, "AAPL", "mid")
        assert isinstance(trades, list)
        # Each trade should have a valid outcome
        for t in trades:
            assert t.outcome in ("WIN", "LOSS", "EXPIRED")
            assert t.exit_price is not None

    def test_trade_rr_is_positive(self):
        engine = BacktestEngine(max_hold_bars=10)
        setup = _make_setup_mock(fires_every_n=30)
        df = _make_df(n=300)
        trades = engine._simulate_symbol(setup, df, "AAPL", "mid")
        for t in trades:
            assert t.rr_ratio >= 0

    def test_no_lookahead(self):
        """Verify detect() is only called with bars up to current index."""
        bar_counts = []

        class TrackingSetup:
            name = "tracker"
            min_bars = 50
            def detect(self, df):
                bar_counts.append(len(df))
                return None

        engine = BacktestEngine()
        df = _make_df(n=100)
        engine._simulate_symbol(TrackingSetup(), df, "X", "mid")
        # Each call should have strictly more bars than the previous
        assert bar_counts == sorted(bar_counts)

    @pytest.mark.asyncio
    async def test_run_with_mocked_provider(self):
        engine = BacktestEngine(max_hold_bars=10)
        setup = _make_setup_mock(fires_every_n=30)
        df = _make_df(n=300)

        with patch("hermes.data.yfinance_provider.YFinanceProvider") as MockProvider:
            instance = MockProvider.return_value
            instance.get_ohlcv = AsyncMock(return_value=df)

            result = await engine.run(
                setup, ["AAPL", "MSFT"], "1d",
                date(2022, 1, 1), date(2024, 1, 1), "mid",
            )

        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0


# ── Report ────────────────────────────────────────────────────

class TestBacktestReport:
    def test_report_generates_markdown(self, tmp_path):
        from hermes.backtest.report import generate_report
        result = BacktestResult("mid", date(2023, 1, 1), date(2024, 1, 1))
        for _ in range(6):
            result.add_trade(SimulatedTrade(
                symbol="AAPL", setup_name="mean_reversion", portfolio="mid",
                direction="long", entry_bar=0, entry_price=100.0,
                stop_price=97.0, target_price=109.0, raw_score=0.75, outcome="WIN", pnl_pct=8.9,
            ))
        path = generate_report(result, output_dir=tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "mean_reversion" in content
        assert "PASS" in content or "FAIL" in content

    def test_empty_result_report(self, tmp_path):
        from hermes.backtest.report import generate_report
        result = BacktestResult("long", date(2023, 1, 1), date(2024, 1, 1))
        path = generate_report(result, output_dir=tmp_path)
        assert path.exists()


# ── Outcome tracker ───────────────────────────────────────────

class TestOutcomeTracker:
    def test_open_and_close(self):
        from hermes.outcome.tracker import OpenPosition, OutcomeTracker
        t = OutcomeTracker()
        pos = OpenPosition(
            signal_id="dc5df980-088a-4dba-93ac-3b93d168ac0e", symbol="AAPL", portfolio=Portfolio.MID,
            direction="long", entry_price=150.0, stop_price=145.0,
            target_price=165.0,
        )
        t.open(pos)
        assert t.open_count == 1
        closed = t.close("dc5df980-088a-4dba-93ac-3b93d168ac0e")
        assert closed is not None
        assert t.open_count == 0

    @pytest.mark.asyncio
    async def test_check_all_resolves_win(self):
        from hermes.outcome.tracker import OpenPosition, OutcomeTracker
        t = OutcomeTracker()
        pos = OpenPosition(
            signal_id="72de4642-a786-484b-bade-ab883dbaceea", symbol="AAPL", portfolio=Portfolio.MID,
            direction="long", entry_price=150.0, stop_price=145.0,
            target_price=155.0,
            entry_time=datetime.now(UTC) - timedelta(hours=1),
        )
        t.open(pos)

        with patch("hermes.outcome.tracker._fetch_prices", new=AsyncMock(return_value={"AAPL": 160.0})):
            resolved = await t.check_all()

        assert resolved == 1
        assert t.open_count == 0

    @pytest.mark.asyncio
    async def test_check_all_resolves_loss(self):
        from hermes.outcome.tracker import OpenPosition, OutcomeTracker
        t = OutcomeTracker()
        pos = OpenPosition(
            signal_id="919c9fd8-0e9a-4a26-8f6d-724336f473ee", symbol="TSLA", portfolio=Portfolio.INTRA,
            direction="long", entry_price=200.0, stop_price=195.0,
            target_price=210.0,
            entry_time=datetime.now(UTC) - timedelta(hours=1),
        )
        t.open(pos)

        with patch("hermes.outcome.tracker._fetch_prices", new=AsyncMock(return_value={"TSLA": 193.0})):
            resolved = await t.check_all()

        assert resolved == 1

    @pytest.mark.asyncio
    async def test_check_all_no_positions(self):
        from hermes.outcome.tracker import OutcomeTracker
        t = OutcomeTracker()
        resolved = await t.check_all()
        assert resolved == 0
