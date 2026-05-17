"""
AlpacaProvider — DataProvider implementation backed by the Alpaca Market Data API.

Supports:
  - US equities via StockHistoricalDataClient
  - Crypto via CryptoHistoricalDataClient (BTC/USD, ETH/USD, etc.)
  - Rate limiting: asyncio.Semaphore(10) + 429 retry with 60 s backoff
  - is_tradeable(): delegates to TradingClient.get_asset().tradable
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Final

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.models.bars import BarSet
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from hermes.config import settings
from hermes.data.base import (
    ProviderError,
    RateLimitError,
    Timeframe,
    validate_ohlcv,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROVIDER_NAME: Final[str] = "alpaca"

# Symbols we treat as crypto when given bare (no '/') — extend as needed
_KNOWN_CRYPTO_BASE: Final[frozenset[str]] = frozenset(
    {
        "BTC", "ETH", "SOL", "DOGE", "LTC", "BCH", "LINK", "UNI",
        "AAVE", "AVAX", "MATIC", "XRP", "ADA", "DOT", "ATOM",
        "ALGO", "XTZ", "BAT", "MKR", "COMP", "YFI", "SUSHI",
        "CRV", "SNX", "GRT", "FIL", "ETC", "TRX", "XLM",
    }
)

# Default quote currency appended when a bare crypto base is given
_DEFAULT_QUOTE: Final[str] = "USD"

# Semaphore: keep concurrent Alpaca API calls within free-tier safety margin
# (200 req/min ≈ ~3 req/s;  10 concurrent calls with a 0.1 s sleep ≈ 100 req/s max)
_SEMAPHORE = asyncio.Semaphore(10)
_SLEEP_BETWEEN_CALLS: Final[float] = 0.05   # seconds
_RETRY_LIMIT: Final[int] = 2                 # attempts beyond the first
_RATE_LIMIT_BACKOFF: Final[float] = 60.0    # seconds to wait on HTTP 429

# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------

def _make_tf(amount: int, unit: str) -> TimeFrame:
    """Construct a TimeFrame, satisfying static analysers that want TimeFrameUnit."""
    return TimeFrame(amount, unit)  # type: ignore[arg-type]


_TIMEFRAME_MAP: dict[Timeframe, TimeFrame] = {
    Timeframe.M1:  _make_tf(1,  TimeFrameUnit.Minute),
    Timeframe.M5:  _make_tf(5,  TimeFrameUnit.Minute),
    Timeframe.M15: _make_tf(15, TimeFrameUnit.Minute),
    Timeframe.M30: _make_tf(30, TimeFrameUnit.Minute),
    Timeframe.H1:  _make_tf(1,  TimeFrameUnit.Hour),
    Timeframe.H4:  _make_tf(4,  TimeFrameUnit.Hour),
    Timeframe.D1:  _make_tf(1,  TimeFrameUnit.Day),
    Timeframe.W1:  _make_tf(1,  TimeFrameUnit.Week),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_crypto_symbol(symbol: str) -> bool:
    """Return True for symbols that should be routed to CryptoHistoricalDataClient."""
    if "/" in symbol:
        return True
    return symbol.upper() in _KNOWN_CRYPTO_BASE


def _normalise_crypto_symbol(symbol: str) -> str:
    """Convert 'BTC' → 'BTC/USD';  'BTC/USD' stays as-is."""
    symbol = symbol.upper()
    if "/" not in symbol:
        return f"{symbol}/{_DEFAULT_QUOTE}"
    return symbol


def _to_utc_datetime(dt: date | datetime) -> datetime:
    """Coerce a date or datetime to a UTC-aware datetime."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    # plain date → midnight UTC
    return datetime(dt.year, dt.month, dt.day, tzinfo=UTC)


def _barset_to_dataframe(barset: BarSet, symbol: str) -> pd.DataFrame:
    """
    Convert an Alpaca BarSet to a single-symbol DataFrame with a DatetimeIndex.

    The BarSet.df property returns a MultiIndex (symbol, timestamp) frame.
    We drop the symbol level and keep only OHLCV columns.
    """
    df: pd.DataFrame = barset.df
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # If MultiIndex, extract this symbol's rows
    if isinstance(df.index, pd.MultiIndex):
        # The first level is the symbol string (may use ALPACA format e.g. 'BTC/USD')
        available_symbols = df.index.get_level_values(0).unique().tolist()
        # Try exact match first, then case-insensitive
        matched = symbol if symbol in available_symbols else None
        if matched is None:
            sym_upper = symbol.upper()
            for s in available_symbols:
                if s.upper() == sym_upper:
                    matched = s
                    break
        if matched is None and len(available_symbols) == 1:
            matched = available_symbols[0]
        if matched is None:
            log.warning("Symbol %s not found in BarSet (available: %s)", symbol, available_symbols)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = df.xs(matched, level=0)  # type: ignore[assignment]

    # Rename columns to lowercase to match OHLCV_COLUMNS
    df.columns = [c.lower() for c in df.columns]

    # Ensure the index is a DatetimeIndex named 'timestamp'
    df.index.name = "timestamp"

    # Keep only the columns we care about (BarSet may also include vwap, trade_count)
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ProviderError(PROVIDER_NAME, symbol, f"Expected column '{col}' missing from BarSet")

    return df[["open", "high", "low", "close", "volume"]].copy()


# ---------------------------------------------------------------------------
# AlpacaProvider
# ---------------------------------------------------------------------------

class AlpacaProvider:
    """
    DataProvider backed by the Alpaca Market Data API (alpaca-py SDK).

    The Alpaca SDK clients are synchronous; we run blocking calls in the
    default ThreadPoolExecutor via asyncio.to_thread() so the event loop
    stays unblocked.
    """

    name: str = PROVIDER_NAME

    def __init__(self) -> None:
        self._api_key: str = settings.alpaca_paper_api_key
        self._secret_key: str = settings.alpaca_paper_secret_key
        self._data_base_url: str = settings.alpaca_data_base_url
        self._paper_base_url: str = settings.alpaca_paper_base_url

        # Lazy-initialised clients (created on first use after credential check)
        self._stock_client: StockHistoricalDataClient | None = None
        self._crypto_client: CryptoHistoricalDataClient | None = None
        self._trading_client: TradingClient | None = None

    # ------------------------------------------------------------------
    # Credential validation
    # ------------------------------------------------------------------

    def _require_credentials(self) -> None:
        """Raise ProviderError immediately if API keys are not configured."""
        if not self._api_key or not self._secret_key:
            raise ProviderError(
                PROVIDER_NAME,
                "(init)",
                "Alpaca API keys are not configured. "
                "Set ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY in your .env file "
                "or pass them via environment variables.",
            )

    # ------------------------------------------------------------------
    # Client accessors (lazy, thread-safe enough for single-process use)
    # ------------------------------------------------------------------

    def _get_stock_client(self) -> StockHistoricalDataClient:
        self._require_credentials()
        if self._stock_client is None:
            self._stock_client = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                url_override=self._data_base_url or None,
            )
        return self._stock_client

    def _get_crypto_client(self) -> CryptoHistoricalDataClient:
        self._require_credentials()
        if self._crypto_client is None:
            self._crypto_client = CryptoHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                url_override=self._data_base_url or None,
            )
        return self._crypto_client

    def _get_trading_client(self) -> TradingClient:
        self._require_credentials()
        if self._trading_client is None:
            self._trading_client = TradingClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                paper=True,
                url_override=self._paper_base_url or None,
            )
        return self._trading_client

    # ------------------------------------------------------------------
    # Core retry logic
    # ------------------------------------------------------------------

    async def _call_with_retry(self, symbol: str, fn, *args, **kwargs):
        """
        Execute a synchronous Alpaca SDK call in a thread, with:
          - asyncio.Semaphore to cap concurrent inflight requests
          - Retry on HTTP 429 (up to _RETRY_LIMIT retries) with 60 s backoff
          - ProviderError wrapping on unrecoverable failures
        """
        last_error: Exception | None = None

        for attempt in range(_RETRY_LIMIT + 1):
            async with _SEMAPHORE:
                try:
                    result = await asyncio.to_thread(fn, *args, **kwargs)
                    await asyncio.sleep(_SLEEP_BETWEEN_CALLS)
                    return result
                except APIError as exc:
                    status = getattr(exc, "status_code", None)
                    if status == 429:
                        last_error = exc
                        if attempt < _RETRY_LIMIT:
                            log.warning(
                                "[alpaca] Rate-limited on %s (attempt %d/%d). "
                                "Backing off %.0f s.",
                                symbol, attempt + 1, _RETRY_LIMIT + 1, _RATE_LIMIT_BACKOFF,
                            )
                            await asyncio.sleep(_RATE_LIMIT_BACKOFF)
                            continue
                        raise RateLimitError(
                            PROVIDER_NAME,
                            symbol,
                            f"Rate-limited after {_RETRY_LIMIT} retries. "
                            "Wait before retrying.",
                        ) from exc
                    # Any other API error is unrecoverable
                    raise ProviderError(
                        PROVIDER_NAME,
                        symbol,
                        f"Alpaca API error (HTTP {status}): {exc}",
                    ) from exc
                except Exception as exc:
                    raise ProviderError(
                        PROVIDER_NAME,
                        symbol,
                        f"Unexpected error fetching data: {exc}",
                    ) from exc

        # Should be unreachable, but just in case
        raise RateLimitError(
            PROVIDER_NAME,
            symbol,
            f"Exhausted {_RETRY_LIMIT} retries due to rate limiting.",
        ) from last_error

    # ------------------------------------------------------------------
    # DataProvider protocol
    # ------------------------------------------------------------------

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date | datetime,
        end: date | datetime,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV bars for *symbol* from Alpaca.

        Crypto symbols (contains '/' or bare base like 'BTC') are routed to
        CryptoHistoricalDataClient; everything else to StockHistoricalDataClient.

        Returns a DataFrame with DatetimeIndex (UTC) and columns:
            open, high, low, close, volume
        Returns an empty DataFrame (not None) if no data is available.
        Raises ProviderError / RateLimitError on failure.
        """
        alpaca_tf = _TIMEFRAME_MAP.get(timeframe)
        if alpaca_tf is None:
            raise ProviderError(
                PROVIDER_NAME,
                symbol,
                f"Unsupported timeframe: {timeframe!r}. "
                f"Supported: {list(_TIMEFRAME_MAP.keys())}",
            )

        start_dt = _to_utc_datetime(start)
        end_dt = _to_utc_datetime(end)

        if _is_crypto_symbol(symbol):
            return await self._fetch_crypto_bars(symbol, alpaca_tf, start_dt, end_dt)
        return await self._fetch_stock_bars(symbol, alpaca_tf, start_dt, end_dt)

    async def _fetch_stock_bars(
        self,
        symbol: str,
        alpaca_tf: TimeFrame,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        client = self._get_stock_client()
        request = StockBarsRequest(
            symbol_or_symbols=symbol.upper(),
            timeframe=alpaca_tf,
            start=start,
            end=end,
        )
        log.debug("[alpaca] Fetching equity bars: %s %s %s→%s", symbol, alpaca_tf, start, end)

        barset: BarSet = await self._call_with_retry(
            symbol, client.get_stock_bars, request
        )

        df = _barset_to_dataframe(barset, symbol.upper())
        return validate_ohlcv(df, symbol, PROVIDER_NAME)

    async def _fetch_crypto_bars(
        self,
        symbol: str,
        alpaca_tf: TimeFrame,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        alpaca_symbol = _normalise_crypto_symbol(symbol)
        client = self._get_crypto_client()
        request = CryptoBarsRequest(
            symbol_or_symbols=alpaca_symbol,
            timeframe=alpaca_tf,
            start=start,
            end=end,
        )
        log.debug("[alpaca] Fetching crypto bars: %s %s %s→%s", alpaca_symbol, alpaca_tf, start, end)

        barset: BarSet = await self._call_with_retry(
            alpaca_symbol, client.get_crypto_bars, request
        )

        df = _barset_to_dataframe(barset, alpaca_symbol)
        return validate_ohlcv(df, alpaca_symbol, PROVIDER_NAME)

    async def is_tradeable(self, symbol: str) -> bool:
        """
        Return True if the asset is currently tradable on Alpaca.

        For crypto symbols the trading client asset lookup may fail; in that
        case we return True optimistically (Alpaca crypto is always active).
        """
        self._require_credentials()
        client = self._get_trading_client()

        lookup_symbol = symbol.upper()
        # Alpaca trading client expects crypto in 'BTCUSD' format (no slash)
        if "/" in lookup_symbol:
            lookup_symbol = lookup_symbol.replace("/", "")

        try:
            asset = await self._call_with_retry(
                symbol, client.get_asset, lookup_symbol
            )
            tradable: bool = bool(getattr(asset, "tradable", False))
            return tradable
        except ProviderError as exc:
            # Asset not found → not tradeable
            log.debug("[alpaca] is_tradeable(%s) raised ProviderError: %s", symbol, exc)
            return False
