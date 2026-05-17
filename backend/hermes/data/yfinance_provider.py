"""YFinance implementation of the DataProvider protocol.

All network calls are wrapped in asyncio.to_thread() so the async event loop
is never blocked.  A Semaphore caps concurrent yfinance requests at 2, and a
0.5-second inter-call delay prevents hammering Yahoo's unofficial API.
Transient failures are retried up to 3 times with exponential back-off.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Final

import pandas as pd
import yfinance as yf

from hermes.data.base import (
    OHLCV_COLUMNS,
    ProviderError,
    RateLimitError,
    Timeframe,
    validate_ohlcv,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CONCURRENT: Final[int] = 2
_CALL_DELAY: Final[float] = 0.5        # seconds between calls
_MAX_RETRIES: Final[int] = 3
_BACKOFF_BASE: Final[float] = 1.0      # 1 s, 2 s, 4 s

# Consecutive empty responses before we treat it as a rate-limit event
_EMPTY_LIMIT: Final[int] = 3

# Map Timeframe enum → yfinance interval string.
# H4 is not natively supported by yfinance; we fetch H1 and resample.
_TF_MAP: Final[dict[Timeframe, str]] = {
    Timeframe.M1:  "1m",
    Timeframe.M5:  "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1:  "60m",
    Timeframe.H4:  "60m",   # fetched as 1-h then resampled to 4-h
    Timeframe.D1:  "1d",
    Timeframe.W1:  "1wk",
}

# Known exchange suffixes we should try when a bare symbol returns nothing.
# Order matters: more common exchanges first.
_EXCHANGE_SUFFIXES: Final[list[str]] = [".T", ".HK", ".L", ".AX", ".TO", ".NS", ".BO"]

# ATR look-back window used for gap sanity check
_ATR_WINDOW: Final[int] = 14

# Maximum allowed single-bar close-to-close percent change (absolute)
_MAX_PCT_CHANGE: Final[float] = 0.50   # 50 %

# Gap threshold as multiple of ATR
_MAX_GAP_ATR_MULT: Final[float] = 3.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample a 1-h OHLCV DataFrame to 4-h bars."""
    if df.empty:
        return df
    resampled: pd.DataFrame = df.resample("4h", closed="left", label="left").agg(  # type: ignore[assignment]
        {
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }
    )
    return resampled.dropna()  # type: ignore[return-value]


def _compute_atr(df: pd.DataFrame, window: int = _ATR_WINDOW) -> pd.Series:
    """Compute ATR(window) for a standard OHLCV DataFrame."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window, min_periods=1).mean()  # type: ignore[return-value]


def _apply_sanity_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Drop bars that fail basic sanity checks.

    Rules
    -----
    1. Absolute close-to-close pct_change > 50 % → drop that bar.
    2. Open-to-prev-close gap > 3× ATR(14)       → drop that bar.
    """
    if df.empty:
        return df

    df = df.copy()

    # Rule 1 — large price moves
    pct = df["close"].pct_change().abs()
    bad_pct = pct > _MAX_PCT_CHANGE

    # Rule 2 — large gaps
    atr = _compute_atr(df)
    gap = (df["open"] - df["close"].shift(1)).abs()
    bad_gap = gap > (_MAX_GAP_ATR_MULT * atr)

    bad_mask = bad_pct | bad_gap
    n_dropped = int(bad_mask.sum())
    if n_dropped:
        logger.debug("Sanity filter dropped %d bar(s).", n_dropped)

    return df[~bad_mask]


def _normalise_columns(raw: pd.DataFrame) -> pd.DataFrame:
    """Lowercase yfinance column names and keep only OHLCV columns."""
    raw = raw.copy()
    raw.columns = [c.lower() for c in raw.columns]

    # yfinance sometimes returns 'adj close' — treat as 'close'
    if "close" not in raw.columns and "adj close" in raw.columns:
        raw = raw.rename(columns={"adj close": "close"})

    # Keep only what we need; missing columns will be caught by validate_ohlcv
    available = [c for c in OHLCV_COLUMNS if c in raw.columns]
    return raw[available]  # type: ignore[return-value]


def _download_sync(
    ticker_str: str,
    interval: str,
    start: date | datetime,
    end: date | datetime,
) -> pd.DataFrame:
    """Synchronous yfinance download — runs inside asyncio.to_thread()."""
    result: pd.DataFrame | None = yf.download(
        tickers=ticker_str,
        interval=interval,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
    )
    if result is None:
        return pd.DataFrame()
    return result


def _get_info_sync(ticker_str: str) -> dict:
    """Synchronous yf.Ticker.info fetch — runs inside asyncio.to_thread()."""
    return yf.Ticker(ticker_str).info


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class YFinanceProvider:
    """Async DataProvider backed by the yfinance library.

    Usage
    -----
    provider = YFinanceProvider()
    df = await provider.get_ohlcv("AAPL", Timeframe.D1, date(2024, 1, 1), date(2024, 6, 1))
    """

    name: str = "yfinance"

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT)
        self._empty_streak: dict[str, int] = {}   # symbol → consecutive-empty count

    # ------------------------------------------------------------------
    # Public API (DataProvider protocol)
    # ------------------------------------------------------------------

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date | datetime,
        end: date | datetime,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars and return a clean, UTC-indexed DataFrame.

        Returns an empty DataFrame when the symbol genuinely has no data.
        Raises ProviderError on unrecoverable failure.
        Raises RateLimitError after consecutive empty responses.
        """
        interval = _TF_MAP[timeframe]
        ticker_str = self._resolve_ticker(symbol)

        raw = await self._fetch_with_retry(ticker_str, interval, start, end)

        # Track consecutive empty responses
        if raw.empty:
            count = self._empty_streak.get(symbol, 0) + 1
            self._empty_streak[symbol] = count
            if count >= _EMPTY_LIMIT:
                self._empty_streak[symbol] = 0
                raise RateLimitError(
                    self.name,
                    symbol,
                    f"Received {count} consecutive empty responses — possible rate-limit.",
                )
            logger.debug("%s: no data returned for %s interval=%s.", symbol, timeframe, interval)
            return self._empty_df()
        else:
            self._empty_streak[symbol] = 0

        df = _normalise_columns(raw)

        # H4 resample
        if timeframe is Timeframe.H4:
            df = _resample_to_4h(df)

        df = _apply_sanity_filters(df)
        df = validate_ohlcv(df, symbol, self.name)
        return df

    async def is_tradeable(self, symbol: str) -> bool:
        """Return True if the symbol has a live market price (not delisted)."""
        ticker_str = self._resolve_ticker(symbol)
        try:
            info: dict = await asyncio.to_thread(_get_info_sync, ticker_str)
        except Exception as exc:
            logger.warning("is_tradeable(%s) info fetch failed: %s", symbol, exc)
            return False

        return info.get("regularMarketPrice") is not None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_ticker(self, symbol: str) -> str:
        """Return the yfinance ticker string for a symbol.

        If the symbol already contains a dot (e.g. "6758.T") we trust it as-is.
        Otherwise we return the bare symbol and let the retry logic attempt
        known suffixes when the download comes back empty.
        """
        return symbol  # suffix probing happens inside _fetch_with_retry

    async def _fetch_with_retry(
        self,
        ticker_str: str,
        interval: str,
        start: date | datetime,
        end: date | datetime,
    ) -> pd.DataFrame:
        """Download data, retrying on failure with exponential back-off.

        On the first attempt, uses the ticker as-is.  If the result is empty
        and the symbol has no dot suffix, sequentially probes known exchange
        suffixes before giving up.
        """
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                raw = await self._throttled_download(ticker_str, interval, start, end)
            except Exception as exc:
                last_exc = exc
                wait = _BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Download failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            if not raw.empty:
                return raw

            # First attempt came back empty and symbol has no suffix → probe suffixes
            if attempt == 0 and "." not in ticker_str:
                for suffix in _EXCHANGE_SUFFIXES:
                    candidate = ticker_str + suffix
                    logger.debug("Probing suffix ticker: %s", candidate)
                    try:
                        raw = await self._throttled_download(candidate, interval, start, end)
                    except Exception:
                        continue
                    if not raw.empty:
                        logger.info(
                            "Resolved %s → %s via suffix probe.",
                            ticker_str,
                            candidate,
                        )
                        return raw

            return raw   # genuinely empty after probing

        raise ProviderError(
            self.name,
            ticker_str,
            f"All {_MAX_RETRIES} download attempts failed: {last_exc}",
        )

    async def _throttled_download(
        self,
        ticker_str: str,
        interval: str,
        start: date | datetime,
        end: date | datetime,
    ) -> pd.DataFrame:
        """Acquire semaphore, sleep the inter-call delay, then download."""
        async with self._sem:
            await asyncio.sleep(_CALL_DELAY)
            return await asyncio.to_thread(
                _download_sync, ticker_str, interval, start, end
            )

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
