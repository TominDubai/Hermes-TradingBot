"""Unit tests for YFinanceProvider.

All tests mock yfinance so no live network calls are made.
Tests that require a live connection are marked with @pytest.mark.integration.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from hermes.data.base import OHLCV_COLUMNS, RateLimitError, Timeframe
from hermes.data.yfinance_provider import (
    YFinanceProvider,
    _apply_sanity_filters,
    _compute_atr,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(
    n: int = 20,
    start: str = "2024-01-01",
    freq: str = "D",
    base_price: float = 100.0,
    volume: float = 1_000_000.0,
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with a UTC DatetimeIndex."""
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    prices = [base_price + i * 0.1 for i in range(n)]
    data = {
        "Open":   [p * 0.99  for p in prices],
        "High":   [p * 1.01  for p in prices],
        "Low":    [p * 0.98  for p in prices],
        "Close":  prices,
        "Volume": [volume] * n,
    }
    return pd.DataFrame(data, index=idx)


def _run(coro):
    """Run a coroutine in a fresh event loop (helper for non-async tests)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetOhlcvNormal:
    """Normal fetch — non-empty data returns a correctly shaped DataFrame."""

    def test_returns_correct_shape(self) -> None:
        raw = _make_ohlcv(n=30)
        provider = YFinanceProvider()

        with patch("hermes.data.yfinance_provider._download_sync", return_value=raw):
            df = _run(
                provider.get_ohlcv(
                    "AAPL",
                    Timeframe.D1,
                    date(2024, 1, 1),
                    date(2024, 1, 31),
                )
            )

        assert not df.empty, "Expected a non-empty DataFrame"
        assert list(df.columns) == OHLCV_COLUMNS, "Columns must match OHLCV_COLUMNS"
        assert df.index.tz is not None, "Index must be tz-aware"  # type: ignore[union-attr]
        assert str(df.index.tz) == "UTC", "Index must be UTC"  # type: ignore[union-attr]
        assert len(df) == 30

    def test_index_is_sorted_ascending(self) -> None:
        raw = _make_ohlcv(n=10)
        provider = YFinanceProvider()

        with patch("hermes.data.yfinance_provider._download_sync", return_value=raw):
            df = _run(
                provider.get_ohlcv("MSFT", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 15))
            )

        assert df.index.is_monotonic_increasing, "Index must be sorted ascending"

    def test_dtypes_are_float64(self) -> None:
        raw = _make_ohlcv(n=5)
        provider = YFinanceProvider()

        with patch("hermes.data.yfinance_provider._download_sync", return_value=raw):
            df = _run(
                provider.get_ohlcv("GOOG", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 10))
            )

        for col in OHLCV_COLUMNS:
            assert df[col].dtype == "float64", f"Column {col} should be float64"


class TestGetOhlcvEmpty:
    """yfinance returns an empty DataFrame — provider returns empty DataFrame (no raise)."""

    def test_empty_response_returns_empty_df(self) -> None:
        provider = YFinanceProvider()
        empty_raw = pd.DataFrame()

        with patch("hermes.data.yfinance_provider._download_sync", return_value=empty_raw):
            df = _run(
                provider.get_ohlcv("FAKE", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 5))
            )

        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_consecutive_empty_raises_rate_limit(self) -> None:
        """After _EMPTY_LIMIT consecutive empty responses, RateLimitError is raised."""
        from hermes.data.yfinance_provider import _EMPTY_LIMIT

        provider = YFinanceProvider()
        empty_raw = pd.DataFrame()

        with patch("hermes.data.yfinance_provider._download_sync", return_value=empty_raw):
            for _ in range(_EMPTY_LIMIT - 1):
                df = _run(
                    provider.get_ohlcv("FAKE", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 5))
                )
                assert df.empty  # still empty, not yet an error

            with pytest.raises(RateLimitError):
                _run(
                    provider.get_ohlcv("FAKE", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 5))
                )


class TestSanityFilters:
    """Bars with >50 % daily moves should be dropped."""

    def test_large_price_move_filtered(self) -> None:
        """A bar that doubles overnight (100 % move) must be removed."""
        idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
        closes = [100.0, 101.0, 102.0, 210.0, 212.0]  # bar 3 is a >50 % jump
        df = pd.DataFrame(
            {
                "open":   closes,
                "high":   [c * 1.01 for c in closes],
                "low":    [c * 0.99 for c in closes],
                "close":  closes,
                "volume": [1_000_000.0] * 5,
            },
            index=idx,
        )

        filtered = _apply_sanity_filters(df)

        # The bar at index 3 (210.0 close, ~106 % up from 102.0) must be dropped
        assert len(filtered) < len(df), "Expected at least one bar to be dropped"
        assert 210.0 not in filtered["close"].values

    def test_normal_data_unchanged(self) -> None:
        """Data with small daily moves should pass through unmodified."""
        raw = _make_ohlcv(n=20)
        raw.columns = [c.lower() for c in raw.columns]  # normalise
        filtered = _apply_sanity_filters(raw)
        assert len(filtered) == len(raw)

    def test_provider_filters_bad_bars_end_to_end(self) -> None:
        """End-to-end: a bar with a 200 % jump is dropped by get_ohlcv."""
        n = 10
        raw = _make_ohlcv(n=n)
        # Inject a 200 % price spike on bar 5
        raw.iloc[5, raw.columns.get_loc("Close")] = raw.iloc[4]["Close"] * 3.0
        raw.iloc[5, raw.columns.get_loc("High")] = raw.iloc[5]["Close"] * 1.01
        raw.iloc[5, raw.columns.get_loc("Open")] = raw.iloc[5]["Close"] * 0.99

        provider = YFinanceProvider()

        with patch("hermes.data.yfinance_provider._download_sync", return_value=raw):
            df = _run(
                provider.get_ohlcv("TEST", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 15))
            )

        assert len(df) < n, "Provider should have dropped the spike bar"


class TestIsTradeable:
    """is_tradeable() reads regularMarketPrice from yf.Ticker.info."""

    def test_active_symbol_returns_true(self) -> None:
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": 150.25, "symbol": "AAPL"}

        with patch("hermes.data.yfinance_provider.yf.Ticker", return_value=mock_ticker):
            result = _run(YFinanceProvider().is_tradeable("AAPL"))

        assert result is True

    def test_delisted_symbol_returns_false(self) -> None:
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": None, "symbol": "DEAD"}

        with patch("hermes.data.yfinance_provider.yf.Ticker", return_value=mock_ticker):
            result = _run(YFinanceProvider().is_tradeable("DEAD"))

        assert result is False

    def test_missing_key_returns_false(self) -> None:
        mock_ticker = MagicMock()
        mock_ticker.info = {}  # key absent

        with patch("hermes.data.yfinance_provider.yf.Ticker", return_value=mock_ticker):
            result = _run(YFinanceProvider().is_tradeable("GHOST"))

        assert result is False

    def test_exception_returns_false(self) -> None:
        with patch(
            "hermes.data.yfinance_provider._get_info_sync",
            side_effect=Exception("network error"),
        ):
            result = _run(YFinanceProvider().is_tradeable("ERR"))

        assert result is False


class TestTimeframeMapping:
    """All Timeframe values should be accepted without KeyError."""

    @pytest.mark.parametrize(
        "tf",
        [
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.M30,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
            Timeframe.W1,
        ],
    )
    def test_timeframe_accepted(self, tf: Timeframe) -> None:
        raw = _make_ohlcv(n=5, freq="h" if tf not in {Timeframe.D1, Timeframe.W1} else "D")
        provider = YFinanceProvider()

        with patch("hermes.data.yfinance_provider._download_sync", return_value=raw):
            # Should not raise KeyError or similar
            df = _run(
                provider.get_ohlcv("AAPL", tf, date(2024, 1, 1), date(2024, 1, 10))
            )

        assert isinstance(df, pd.DataFrame)


class TestComputeAtr:
    """Basic sanity check for the ATR helper."""

    def test_atr_length_matches_input(self) -> None:
        df = _make_ohlcv(n=30)
        df.columns = [c.lower() for c in df.columns]
        atr = _compute_atr(df)
        assert len(atr) == len(df)

    def test_atr_non_negative(self) -> None:
        df = _make_ohlcv(n=30)
        df.columns = [c.lower() for c in df.columns]
        atr = _compute_atr(df)
        assert (atr >= 0).all()


# ---------------------------------------------------------------------------
# Integration tests (require live network)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_live_aapl_daily() -> None:
    """Fetch real AAPL daily data. Requires internet access."""
    provider = YFinanceProvider()
    df = _run(
        provider.get_ohlcv("AAPL", Timeframe.D1, date(2024, 1, 1), date(2024, 3, 31))
    )
    assert not df.empty
    assert list(df.columns) == OHLCV_COLUMNS
    assert df.index.tz is not None  # type: ignore[union-attr]


@pytest.mark.integration
def test_live_is_tradeable_aapl() -> None:
    """Check AAPL is tradeable. Requires internet access."""
    provider = YFinanceProvider()
    result = _run(provider.is_tradeable("AAPL"))
    assert result is True


@pytest.mark.integration
def test_live_tse_suffix_probe() -> None:
    """Probe .T suffix for a TSE stock (Toyota). Requires internet access."""
    provider = YFinanceProvider()
    # Toyota on TSE
    df = _run(
        provider.get_ohlcv("7203.T", Timeframe.D1, date(2024, 1, 1), date(2024, 3, 31))
    )
    assert not df.empty
