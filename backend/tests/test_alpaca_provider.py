"""
Unit tests for AlpacaProvider.

All tests mock the alpaca-py SDK so no live network calls are made.
Tests that require a live Alpaca connection are marked with @pytest.mark.integration.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from hermes.data.alpaca_provider import (
    _TIMEFRAME_MAP,
    PROVIDER_NAME,
    AlpacaProvider,
    _barset_to_dataframe,
    _is_crypto_symbol,
    _normalise_crypto_symbol,
    _to_utc_datetime,
)
from hermes.data.base import OHLCV_COLUMNS, ProviderError, RateLimitError, Timeframe

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine in a fresh event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_barset_df(
    symbol: str,
    n: int = 10,
    start: str = "2024-01-01",
    freq: str = "D",
    base_price: float = 150.0,
) -> pd.DataFrame:
    """
    Build a DataFrame that mimics the multi-index output of BarSet.df
    (levels: symbol, timestamp).
    """
    timestamps = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    prices = [base_price + i * 0.5 for i in range(n)]
    rows = []
    for ts, p in zip(timestamps, prices, strict=False):
        rows.append(
            {
                "symbol": symbol,
                "timestamp": ts,
                "open": p * 0.99,
                "high": p * 1.01,
                "low": p * 0.98,
                "close": p,
                "volume": 1_000_000.0,
                "trade_count": 5000,
                "vwap": p,
            }
        )
    df = pd.DataFrame(rows)
    df = df.set_index(["symbol", "timestamp"])
    return df


def _make_mock_barset(symbol: str, n: int = 10, **kwargs) -> MagicMock:
    """Return a MagicMock that mimics BarSet with a .df property."""
    mock_bs = MagicMock()
    mock_bs.df = _make_barset_df(symbol, n=n, **kwargs)
    return mock_bs


def _make_unconfigured_provider() -> AlpacaProvider:
    """Return an AlpacaProvider with empty API keys (simulates unconfigured state)."""
    provider = AlpacaProvider.__new__(AlpacaProvider)
    provider._api_key = ""
    provider._secret_key = ""
    provider._data_base_url = "https://data.alpaca.markets"
    provider._paper_base_url = "https://paper-api.alpaca.markets"
    provider._stock_client = None
    provider._crypto_client = None
    provider._trading_client = None
    return provider


def _make_configured_provider() -> AlpacaProvider:
    """Return an AlpacaProvider with fake (non-empty) credentials."""
    provider = AlpacaProvider.__new__(AlpacaProvider)
    provider._api_key = "FAKE_API_KEY"
    provider._secret_key = "FAKE_SECRET_KEY"
    provider._data_base_url = "https://data.alpaca.markets"
    provider._paper_base_url = "https://paper-api.alpaca.markets"
    provider._stock_client = None
    provider._crypto_client = None
    provider._trading_client = None
    return provider


# ---------------------------------------------------------------------------
# Unit tests: symbol classification helpers
# ---------------------------------------------------------------------------

class TestIsCryptoSymbol:

    def test_slash_notation_is_crypto(self) -> None:
        assert _is_crypto_symbol("BTC/USD") is True
        assert _is_crypto_symbol("ETH/USD") is True
        assert _is_crypto_symbol("SOL/USDT") is True

    def test_known_bare_symbols_are_crypto(self) -> None:
        for sym in ("BTC", "ETH", "SOL", "DOGE", "LTC"):
            assert _is_crypto_symbol(sym) is True, f"{sym} should be crypto"

    def test_unknown_bare_symbols_are_not_crypto(self) -> None:
        for sym in ("AAPL", "MSFT", "GOOG", "TSLA", "SPY"):
            assert _is_crypto_symbol(sym) is False, f"{sym} should NOT be crypto"

    def test_case_sensitive_bare_check(self) -> None:
        # Implementation uppercases before checking — both cases should be detected
        assert _is_crypto_symbol("btc") is True   # implementation calls .upper()
        assert _is_crypto_symbol("BTC") is True


class TestNormaliseCryptoSymbol:

    def test_bare_base_becomes_base_usd(self) -> None:
        assert _normalise_crypto_symbol("BTC") == "BTC/USD"
        assert _normalise_crypto_symbol("ETH") == "ETH/USD"

    def test_slash_notation_unchanged(self) -> None:
        assert _normalise_crypto_symbol("BTC/USD") == "BTC/USD"
        assert _normalise_crypto_symbol("ETH/USDT") == "ETH/USDT"

    def test_output_is_uppercased(self) -> None:
        assert _normalise_crypto_symbol("btc/usd") == "BTC/USD"


class TestToUtcDatetime:

    def test_date_becomes_midnight_utc(self) -> None:
        dt = _to_utc_datetime(date(2024, 6, 15))
        assert dt == datetime(2024, 6, 15, tzinfo=UTC)

    def test_naive_datetime_becomes_utc(self) -> None:
        naive = datetime(2024, 6, 15, 9, 30)
        result = _to_utc_datetime(naive)
        assert result.tzinfo is not None
        assert result == datetime(2024, 6, 15, 9, 30, tzinfo=UTC)

    def test_aware_datetime_converted_to_utc(self) -> None:
        import datetime as dt_mod
        from datetime import timezone as tz
        eastern = tz(dt_mod.timedelta(hours=-5))
        aware = datetime(2024, 6, 15, 9, 30, tzinfo=eastern)
        result = _to_utc_datetime(aware)
        assert result.tzinfo == dt_mod.UTC
        assert result.hour == 14  # 9:30 EST = 14:30 UTC


class TestBarsetToDataframe:

    def test_multiindex_returns_correct_dataframe(self) -> None:
        mock_bs = _make_mock_barset("AAPL", n=5)
        df = _barset_to_dataframe(mock_bs, "AAPL")
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 5

    def test_multiindex_crypto_symbol(self) -> None:
        mock_bs = _make_mock_barset("BTC/USD", n=3)
        df = _barset_to_dataframe(mock_bs, "BTC/USD")
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 3

    def test_empty_barset_returns_empty_df(self) -> None:
        mock_bs = MagicMock()
        mock_bs.df = pd.DataFrame()
        df = _barset_to_dataframe(mock_bs, "AAPL")
        assert df.empty
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Unit tests: equity fetch
# ---------------------------------------------------------------------------

class TestGetOhlcvEquity:

    def test_normal_equity_fetch_returns_correct_dataframe(self) -> None:
        """Normal equity fetch should return a properly shaped OHLCV DataFrame."""
        provider = _make_configured_provider()
        mock_bs = _make_mock_barset("AAPL", n=20)

        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = mock_bs

        with patch.object(provider, "_get_stock_client", return_value=mock_client):
            df = _run(
                provider.get_ohlcv("AAPL", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 31))
            )

        assert not df.empty, "Expected a non-empty DataFrame"
        assert list(df.columns) == OHLCV_COLUMNS
        assert df.index.tz is not None  # type: ignore[union-attr]
        assert str(df.index.tz) == "UTC"  # type: ignore[union-attr]
        assert len(df) == 20

    def test_equity_index_is_sorted_ascending(self) -> None:
        provider = _make_configured_provider()
        mock_bs = _make_mock_barset("MSFT", n=10)
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = mock_bs

        with patch.object(provider, "_get_stock_client", return_value=mock_client):
            df = _run(
                provider.get_ohlcv("MSFT", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 15))
            )

        assert df.index.is_monotonic_increasing

    def test_equity_fetch_calls_stock_client_not_crypto(self) -> None:
        """Equity symbols must use StockHistoricalDataClient."""
        provider = _make_configured_provider()
        mock_stock_client = MagicMock()
        mock_stock_client.get_stock_bars.return_value = _make_mock_barset("SPY", n=5)
        mock_crypto_client = MagicMock()

        with (
            patch.object(provider, "_get_stock_client", return_value=mock_stock_client),
            patch.object(provider, "_get_crypto_client", return_value=mock_crypto_client),
        ):
            _run(provider.get_ohlcv("SPY", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 10)))

        mock_stock_client.get_stock_bars.assert_called_once()
        mock_crypto_client.get_crypto_bars.assert_not_called()

    def test_empty_response_returns_empty_dataframe(self) -> None:
        """When Alpaca returns no bars, an empty DataFrame should be returned (not an error)."""
        provider = _make_configured_provider()
        empty_mock_bs = MagicMock()
        empty_mock_bs.df = pd.DataFrame()
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = empty_mock_bs

        with patch.object(provider, "_get_stock_client", return_value=mock_client):
            df = _run(
                provider.get_ohlcv("FAKE", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 5))
            )

        assert isinstance(df, pd.DataFrame)
        assert df.empty


# ---------------------------------------------------------------------------
# Unit tests: crypto fetch and client selection
# ---------------------------------------------------------------------------

class TestGetOhlcvCrypto:

    def test_slash_symbol_uses_crypto_client(self) -> None:
        """BTC/USD (slash notation) must use CryptoHistoricalDataClient."""
        provider = _make_configured_provider()
        mock_crypto_client = MagicMock()
        mock_crypto_client.get_crypto_bars.return_value = _make_mock_barset("BTC/USD", n=10)
        mock_stock_client = MagicMock()

        with (
            patch.object(provider, "_get_crypto_client", return_value=mock_crypto_client),
            patch.object(provider, "_get_stock_client", return_value=mock_stock_client),
        ):
            df = _run(
                provider.get_ohlcv("BTC/USD", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 15))
            )

        mock_crypto_client.get_crypto_bars.assert_called_once()
        mock_stock_client.get_stock_bars.assert_not_called()
        assert not df.empty
        assert list(df.columns) == OHLCV_COLUMNS

    def test_bare_btc_symbol_uses_crypto_client(self) -> None:
        """Bare 'BTC' symbol must be routed to CryptoHistoricalDataClient and mapped to BTC/USD."""
        provider = _make_configured_provider()
        mock_crypto_client = MagicMock()
        mock_crypto_client.get_crypto_bars.return_value = _make_mock_barset("BTC/USD", n=5)
        mock_stock_client = MagicMock()

        with (
            patch.object(provider, "_get_crypto_client", return_value=mock_crypto_client),
            patch.object(provider, "_get_stock_client", return_value=mock_stock_client),
        ):
            _run(
                provider.get_ohlcv("BTC", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 10))
            )

        mock_crypto_client.get_crypto_bars.assert_called_once()
        mock_stock_client.get_stock_bars.assert_not_called()
        # Verify the CryptoBarsRequest was constructed with 'BTC/USD'
        call_args = mock_crypto_client.get_crypto_bars.call_args
        request_obj = call_args[0][0]  # first positional argument
        assert request_obj.symbol_or_symbols == "BTC/USD"

    def test_crypto_returns_correct_dataframe(self) -> None:
        """Crypto fetch should return a properly shaped OHLCV DataFrame."""
        provider = _make_configured_provider()
        mock_crypto_client = MagicMock()
        mock_crypto_client.get_crypto_bars.return_value = _make_mock_barset("ETH/USD", n=15)

        with patch.object(provider, "_get_crypto_client", return_value=mock_crypto_client):
            df = _run(
                provider.get_ohlcv("ETH/USD", Timeframe.H1, date(2024, 1, 1), date(2024, 1, 5))
            )

        assert not df.empty
        assert list(df.columns) == OHLCV_COLUMNS
        assert df.index.tz is not None  # type: ignore[union-attr]
        assert str(df.index.tz) == "UTC"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Unit tests: unconfigured credentials
# ---------------------------------------------------------------------------

class TestUnconfiguredCredentials:

    def test_get_ohlcv_raises_provider_error_with_helpful_message(self) -> None:
        """Empty API keys must raise ProviderError with a clear setup instruction."""
        provider = _make_unconfigured_provider()

        with pytest.raises(ProviderError) as exc_info:
            _run(
                provider.get_ohlcv("AAPL", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 31))
            )

        err = exc_info.value
        assert err.provider == PROVIDER_NAME
        # Message should guide the user toward fixing their .env
        assert "ALPACA_PAPER_API_KEY" in str(err) or "not configured" in str(err).lower()

    def test_is_tradeable_raises_provider_error(self) -> None:
        """is_tradeable() must also raise ProviderError when keys are missing."""
        provider = _make_unconfigured_provider()

        with pytest.raises(ProviderError) as exc_info:
            _run(provider.is_tradeable("AAPL"))

        assert exc_info.value.provider == PROVIDER_NAME

    def test_error_message_mentions_secret_key(self) -> None:
        """Error message should mention both env vars so the user knows what to set."""
        provider = _make_unconfigured_provider()

        with pytest.raises(ProviderError) as exc_info:
            _run(
                provider.get_ohlcv("AAPL", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 5))
            )

        msg = str(exc_info.value)
        assert "ALPACA_PAPER_SECRET_KEY" in msg or ".env" in msg


# ---------------------------------------------------------------------------
# Unit tests: rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:

    def test_http_429_retries_and_raises_rate_limit_error(self) -> None:
        """
        When the Alpaca API returns 429, provider should retry _RETRY_LIMIT times
        then raise RateLimitError.
        """
        from alpaca.common.exceptions import APIError

        provider = _make_configured_provider()

        # Build a proper APIError with a mock http_error that has status_code=429
        mock_http_error = MagicMock()
        mock_http_error.response.status_code = 429
        api_error = APIError({"message": "too many requests"}, http_error=mock_http_error)

        mock_client = MagicMock()
        mock_client.get_stock_bars.side_effect = api_error

        with (  # noqa: SIM117
            patch.object(provider, "_get_stock_client", return_value=mock_client),
            patch("hermes.data.alpaca_provider.asyncio.sleep", new_callable=AsyncMock),
        ):
            with pytest.raises(RateLimitError):
                _run(
                    provider.get_ohlcv(
                        "AAPL", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 31)
                    )
                )

        # Should have been called (1 initial + _RETRY_LIMIT retries)
        from hermes.data.alpaca_provider import _RETRY_LIMIT
        assert mock_client.get_stock_bars.call_count == _RETRY_LIMIT + 1

    def test_other_api_error_raises_provider_error_immediately(self) -> None:
        """Non-429 API errors should raise ProviderError without retrying."""
        from alpaca.common.exceptions import APIError

        provider = _make_configured_provider()

        mock_http_error = MagicMock()
        mock_http_error.response.status_code = 404
        api_error = APIError({"message": "symbol not found"}, http_error=mock_http_error)

        mock_client = MagicMock()
        mock_client.get_stock_bars.side_effect = api_error

        with patch.object(provider, "_get_stock_client", return_value=mock_client):  # noqa: SIM117
            with pytest.raises(ProviderError) as exc_info:
                _run(
                    provider.get_ohlcv(
                        "NOPE", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 5)
                    )
                )

        # Should NOT be a RateLimitError
        assert type(exc_info.value) is ProviderError
        # Should NOT retry
        mock_client.get_stock_bars.assert_called_once()


# ---------------------------------------------------------------------------
# Unit tests: is_tradeable
# ---------------------------------------------------------------------------

class TestIsTradeable:

    def test_tradable_asset_returns_true(self) -> None:
        provider = _make_configured_provider()

        mock_asset = MagicMock()
        mock_asset.tradable = True

        mock_client = MagicMock()
        mock_client.get_asset.return_value = mock_asset

        with patch.object(provider, "_get_trading_client", return_value=mock_client):
            result = _run(provider.is_tradeable("AAPL"))

        assert result is True

    def test_non_tradable_asset_returns_false(self) -> None:
        provider = _make_configured_provider()

        mock_asset = MagicMock()
        mock_asset.tradable = False

        mock_client = MagicMock()
        mock_client.get_asset.return_value = mock_asset

        with patch.object(provider, "_get_trading_client", return_value=mock_client):
            result = _run(provider.is_tradeable("DEAD"))

        assert result is False

    def test_unknown_asset_returns_false(self) -> None:
        """When get_asset raises an error, is_tradeable should return False."""
        from alpaca.common.exceptions import APIError

        provider = _make_configured_provider()

        mock_http_error = MagicMock()
        mock_http_error.response.status_code = 404
        api_error = APIError({"message": "asset not found"}, http_error=mock_http_error)

        mock_client = MagicMock()
        mock_client.get_asset.side_effect = api_error

        with patch.object(provider, "_get_trading_client", return_value=mock_client):
            result = _run(provider.is_tradeable("GHOST"))

        assert result is False

    def test_crypto_slash_stripped_for_trading_lookup(self) -> None:
        """BTC/USD should be looked up as BTCUSD on the trading client."""
        provider = _make_configured_provider()

        mock_asset = MagicMock()
        mock_asset.tradable = True

        mock_client = MagicMock()
        mock_client.get_asset.return_value = mock_asset

        with patch.object(provider, "_get_trading_client", return_value=mock_client):
            _run(provider.is_tradeable("BTC/USD"))

        call_args = mock_client.get_asset.call_args
        lookup_sym = call_args[0][0]
        assert lookup_sym == "BTCUSD"


# ---------------------------------------------------------------------------
# Unit tests: timeframe mapping
# ---------------------------------------------------------------------------

class TestTimeframeMapping:

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
    def test_all_timeframes_are_mapped(self, tf: Timeframe) -> None:
        """Every Timeframe value must be present in _TIMEFRAME_MAP."""
        assert tf in _TIMEFRAME_MAP, f"Timeframe {tf!r} is not in _TIMEFRAME_MAP"

    @pytest.mark.parametrize(
        "tf",
        [
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.D1,
            Timeframe.W1,
        ],
    )
    def test_timeframe_accepted_in_get_ohlcv(self, tf: Timeframe) -> None:
        """get_ohlcv should not raise for any valid Timeframe enum value."""
        provider = _make_configured_provider()
        freq = "h" if tf not in {Timeframe.D1, Timeframe.W1} else "D"
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = _make_mock_barset("AAPL", n=5, freq=freq)

        with patch.object(provider, "_get_stock_client", return_value=mock_client):
            df = _run(
                provider.get_ohlcv("AAPL", tf, date(2024, 1, 1), date(2024, 1, 31))
            )

        assert isinstance(df, pd.DataFrame)


# ---------------------------------------------------------------------------
# Unit tests: DataProvider protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:

    def test_implements_data_provider_protocol(self) -> None:
        """AlpacaProvider must satisfy the DataProvider runtime_checkable protocol."""
        from hermes.data.base import DataProvider

        provider = AlpacaProvider.__new__(AlpacaProvider)
        assert isinstance(provider, DataProvider)

    def test_has_name_attribute(self) -> None:
        provider = AlpacaProvider.__new__(AlpacaProvider)
        assert hasattr(provider, "name")
        assert isinstance(provider.name, str)
        assert provider.name == PROVIDER_NAME


# ---------------------------------------------------------------------------
# Integration tests (require live Alpaca API credentials + network)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_live_aapl_daily() -> None:
    """Fetch real AAPL daily bars. Requires ALPACA_PAPER_API_KEY and network."""
    provider = AlpacaProvider()
    df = _run(
        provider.get_ohlcv("AAPL", Timeframe.D1, date(2024, 1, 1), date(2024, 3, 31))
    )
    assert not df.empty
    assert list(df.columns) == OHLCV_COLUMNS
    assert df.index.tz is not None  # type: ignore[union-attr]


@pytest.mark.integration
def test_live_btc_usd_daily() -> None:
    """Fetch real BTC/USD daily bars. Requires credentials + network."""
    provider = AlpacaProvider()
    df = _run(
        provider.get_ohlcv("BTC/USD", Timeframe.D1, date(2024, 1, 1), date(2024, 3, 31))
    )
    assert not df.empty
    assert list(df.columns) == OHLCV_COLUMNS


@pytest.mark.integration
def test_live_is_tradeable_aapl() -> None:
    """Check AAPL is tradeable via live Alpaca API."""
    provider = AlpacaProvider()
    result = _run(provider.is_tradeable("AAPL"))
    assert result is True


@pytest.mark.integration
def test_live_bare_btc_symbol() -> None:
    """Bare 'BTC' symbol should be mapped to BTC/USD and return data."""
    provider = AlpacaProvider()
    df = _run(
        provider.get_ohlcv("BTC", Timeframe.D1, date(2024, 1, 1), date(2024, 1, 31))
    )
    assert not df.empty
    assert list(df.columns) == OHLCV_COLUMNS
