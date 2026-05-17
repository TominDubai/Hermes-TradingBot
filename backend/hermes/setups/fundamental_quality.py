from __future__ import annotations

from typing import Any

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import sma
from hermes.setups.base import Setup, SetupResult


class FundamentalQuality(Setup):
    """
    Greenblatt-inspired fundamental quality screen with technical overlay.
    Fires on weekly scans. Injects yfinance info for testability.
    """

    name = "fundamental_quality"
    portfolio = Portfolio.LONG
    min_bars = 50

    def detect(  # type: ignore[override]
        self,
        df: pd.DataFrame,
        symbol: str | None = None,
        _info_override: dict[str, Any] | None = None,
    ) -> SetupResult | None:
        if not self.validate(df):
            return None

        # Fetch fundamentals
        info: dict[str, Any] = {}
        if _info_override is not None:
            info = _info_override
        elif symbol:
            try:
                import yfinance as yf
                info = yf.Ticker(symbol).info or {}
            except Exception:
                return None

        if not info:
            return None

        # Score each criterion
        criteria_met = 0
        pe = info.get("trailingPE")
        roe = info.get("returnOnEquity")
        dte = info.get("debtToEquity")
        rev_growth = info.get("revenueGrowth")
        eps_growth = info.get("earningsGrowth")

        if pe is not None and 5 <= pe <= 25:
            criteria_met += 1
        if roe is not None and roe > 0.15:
            criteria_met += 1
        if dte is not None and dte < 100:
            criteria_met += 1
        if rev_growth is not None and rev_growth > 0.05:
            criteria_met += 1
        if eps_growth is not None and eps_growth > 0:
            criteria_met += 1

        if criteria_met < 3:
            return None

        # Technical overlay: price above SMA(50)
        close = df["close"]
        sma50 = sma(close, 50)
        c = float(close.iloc[-1])
        s50 = float(sma50.iloc[-1])
        if c <= s50:
            return None

        entry = c
        stop = s50 * 0.95  # SMA50 minus 5%
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + 3.0 * risk

        score = 0.80 if criteria_met >= 4 else 0.60

        return SetupResult(
            score=score,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="1wk",
            metadata={
                "pe_ratio": pe,
                "roe": roe,
                "debt_to_equity": dte,
                "revenue_growth": rev_growth,
                "eps_growth": eps_growth,
                "criteria_met": criteria_met,
                "close_vs_sma50_pct": round((c - s50) / s50, 4),
            },
        )
