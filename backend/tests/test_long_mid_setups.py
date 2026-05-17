"""Tests for long and mid portfolio setups."""
import numpy as np
import pandas as pd
import pytest

from hermes.events.types import Direction
from hermes.setups.base import SetupResult


def _make_df(n: int = 300, trend: str = "up", seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with a controlled trend."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="B", tz="UTC")

    if trend == "up":
        base = np.linspace(100, 200, n)
    elif trend == "down":
        base = np.linspace(200, 100, n)
    else:
        base = np.full(n, 150.0)

    noise = rng.normal(0, 1, n)
    close = base + noise
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    low = np.maximum(low, 0.5)
    open_ = close - rng.normal(0, 0.5, n)
    volume = rng.uniform(1_000_000, 5_000_000, n)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ── EMA Trend Follow ──────────────────────────────────────────

class TestEMATrendFollow:
    def test_insufficient_bars_returns_none(self):
        from hermes.setups.ema_trend_follow import EMATrendFollow
        df = _make_df(n=100)
        result = EMATrendFollow().detect(df)
        assert result is None

    def test_downtrend_returns_none(self):
        from hermes.setups.ema_trend_follow import EMATrendFollow
        df = _make_df(n=300, trend="down")
        result = EMATrendFollow().detect(df)
        assert result is None

    def test_uptrend_may_detect(self):
        """With 300 uptrend bars the setup may fire — just check shape if it does."""
        from hermes.setups.ema_trend_follow import EMATrendFollow
        df = _make_df(n=300, trend="up")
        result = EMATrendFollow().detect(df)
        if result is not None:
            assert result.direction == Direction.LONG
            assert result.rr_ratio >= 2.9
            assert result.entry > result.stop
            assert result.target > result.entry

    def test_setup_result_rr_properties(self):
        r = SetupResult(
            score=0.7,
            entry=100.0,
            stop=97.0,
            target=109.0,
            direction=Direction.LONG,
            setup_name="ema_trend_follow",
            timeframe="1d",
        )
        assert r.risk == pytest.approx(3.0)
        assert r.reward == pytest.approx(9.0)
        assert r.rr_ratio == pytest.approx(3.0)


# ── Fundamental Quality ───────────────────────────────────────

class TestFundamentalQuality:
    def test_insufficient_bars_returns_none(self):
        from hermes.setups.fundamental_quality import FundamentalQuality
        df = _make_df(n=10)
        result = FundamentalQuality().detect(df)
        assert result is None

    def test_empty_info_returns_none(self):
        from hermes.setups.fundamental_quality import FundamentalQuality
        df = _make_df(n=100, trend="up")
        result = FundamentalQuality().detect(df, _info_override={})
        assert result is None

    def test_good_fundamentals_with_uptrend_detects(self):
        from hermes.setups.fundamental_quality import FundamentalQuality
        df = _make_df(n=100, trend="up")
        good_info = {
            "trailingPE": 15.0,
            "returnOnEquity": 0.22,
            "debtToEquity": 50.0,
            "revenueGrowth": 0.12,
            "earningsGrowth": 0.08,
        }
        result = FundamentalQuality().detect(df, _info_override=good_info)
        assert result is not None
        assert result.direction == Direction.LONG
        assert result.score == pytest.approx(0.80)
        assert result.metadata["criteria_met"] >= 4

    def test_poor_fundamentals_returns_none(self):
        from hermes.setups.fundamental_quality import FundamentalQuality
        df = _make_df(n=100, trend="up")
        bad_info = {
            "trailingPE": 200.0,   # too high
            "returnOnEquity": 0.02,  # too low
            "debtToEquity": 300.0,  # too high
            "revenueGrowth": -0.1,  # negative
            "earningsGrowth": -0.2,  # negative
        }
        result = FundamentalQuality().detect(df, _info_override=bad_info)
        assert result is None

    def test_downtrend_returns_none_even_with_good_fundamentals(self):
        from hermes.setups.fundamental_quality import FundamentalQuality
        df = _make_df(n=100, trend="down")
        good_info = {
            "trailingPE": 12.0,
            "returnOnEquity": 0.25,
            "debtToEquity": 30.0,
            "revenueGrowth": 0.15,
            "earningsGrowth": 0.10,
        }
        result = FundamentalQuality().detect(df, _info_override=good_info)
        assert result is None  # price below SMA50


# ── Mean Reversion ────────────────────────────────────────────

class TestMeanReversion:
    def test_insufficient_bars_returns_none(self):
        from hermes.setups.mean_reversion import MeanReversion
        df = _make_df(n=30)
        assert MeanReversion().detect(df) is None

    def test_downtrend_returns_none(self):
        from hermes.setups.mean_reversion import MeanReversion
        df = _make_df(n=250, trend="down")
        assert MeanReversion().detect(df) is None


# ── Breakout Consolidation ────────────────────────────────────

class TestBreakoutConsolidation:
    def test_insufficient_bars_returns_none(self):
        from hermes.setups.breakout_consolidation import BreakoutConsolidation
        df = _make_df(n=30)
        assert BreakoutConsolidation().detect(df) is None

    def test_returns_none_or_result(self):
        from hermes.setups.breakout_consolidation import BreakoutConsolidation
        df = _make_df(n=100, trend="flat")
        result = BreakoutConsolidation().detect(df)
        if result is not None:
            assert result.rr_ratio > 0
            assert result.entry > result.stop


# ── Cup and Handle ────────────────────────────────────────────

class TestCupAndHandle:
    def test_insufficient_bars_returns_none(self):
        from hermes.setups.cup_and_handle import CupAndHandle
        df = _make_df(n=50)
        assert CupAndHandle().detect(df) is None

    def test_returns_none_or_valid_result(self):
        from hermes.setups.cup_and_handle import CupAndHandle
        df = _make_df(n=150, trend="up")
        result = CupAndHandle().detect(df)
        if result is not None:
            assert result.direction == Direction.LONG
            assert result.rr_ratio > 0
