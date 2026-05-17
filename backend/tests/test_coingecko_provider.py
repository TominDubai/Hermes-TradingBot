"""Unit tests for CoinGeckoProvider.

All tests mock aiohttp — no live network calls.
Tests that require a real CoinGecko connection are marked @pytest.mark.integration.
"""
from __future__ import annotations

from datetime import UTC, date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from hermes.data.base import ProviderError, RateLimitError, Timeframe
from hermes.data.coingecko_provider import (
    SYMBOL_TO_ID,
    CoinGeckoProvider,
    _days_param,
    _to_datetime,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_ohlc_rows(n: int = 5) -> list[list[float]]:
    """Generate n fake OHLC rows starting from 2024-01-01."""
    base_ms = 1704067200000  # 2024-01-01 00:00 UTC
    day_ms = 86_400_000
    rows: list[list[float]] = []
    for i in range(n):
        ts = base_ms + i * day_ms
        rows.append([ts, 42000.0 + i, 43000.0 + i, 41000.0 + i, 42500.0 + i])
    return rows


def _make_market_chart(n: int = 5) -> dict[str, Any]:
    """Generate a fake market_chart response with n daily volume entries."""
    base_ms = 1704067200000
    day_ms = 86_400_000
    volumes = [[base_ms + i * day_ms, 1_000_000.0 + i * 100_000] for i in range(n)]
    return {"total_volumes": volumes, "prices": [], "market_caps": []}


def _mock_session(ohlc_rows: list[Any], market_chart: dict[str, Any]) -> MagicMock:
    """Return a mock aiohttp.ClientSession that returns prepared JSON."""

    async def _json(content_type: str | None = None) -> Any:
        # This will be called twice — once for OHLC, once for market_chart.
        # We use side_effect on the context manager to sequence responses.
        ...  # overridden below

    # We need two separate response mocks
    ohlc_resp = MagicMock()
    ohlc_resp.status = 200
    ohlc_resp.json = AsyncMock(return_value=ohlc_rows)
    ohlc_resp.__aenter__ = AsyncMock(return_value=ohlc_resp)
    ohlc_resp.__aexit__ = AsyncMock(return_value=False)

    chart_resp = MagicMock()
    chart_resp.status = 200
    chart_resp.json = AsyncMock(return_value=market_chart)
    chart_resp.__aenter__ = AsyncMock(return_value=chart_resp)
    chart_resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    # get() is called concurrently twice; use side_effect list
    session.get = MagicMock(side_effect=[ohlc_resp, chart_resp])
    return session


# ---------------------------------------------------------------------------
# Unit tests: internal helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_to_datetime_date(self) -> None:
        dt = _to_datetime(date(2024, 1, 15))
        assert dt.tzinfo is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_to_datetime_naive_datetime(self) -> None:
        from datetime import datetime
        dt = _to_datetime(datetime(2024, 6, 1, 12, 0))
        assert dt.tzinfo == UTC

    def test_days_param_basic(self) -> None:
        days = _days_param(date(2024, 1, 1), date(2024, 1, 10))
        assert days == 10  # inclusive

    def test_days_param_min_one(self) -> None:
        days = _days_param(date(2024, 1, 5), date(2024, 1, 5))
        assert days == 1


# ---------------------------------------------------------------------------
# Unit tests: symbol mapping
# ---------------------------------------------------------------------------

class TestSymbolMapping:
    def test_known_symbols_present(self) -> None:
        for sym in ("BTC", "ETH", "SOL", "BNB", "ADA", "XRP", "AVAX", "DOT", "MATIC", "LINK"):
            assert sym in SYMBOL_TO_ID

    def test_btc_maps_to_bitcoin(self) -> None:
        assert SYMBOL_TO_ID["BTC"] == "bitcoin"

    def test_unknown_symbol_raises_provider_error(self) -> None:
        """Requesting an unknown symbol must raise ProviderError immediately."""
        provider = CoinGeckoProvider()
        with pytest.raises(ProviderError) as exc_info:
            provider._coin_id("FAKECOIN999")
        assert "FAKECOIN999" in str(exc_info.value)
        assert "Unknown symbol" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unit tests: get_ohlcv — happy path
# ---------------------------------------------------------------------------

class TestGetOhlcvHappyPath:
    @pytest.mark.asyncio
    async def test_returns_dataframe_correct_shape(self) -> None:
        """Successful fetch should return a DataFrame with 5 rows and OHLCV columns."""
        n = 5
        ohlc_rows = _make_ohlc_rows(n)
        market_chart = _make_market_chart(n)
        session = _mock_session(ohlc_rows, market_chart)

        provider = CoinGeckoProvider(session=session)

        with patch.object(provider, "_semaphore", new=MagicMock()) as sem:
            # Let the semaphore context manager be a no-op
            sem.__aenter__ = AsyncMock(return_value=None)
            sem.__aexit__ = AsyncMock(return_value=False)
            # Patch asyncio.sleep to avoid actual waiting
            with patch("hermes.data.coingecko_provider.asyncio.sleep", new=AsyncMock()):
                df = await provider.get_ohlcv(
                    symbol="BTC",
                    timeframe=Timeframe.D1,
                    start=date(2024, 1, 1),
                    end=date(2024, 1, 5),
                )

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        expected_cols = {"open", "high", "low", "close", "volume"}
        assert expected_cols.issubset(set(df.columns))
        assert df.index.tz is not None  # UTC-aware  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_index_is_utc(self) -> None:
        """DatetimeIndex must be UTC-aware."""
        ohlc_rows = _make_ohlc_rows(3)
        market_chart = _make_market_chart(3)
        session = _mock_session(ohlc_rows, market_chart)
        provider = CoinGeckoProvider(session=session)

        with patch.object(provider, "_semaphore", new=MagicMock()) as sem:
            sem.__aenter__ = AsyncMock(return_value=None)
            sem.__aexit__ = AsyncMock(return_value=False)
            with patch("hermes.data.coingecko_provider.asyncio.sleep", new=AsyncMock()):
                df = await provider.get_ohlcv(
                    symbol="ETH",
                    timeframe=Timeframe.D1,
                    start=date(2024, 1, 1),
                    end=date(2024, 1, 3),
                )

        assert str(df.index.tz) == "UTC"  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_columns_are_float64(self) -> None:
        """All OHLCV columns should be numeric."""
        ohlc_rows = _make_ohlc_rows(3)
        market_chart = _make_market_chart(3)
        session = _mock_session(ohlc_rows, market_chart)
        provider = CoinGeckoProvider(session=session)

        with patch.object(provider, "_semaphore", new=MagicMock()) as sem:
            sem.__aenter__ = AsyncMock(return_value=None)
            sem.__aexit__ = AsyncMock(return_value=False)
            with patch("hermes.data.coingecko_provider.asyncio.sleep", new=AsyncMock()):
                df = await provider.get_ohlcv(
                    symbol="SOL",
                    timeframe=Timeframe.D1,
                    start=date(2024, 1, 1),
                    end=date(2024, 1, 3),
                )

        for col in ("open", "high", "low", "close", "volume"):
            assert pd.api.types.is_numeric_dtype(df[col]), f"{col} should be numeric"


# ---------------------------------------------------------------------------
# Unit tests: get_ohlcv — error handling
# ---------------------------------------------------------------------------

class TestGetOhlcvErrors:
    @pytest.mark.asyncio
    async def test_unknown_symbol_raises_provider_error(self) -> None:
        """Fetching an unmapped symbol must raise ProviderError."""
        provider = CoinGeckoProvider()
        with pytest.raises(ProviderError) as exc_info:
            await provider.get_ohlcv(
                symbol="NOTREAL",
                timeframe=Timeframe.D1,
                start=date(2024, 1, 1),
                end=date(2024, 1, 5),
            )
        assert "NOTREAL" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_daily_timeframe_raises_provider_error(self) -> None:
        """Any timeframe other than D1 must raise ProviderError with a clear message."""
        provider = CoinGeckoProvider()

        for tf in (Timeframe.H1, Timeframe.H4, Timeframe.M5, Timeframe.M15, Timeframe.W1):
            with pytest.raises(ProviderError) as exc_info:
                await provider.get_ohlcv(
                    symbol="BTC",
                    timeframe=tf,
                    start=date(2024, 1, 1),
                    end=date(2024, 1, 5),
                )
            msg = str(exc_info.value)
            assert "daily" in msg.lower() or "D1" in msg, (
                f"Expected mention of D1/daily in error for {tf}, got: {msg}"
            )

    @pytest.mark.asyncio
    async def test_rate_limit_429_raises_rate_limit_error(self) -> None:
        """HTTP 429 must propagate as RateLimitError (subclass of ProviderError)."""
        rate_limited_resp = MagicMock()
        rate_limited_resp.status = 429
        rate_limited_resp.__aenter__ = AsyncMock(return_value=rate_limited_resp)
        rate_limited_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=rate_limited_resp)

        provider = CoinGeckoProvider(session=session)

        with patch.object(provider, "_semaphore", new=MagicMock()) as sem:
            sem.__aenter__ = AsyncMock(return_value=None)
            sem.__aexit__ = AsyncMock(return_value=False)
            with patch("hermes.data.coingecko_provider.asyncio.sleep", new=AsyncMock()):  # noqa: SIM117
                with pytest.raises(RateLimitError):
                    await provider.get_ohlcv(
                        symbol="BTC",
                        timeframe=Timeframe.D1,
                        start=date(2024, 1, 1),
                        end=date(2024, 1, 5),
                    )

    @pytest.mark.asyncio
    async def test_http_error_raises_provider_error(self) -> None:
        """Non-200, non-429 HTTP status must raise ProviderError after retries."""
        error_resp = MagicMock()
        error_resp.status = 500
        error_resp.text = AsyncMock(return_value="Internal Server Error")
        error_resp.__aenter__ = AsyncMock(return_value=error_resp)
        error_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        # Return error for all retry attempts
        session.get = MagicMock(return_value=error_resp)

        provider = CoinGeckoProvider(session=session)

        with patch.object(provider, "_semaphore", new=MagicMock()) as sem:
            sem.__aenter__ = AsyncMock(return_value=None)
            sem.__aexit__ = AsyncMock(return_value=False)
            with patch("hermes.data.coingecko_provider.asyncio.sleep", new=AsyncMock()):  # noqa: SIM117
                with pytest.raises(ProviderError) as exc_info:
                    await provider.get_ohlcv(
                        symbol="BTC",
                        timeframe=Timeframe.D1,
                        start=date(2024, 1, 1),
                        end=date(2024, 1, 5),
                    )
        assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Unit tests: is_tradeable
# ---------------------------------------------------------------------------

class TestIsTradeable:
    @pytest.mark.asyncio
    async def test_tradeable_when_usd_price_present(self) -> None:
        coin_data = {
            "market_data": {
                "current_price": {"usd": 42000.0}
            }
        }
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value=coin_data)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=resp)
        provider = CoinGeckoProvider(session=session)

        with patch.object(provider, "_semaphore", new=MagicMock()) as sem:
            sem.__aenter__ = AsyncMock(return_value=None)
            sem.__aexit__ = AsyncMock(return_value=False)
            with patch("hermes.data.coingecko_provider.asyncio.sleep", new=AsyncMock()):
                result = await provider.is_tradeable("BTC")

        assert result is True

    @pytest.mark.asyncio
    async def test_not_tradeable_when_no_price(self) -> None:
        coin_data: dict[str, Any] = {"market_data": {"current_price": {}}}
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value=coin_data)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=resp)
        provider = CoinGeckoProvider(session=session)

        with patch.object(provider, "_semaphore", new=MagicMock()) as sem:
            sem.__aenter__ = AsyncMock(return_value=None)
            sem.__aexit__ = AsyncMock(return_value=False)
            with patch("hermes.data.coingecko_provider.asyncio.sleep", new=AsyncMock()):
                result = await provider.is_tradeable("BTC")

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_symbol_raises_provider_error(self) -> None:
        provider = CoinGeckoProvider()
        with pytest.raises(ProviderError):
            await provider.is_tradeable("FAKECOIN")


# ---------------------------------------------------------------------------
# Unit tests: custom symbol map injection
# ---------------------------------------------------------------------------

class TestCustomSymbolMap:
    def test_custom_symbol_overrides_default(self) -> None:
        provider = CoinGeckoProvider(symbol_map={"MYTOKEN": "my-custom-token-id"})
        assert provider._coin_id("MYTOKEN") == "my-custom-token-id"

    def test_default_symbols_still_available_after_custom(self) -> None:
        provider = CoinGeckoProvider(symbol_map={"MYTOKEN": "my-custom-token-id"})
        assert provider._coin_id("BTC") == "bitcoin"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_implements_data_provider_protocol(self) -> None:
        from hermes.data.base import DataProvider
        provider = CoinGeckoProvider()
        assert isinstance(provider, DataProvider)

    def test_has_name_attribute(self) -> None:
        provider = CoinGeckoProvider()
        assert provider.name == "coingecko"


# ---------------------------------------------------------------------------
# Integration tests (require live network — skipped in CI by default)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCoinGeckoIntegration:
    @pytest.mark.asyncio
    async def test_live_btc_ohlcv(self) -> None:
        """Live call to CoinGecko — BTC daily data for last 7 days."""
        async with CoinGeckoProvider() as provider:
            df = await provider.get_ohlcv(
                symbol="BTC",
                timeframe=Timeframe.D1,
                start=date(2024, 1, 1),
                end=date(2024, 1, 7),
            )
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    @pytest.mark.asyncio
    async def test_live_is_tradeable_btc(self) -> None:
        """Live call — BTC should always be tradeable."""
        async with CoinGeckoProvider() as provider:
            result = await provider.is_tradeable("BTC")
        assert result is True
