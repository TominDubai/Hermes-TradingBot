from __future__ import annotations

from abc import abstractmethod
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

import pandas as pd


class Timeframe(StrEnum):
    """Canonical timeframe strings used across all providers."""
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1wk"


# Required columns every provider must return
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


@runtime_checkable
class DataProvider(Protocol):
    """
    Contract all data providers must satisfy.

    Returns a DataFrame with:
      - DatetimeIndex (UTC-aware)
      - columns: open, high, low, close, volume (all float64)
      - no NaN rows
      - sorted ascending by time
    """

    name: str  # e.g. "yfinance", "alpaca", "coingecko"

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date | datetime,
        end: date | datetime,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV bars for a symbol.

        Args:
            symbol:    Ticker in provider-native format (e.g. "AAPL", "BTC/USD")
            timeframe: Timeframe enum value
            start:     Inclusive start (date or tz-aware datetime)
            end:       Inclusive end

        Returns:
            DataFrame with DatetimeIndex (UTC) and OHLCV_COLUMNS.
            Empty DataFrame (not None) if no data available.

        Raises:
            ProviderError: on unrecoverable fetch failure
            RateLimitError: when rate-limited (caller should back off)
        """
        ...

    @abstractmethod
    async def is_tradeable(self, symbol: str) -> bool:
        """Return True if the symbol is currently active / not delisted."""
        ...


class ProviderError(Exception):
    """Unrecoverable error from a data provider."""
    def __init__(self, provider: str, symbol: str, message: str) -> None:
        self.provider = provider
        self.symbol = symbol
        super().__init__(f"[{provider}] {symbol}: {message}")


class RateLimitError(ProviderError):
    """Provider returned a rate-limit response. Caller should back off."""


def validate_ohlcv(df: pd.DataFrame, symbol: str, provider: str) -> pd.DataFrame:
    """
    Validate and normalise a provider's OHLCV output.
    - Ensures all required columns present
    - Drops NaN rows
    - Sorts ascending
    - Converts index to UTC
    """
    if df.empty:
        return df

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ProviderError(provider, symbol, f"Missing columns: {missing}")

    df = df[OHLCV_COLUMNS].copy()
    df = df.dropna()
    df = df.sort_index()

    # Ensure UTC timezone
    idx = df.index  # type: ignore[assignment]
    if idx.tz is None:
        df.index = idx.tz_localize("UTC")  # type: ignore[assignment]
    else:
        df.index = idx.tz_convert("UTC")  # type: ignore[assignment]

    return df
