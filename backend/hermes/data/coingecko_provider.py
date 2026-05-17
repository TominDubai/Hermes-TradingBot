"""CoinGecko free-tier DataProvider implementation.

Supports daily (D1) OHLCV only — CoinGecko's free API does not expose
sub-daily candles for arbitrary date ranges.

Rate limits: ~10-30 req/min on the free tier.
Strategy: asyncio.Semaphore(5) + 2 s sleep after every successful request.
Retries: 3 attempts with exponential back-off; 429 → RateLimitError.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any

import aiohttp
import pandas as pd

from hermes.data.base import (
    ProviderError,
    RateLimitError,
    Timeframe,
    validate_ohlcv,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol → CoinGecko coin-ID mapping
# ---------------------------------------------------------------------------
SYMBOL_TO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "ADA": "cardano",
    "XRP": "ripple",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    # Extended universe
    "LTC": "litecoin",
    "ATOM": "cosmos",
    "UNI": "uniswap",
    "ALGO": "algorand",
    "XLM": "stellar",
    "FIL": "filecoin",
    "NEAR": "near",
    "ICP": "internet-computer",
    "HBAR": "hedera-hashgraph",
    "VET": "vechain",
    "SAND": "the-sandbox",
    "MANA": "decentraland",
    "AXS": "axie-infinity",
    "THETA": "theta-token",
    "EGLD": "elrond-erd-2",
    "FTM": "fantom",
    "AAVE": "aave",
    "EOS": "eos",
    "XTZ": "tezos",
    "CAKE": "pancakeswap-token",
}

_BASE_URL = "https://api.coingecko.com/api/v3"
_MAX_RETRIES = 3
_SLEEP_BETWEEN_REQUESTS = 2.0  # seconds — free tier courtesy delay


def _to_datetime(d: date | datetime) -> datetime:
    """Coerce a date or datetime to a UTC-aware datetime."""
    if isinstance(d, datetime):
        if d.tzinfo is None:
            return d.replace(tzinfo=UTC)
        return d.astimezone(UTC)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _days_param(start: date | datetime, end: date | datetime) -> int:
    """Calculate the 'days' query param from a date range (minimum 1)."""
    dt_start = _to_datetime(start)
    dt_end = _to_datetime(end)
    delta = (dt_end - dt_start).days + 1
    return max(delta, 1)


class CoinGeckoProvider:
    """Async DataProvider backed by the CoinGecko free public API.

    Usage::

        async with CoinGeckoProvider() as provider:
            df = await provider.get_ohlcv("BTC", Timeframe.D1, date(2024,1,1), date(2024,3,1))
    """

    name: str = "coingecko"

    def __init__(
        self,
        *,
        symbol_map: dict[str, str] | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._symbol_map: dict[str, str] = {
            **SYMBOL_TO_ID,
            **(symbol_map or {}),
        }
        self._session: aiohttp.ClientSession | None = session
        self._own_session: bool = session is None
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(5)

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> CoinGeckoProvider:
        if self._own_session:
            self._session = aiohttp.ClientSession(
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _coin_id(self, symbol: str) -> str:
        """Resolve a ticker symbol to a CoinGecko coin ID.

        Raises:
            ProviderError: if the symbol is not in the mapping.
        """
        key = symbol.upper().strip()
        try:
            return self._symbol_map[key]
        except KeyError:
            known = ", ".join(sorted(self._symbol_map))
            raise ProviderError(
                self.name,
                symbol,
                f"Unknown symbol '{symbol}'. Known symbols: {known}",
            ) from None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError(
                "CoinGeckoProvider must be used as an async context manager "
                "or have a session injected."
            )
        return self._session

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        symbol: str = "<unknown>",
    ) -> Any:
        """Perform a rate-limited, retried GET request.

        Args:
            path:   URL path relative to _BASE_URL (must start with /).
            params: Query parameters.
            symbol: Symbol name — used in error messages only.

        Returns:
            Parsed JSON response (dict or list).

        Raises:
            RateLimitError: on HTTP 429.
            ProviderError:  on unrecoverable HTTP errors or network failures.
        """
        session = self._ensure_session()
        url = f"{_BASE_URL}{path}"

        for attempt in range(1, _MAX_RETRIES + 1):
            async with self._semaphore:
                try:
                    async with session.get(url, params=params) as resp:
                        if resp.status == 429:
                            raise RateLimitError(
                                self.name,
                                symbol,
                                "Rate limited by CoinGecko (HTTP 429). Back off and retry.",
                            )
                        if resp.status >= 400:
                            text = await resp.text()
                            raise ProviderError(
                                self.name,
                                symbol,
                                f"HTTP {resp.status}: {text[:200]}",
                            )
                        data = await resp.json(content_type=None)
                        await asyncio.sleep(_SLEEP_BETWEEN_REQUESTS)
                        return data

                except RateLimitError:
                    raise  # never retry on 429 — let caller decide

                except ProviderError:
                    if attempt == _MAX_RETRIES:
                        raise
                    backoff = 2 ** attempt
                    logger.warning(
                        "[coingecko] %s: attempt %d/%d failed, retrying in %ds",
                        symbol,
                        attempt,
                        _MAX_RETRIES,
                        backoff,
                    )
                    await asyncio.sleep(backoff)

                except aiohttp.ClientError as exc:
                    if attempt == _MAX_RETRIES:
                        raise ProviderError(
                            self.name, symbol, f"Network error: {exc}"
                        ) from exc
                    backoff = 2 ** attempt
                    logger.warning(
                        "[coingecko] %s: network error on attempt %d/%d: %s",
                        symbol,
                        attempt,
                        _MAX_RETRIES,
                        exc,
                    )
                    await asyncio.sleep(backoff)

        # Should never reach here
        raise ProviderError(self.name, symbol, "All retry attempts exhausted.")

    async def _fetch_ohlc(
        self, coin_id: str, days: int, symbol: str
    ) -> list[list[float]]:
        """Fetch raw OHLC rows from /coins/{id}/ohlc.

        Returns a list of [timestamp_ms, open, high, low, close].
        """
        data = await self._get(
            f"/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": str(days)},
            symbol=symbol,
        )
        if not isinstance(data, list):
            raise ProviderError(
                self.name, symbol, f"Unexpected OHLC response type: {type(data)}"
            )
        return data  # type: ignore[return-value]

    async def _fetch_volumes(
        self, coin_id: str, days: int, symbol: str
    ) -> dict[int, float]:
        """Fetch daily volumes from /coins/{id}/market_chart.

        Returns a dict mapping timestamp_ms (rounded to day) → volume.
        """
        data = await self._get(
            f"/coins/{coin_id}/market_chart",
            params={
                "vs_currency": "usd",
                "days": str(days),
                "interval": "daily",
            },
            symbol=symbol,
        )
        if not isinstance(data, dict) or "total_volumes" not in data:
            raise ProviderError(
                self.name, symbol, "market_chart response missing 'total_volumes' key."
            )
        # total_volumes: [[timestamp_ms, volume], ...]
        return {int(row[0]): float(row[1]) for row in data["total_volumes"]}

    # ------------------------------------------------------------------
    # DataProvider protocol implementation
    # ------------------------------------------------------------------

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date | datetime,
        end: date | datetime,
    ) -> pd.DataFrame:
        """Fetch daily OHLCV data from CoinGecko.

        Args:
            symbol:    Crypto ticker (e.g. "BTC", "ETH").
            timeframe: Must be Timeframe.D1 — free tier only supports daily.
            start:     Inclusive start date/datetime.
            end:       Inclusive end date/datetime.

        Returns:
            DataFrame with UTC DatetimeIndex and columns [open, high, low, close, volume].

        Raises:
            ProviderError:   Unknown symbol or timeframe != D1.
            RateLimitError:  HTTP 429 from CoinGecko.
        """
        if timeframe != Timeframe.D1:
            raise ProviderError(
                self.name,
                symbol,
                f"CoinGecko free tier only supports daily candles (Timeframe.D1). "
                f"Requested: {timeframe!r}",
            )

        coin_id = self._coin_id(symbol)  # raises ProviderError if unknown
        days = _days_param(start, end)

        # Fetch OHLC and volumes concurrently
        ohlc_task = asyncio.create_task(self._fetch_ohlc(coin_id, days, symbol))
        vol_task = asyncio.create_task(self._fetch_volumes(coin_id, days, symbol))
        ohlc_rows, vol_map = await asyncio.gather(ohlc_task, vol_task)

        if not ohlc_rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # Build DataFrame from OHLC rows
        # Each row: [timestamp_ms, open, high, low, close]
        timestamps = [int(row[0]) for row in ohlc_rows]
        df = pd.DataFrame(
            {
                "open": [float(r[1]) for r in ohlc_rows],
                "high": [float(r[2]) for r in ohlc_rows],
                "low": [float(r[3]) for r in ohlc_rows],
                "close": [float(r[4]) for r in ohlc_rows],
            },
            index=pd.to_datetime(timestamps, unit="ms", utc=True),
        )
        df.index.name = "datetime"

        # Merge volumes — match on nearest daily bucket
        def _lookup_volume(ts: pd.Timestamp) -> float:
            # vol_map keys are ms timestamps; find closest within ±12h
            ts_ms = int(ts.timestamp() * 1000)
            tolerance_ms = 12 * 3600 * 1000
            best_vol = 0.0
            best_dist = tolerance_ms + 1
            for vts, vol in vol_map.items():
                dist = abs(vts - ts_ms)
                if dist < best_dist:
                    best_dist = dist
                    best_vol = vol
            return best_vol if best_dist <= tolerance_ms else 0.0

        df["volume"] = [_lookup_volume(ts) for ts in df.index]

        # Filter to requested date range
        dt_start = pd.Timestamp(_to_datetime(start))
        dt_end = pd.Timestamp(_to_datetime(end)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        df = df[(df.index >= dt_start) & (df.index <= dt_end)]  # type: ignore[assignment]

        return validate_ohlcv(df, symbol, self.name)

    async def is_tradeable(self, symbol: str) -> bool:
        """Return True if the symbol has a current market price on CoinGecko.

        Args:
            symbol: Crypto ticker (e.g. "BTC").

        Returns:
            True if the coin has active market data, False otherwise.

        Raises:
            ProviderError:  Unknown symbol or unexpected API response.
            RateLimitError: HTTP 429 from CoinGecko.
        """
        coin_id = self._coin_id(symbol)
        try:
            data = await self._get(
                f"/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "community_data": "false",
                    "developer_data": "false",
                },
                symbol=symbol,
            )
        except ProviderError:
            return False

        if not isinstance(data, dict):
            return False

        market_data = data.get("market_data", {})
        if not isinstance(market_data, dict):
            return False

        current_price = market_data.get("current_price", {})
        if not isinstance(current_price, dict):
            return False

        usd_price = current_price.get("usd")
        return usd_price is not None and float(usd_price) > 0
