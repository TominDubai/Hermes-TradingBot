"""
Tests for RuleScorer and FeatureExtractor.

All tests are self-contained — no network access, no live data.
DataFrames are synthesised.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from hermes.events.types import Direction
from hermes.scoring.feature_extractor import FeatureExtractor
from hermes.scoring.rule_scorer import RuleScorer
from hermes.setups.base import SetupResult

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_trending_df(
    n: int = 300,
    start_price: float = 100.0,
    slope: float = 0.05,
    volume: float = 200_000.0,
) -> pd.DataFrame:
    """
    Return a clean OHLCV DataFrame with a gentle uptrend.
    n=300 ensures all indicator warm-up periods are satisfied.
    """
    idx = pd.date_range(
        start="2024-01-02 14:30:00",
        periods=n,
        freq="5min",
        tz="UTC",
    )
    close = start_price + np.arange(n) * slope
    open_ = close - 0.1
    high = close + 0.3
    low = close - 0.3
    vol = np.full(n, volume)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def _make_result(
    *,
    entry: float = 105.0,
    stop: float = 100.0,
    target: float = 115.0,
    setup_name: str = "opening_range_breakout",
    score: float = 0.75,
    direction: Direction = Direction.LONG,
    timeframe: str = "5m",
) -> SetupResult:
    return SetupResult(
        score=score,
        entry=entry,
        stop=stop,
        target=target,
        direction=direction,
        setup_name=setup_name,
        timeframe=timeframe,
        metadata={"portfolio": "intra"},
    )


# ── RuleScorer ────────────────────────────────────────────────────────────────


class TestRuleScorer:

    def test_scorer_returns_int(self) -> None:
        """score() always returns an int."""
        df = _make_trending_df()
        result = _make_result()
        scorer = RuleScorer()
        score = scorer.score(result, df)
        assert isinstance(score, int)

    def test_scorer_returns_between_0_and_6(self) -> None:
        """score() is bounded 0–6."""
        df = _make_trending_df()
        result = _make_result()
        scorer = RuleScorer()
        score = scorer.score(result, df)
        assert 0 <= score <= 6

    def test_scorer_returns_0_below_min_rr(self) -> None:
        """
        score() returns 0 when R:R is below the portfolio minimum.

        intra min_rr = 1.2.  We create a result with R:R = 0.5 (risk > reward).
        """
        df = _make_trending_df()
        # entry=105, stop=100, target=107.5 → reward=2.5, risk=5.0 → rr=0.5 < 1.2
        result = _make_result(entry=105.0, stop=100.0, target=107.5)
        scorer = RuleScorer()
        score = scorer.score(result, df)
        assert score == 0, f"Expected 0 for R:R=0.5, got {score}"

    def test_scorer_counts_confluence_factors_correctly(self) -> None:
        """
        With a strongly trending df, most/all factors should fire.
        We verify that the count is at most 6 and that adding good conditions
        never decreases the score.
        """
        # Strong trend: slope so price is well above EMA50/200 by the end
        df_strong = _make_trending_df(n=300, slope=0.3, volume=500_000.0)
        # Good R:R result (rr = 2.0 > 1.2 min for intra)
        result = _make_result(entry=105.0, stop=100.0, target=115.0)
        scorer = RuleScorer()
        score_strong = scorer.score(result, df_strong)
        assert 0 <= score_strong <= 6

        # Flat (no trend) df — fewer factors should fire
        df_flat = _make_trending_df(n=300, slope=0.0)
        score_flat = scorer.score(result, df_flat)
        assert 0 <= score_flat <= 6
        # A strongly trending df should score at least as well as flat
        assert score_strong >= score_flat

    def test_scorer_loads_config(self) -> None:
        """RuleScorer loads scoring_config.yaml without error."""
        scorer = RuleScorer()
        # min_rr for intra should be 1.2 per config
        assert scorer._min_rr.get("intra") == pytest.approx(1.2)

    def test_scorer_minimum_rr_per_portfolio(self) -> None:
        """
        Score returns 0 for each portfolio when R:R is below the minimum.
        """
        scorer = RuleScorer()
        df = _make_trending_df()

        # intra min_rr = 1.2 → use rr = 1.0 (entry=105, stop=100, target=110)
        r_intra = _make_result(
            entry=105.0, stop=100.0, target=110.0, setup_name="opening_range_breakout"
        )  # rr = 5/5 = 1.0 < 1.2
        assert scorer.score(r_intra, df) == 0

    def test_scorer_volume_factor(self) -> None:
        """
        volume_confirming fires when last bar volume > 1.2x 20-bar average.
        We construct df where last bar volume is 3x the rest.
        """
        df = _make_trending_df(n=300, volume=100_000.0)
        vol_array = df["volume"].values.copy()
        vol_array[-1] = 500_000.0  # 5x average — volume_confirming should fire
        df["volume"] = vol_array

        result = _make_result(entry=105.0, stop=100.0, target=115.0)
        scorer = RuleScorer()
        score_high_vol = scorer.score(result, df)

        # Now make last bar volume tiny (< 1.2x)
        df2 = _make_trending_df(n=300, volume=100_000.0)
        vol2 = df2["volume"].values.copy()
        vol2[-1] = 50_000.0  # half the average — volume_confirming off
        df2["volume"] = vol2

        score_low_vol = scorer.score(result, df2)
        # High volume should score >= low volume
        assert score_high_vol >= score_low_vol


# ── FeatureExtractor ──────────────────────────────────────────────────────────


class TestFeatureExtractor:

    def _extract(self, n: int = 300) -> dict[str, Any]:
        df = _make_trending_df(n=n)
        result = _make_result()
        extractor = FeatureExtractor()
        return extractor.extract("AAPL", df, result)

    def test_feature_extractor_returns_dict(self) -> None:
        features = self._extract()
        assert isinstance(features, dict)

    def test_feature_extractor_returns_all_keys(self) -> None:
        """All expected feature keys are present in the returned dict."""
        features = self._extract()
        for key in FeatureExtractor.EXPECTED_KEYS:
            assert key in features, f"Missing key: {key!r}"

    def test_feature_extractor_no_numpy_types(self) -> None:
        """
        No numpy scalar types should survive in the output.
        All values must be Python-native float / int / str / bool / None.
        """
        features = self._extract()
        allowed = (float, int, str, bool, type(None))
        for key, val in features.items():
            assert isinstance(val, allowed), (
                f"Key {key!r} has non-native type {type(val).__name__!r}: {val!r}"
            )

    def test_feature_extractor_nan_replaced_with_none(self) -> None:
        """
        Any NaN values must be replaced by None, not left as float('nan')
        or numpy nan, so the dict is JSON-safe.
        """
        features = self._extract()
        for key, val in features.items():
            if isinstance(val, float):
                assert not math.isnan(val), (
                    f"Key {key!r} is float NaN — should be None"
                )

    def test_feature_extractor_setup_fields(self) -> None:
        """setup_name, raw_score, rr_ratio, direction match the SetupResult."""
        df = _make_trending_df()
        result = _make_result(
            entry=105.0, stop=100.0, target=115.0,
            setup_name="opening_range_breakout",
            score=0.75,
            direction=Direction.LONG,
            timeframe="5m",
        )
        features = FeatureExtractor().extract("SPY", df, result)

        assert features["setup_name"] == "opening_range_breakout"
        assert features["raw_score"] == pytest.approx(0.75)
        expected_rr = result.rr_ratio
        assert features["rr_ratio"] == pytest.approx(expected_rr)
        assert features["direction"] == "long"
        assert features["timeframe"] == "5m"

    def test_feature_extractor_price_fields_non_zero(self) -> None:
        """Price features should be positive numbers (not None) with a valid df."""
        features = self._extract()
        for key in ("close", "open", "high", "low", "volume"):
            val = features[key]
            assert val is not None, f"{key!r} is None"
            assert isinstance(val, (int, float))
            assert val > 0, f"{key!r} = {val} is not positive"

    def test_feature_extractor_short_df_returns_nones(self) -> None:
        """
        With only 25 bars many indicators will be NaN.
        FeatureExtractor must still return a dict with None (not NaN) for those.
        """
        df = _make_trending_df(n=25)
        result = _make_result()
        features = FeatureExtractor().extract("TSLA", df, result)
        for key, val in features.items():
            if isinstance(val, float):
                assert not math.isnan(val), f"Key {key!r} is NaN, should be None"

    def test_feature_extractor_obv_slope_present(self) -> None:
        """obv_slope_5 is present and is a float or None."""
        features = self._extract()
        assert "obv_slope_5" in features
        val = features["obv_slope_5"]
        assert val is None or isinstance(val, float)

    def test_feature_extractor_no_inf(self) -> None:
        """No infinite values in the feature dict."""
        features = self._extract()
        for key, val in features.items():
            if isinstance(val, float):
                assert not math.isinf(val), (
                    f"Key {key!r} is infinite — should be None"
                )
