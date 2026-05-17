"""
Rule Scorer — evaluates confluence factors for a SetupResult.

Six binary factors (each +1), loaded from scoring_config.yaml:
  1. trend_aligned           — price > EMA(50) for intra; EMA(200) for long/mid
  2. higher_timeframe_aligned — ADX > 25 for intra (proxy); skip for others
  3. rsi_in_zone             — RSI 45–70 (bullish zone)
  4. macd_confirming         — MACD histogram > 0
  5. volume_confirming       — volume > 1.2x 20-bar average
  6. atr_range_valid         — ATR between 0.5 % and 5 % of price

Returns 0 immediately if R:R < min_rr (loaded from scoring_config.yaml).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from hermes.indicators.core import adx as calc_adx
from hermes.indicators.core import atr as calc_atr
from hermes.indicators.core import ema as calc_ema
from hermes.indicators.core import macd as calc_macd
from hermes.indicators.core import rsi as calc_rsi
from hermes.setups.base import SetupResult

_CONFIG_PATH = Path(__file__).parent / "scoring_config.yaml"


def _load_config() -> dict:
    with _CONFIG_PATH.open() as fh:
        return yaml.safe_load(fh)


class RuleScorer:
    """
    Stateless confluence scorer.

    Usage::

        scorer = RuleScorer()
        score = scorer.score(setup_result, df)   # int 0–6
    """

    def __init__(self) -> None:
        cfg = _load_config()
        self._min_rr: dict[str, float] = cfg.get("min_rr", {
            "long": 3.0,
            "mid": 1.5,
            "intra": 1.2,
        })
        # Factor weights (all 1 in default config — kept for future flexibility)
        self._factor_weights: dict[str, int] = {
            name: int(details.get("weight", 1))
            for name, details in cfg.get("confluence_factors", {}).items()
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, setup_result: SetupResult, df: pd.DataFrame) -> int:
        """
        Evaluate confluence factors and return a score (int 0–6).

        Returns 0 if R:R is below the portfolio minimum.
        """
        # ── R:R gate ──────────────────────────────────────────────────
        portfolio_key = str(setup_result.direction)  # unused — use setup portfolio
        # We need the portfolio from metadata or infer from setup name;
        # SetupResult does not carry portfolio directly, so we check the
        # setup_name prefix convention.  The setups all set portfolio on the
        # class, not on the result, so we fall back to the intra default.
        # Caller can pass portfolio via a subclass if needed — for now we
        # derive it from the setup name heuristic:
        if any(k in setup_result.setup_name for k in ("orb", "opening_range", "vwap", "momentum")):
            portfolio_key = "intra"
        elif "mid" in setup_result.setup_name:
            portfolio_key = "mid"
        else:
            portfolio_key = "long"

        min_rr = self._min_rr.get(portfolio_key, 1.2)
        if setup_result.rr_ratio < min_rr:
            return 0

        # ── Compute indicators ────────────────────────────────────────
        close = df["close"]
        current_price = float(close.iloc[-1])
        current_volume = float(df["volume"].iloc[-1])

        ema50_series = calc_ema(close, 50)
        ema200_series = calc_ema(close, 200)
        rsi_series = calc_rsi(close, 14)
        _, _, hist_series = calc_macd(close)
        atr_series = calc_atr(df["high"], df["low"], close, 14)
        adx_series, _, _ = calc_adx(df["high"], df["low"], close, 14)

        ema50 = ema50_series.iloc[-1]
        ema200 = ema200_series.iloc[-1]
        rsi_val = rsi_series.iloc[-1]
        macd_hist = hist_series.iloc[-1]
        atr_val = atr_series.iloc[-1]
        adx_val = adx_series.iloc[-1]

        vol_avg20 = df["volume"].rolling(20, min_periods=1).mean().iloc[-1]

        # ── Evaluate factors ──────────────────────────────────────────
        is_intra = portfolio_key == "intra"

        factors: dict[str, bool] = {}

        # 1. Trend aligned
        if is_intra:
            factors["trend_aligned"] = (
                not pd.isna(ema50) and current_price > float(ema50)
            )
        else:
            factors["trend_aligned"] = (
                not pd.isna(ema200) and current_price > float(ema200)
            )

        # 2. Higher timeframe aligned
        #    For intra: use ADX > 25 as a proxy for trending conditions.
        factors["higher_timeframe_aligned"] = (
            not pd.isna(adx_val) and float(adx_val) > 25
        )

        # 3. RSI in zone (45–70 for long signals)
        factors["rsi_in_zone"] = (
            not pd.isna(rsi_val) and 45 <= float(rsi_val) <= 70
        )

        # 4. MACD confirming
        factors["macd_confirming"] = (
            not pd.isna(macd_hist) and float(macd_hist) > 0
        )

        # 5. Volume confirming
        factors["volume_confirming"] = (
            not pd.isna(vol_avg20) and vol_avg20 > 0
            and current_volume > 1.2 * float(vol_avg20)
        )

        # 6. ATR range valid (0.5 %–5 % of price)
        if current_price > 0 and not pd.isna(atr_val):
            atr_pct = float(atr_val) / current_price
            factors["atr_range_valid"] = 0.005 <= atr_pct <= 0.05
        else:
            factors["atr_range_valid"] = False

        # ── Sum with weights ──────────────────────────────────────────
        confluence = 0
        for factor_name, triggered in factors.items():
            if triggered:
                confluence += self._factor_weights.get(factor_name, 1)

        return min(confluence, 6)
