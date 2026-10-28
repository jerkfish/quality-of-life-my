"""Forecast tests.

Two things matter here and they are different things:

*   **the arithmetic is right** -- intervals, implied rates, and the backtest's no-look-ahead
    property; and
*   **the honesty machinery works** -- when a model fails to beat the baseline, the result
    says so rather than quietly ranking it first.

The second is the one a reviewer should look at, because it is the part that is easy to fake.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from colcheck.transforms import forecast as F


@pytest.fixture
def constant_growth() -> pd.Series:
    """60 months compounding at exactly 0.5%/month.

    Year-on-year inflation is therefore a constant `1.005**12 - 1` for the whole series, which
    makes the persistence baseline exactly right and gives every assertion a closed form.
    """
    months = pd.date_range("2018-01-01", periods=60, freq="MS")
    return pd.Series(100.0 * 1.005 ** np.arange(60), index=months)


@pytest.fixture
def flat_series() -> pd.Series:
    months = pd.date_range("2015-01-01", periods=120, freq="MS")
    return pd.Series(100.0, index=months)


def test_persistence_reproduces_a_constant_inflation_rate(constant_growth):
    """With a constant growth rate, persistence forecasting is exact.

    level(t) = level(t-12) * level(t-12)/level(t-24) is the true value when the rate is
    constant, so the point path must land on the realised index and the implied rate must be
    the constant.
    """
    point, lo, hi = F.inflation_persistence(constant_growth, 12)
    expected_rate = (1.005**12 - 1) * 100
    rate = F.implied_yoy(point, constant_growth, 12)

    assert np.allclose(rate, expected_rate, atol=1e-9)
    assert np.all(lo <= point)
    assert np.all(point <= hi)

    # And the point path equals the true continuation of the series.
    true_next = constant_growth.to_numpy()[-1] * 1.005 ** np.arange(1, 13)
    assert np.allclose(point, true_next, rtol=1e-9)


def test_persistence_implies_zero_inflation_for_a_flat_series(flat_series):
    """Flat index -> the last observed rate is 0%, so persistence forecasts 0%."""
    point, _, _ = F.inflation_persistence(flat_series, 6)
    assert np.allclose(F.implied_yoy(point, flat_series, 6), 0.0, atol=1e-9)


def test_persistence_needs_two_years():
    short = pd.Series(100.0, index=pd.date_range("2020-01-01", periods=20, freq="MS"))
    with pytest.raises(ValueError, match="two full years"):
        F.inflation_persistence(short, 12)


def test_implied_yoy_known_answer():
    """A forecast 10% above the year-earlier value implies 10% inflation."""
    history = pd.Series(100.0, index=pd.date_range("2015-01-01", periods=24, freq="MS"))
    forecast_index = np.array([110.0] * 12)
    rate = F.implied_yoy(forecast_index, history, 12)
    assert np.allclose(rate, 10.0)


def test_implied_yoy_uses_the_correct_base_month():
    """Horizon h must divide by the index 12 months before *that* target month, not by the
    last observed value. Getting this wrong mixes up seasonal months."""
    values = np.array([100.0 + i for i in range(24)])  # 100..123, roughly +1/month
    history = pd.Series(values, index=pd.date_range("2015-01-01", periods=24, freq="MS"))
    # Forecast the index for month 25 as 124 (a +1 continuation).
    rate = F.implied_yoy(np.array([124.0] + [np.nan] * 11), history, 12)
    # Base for month 25 is month 13, which is 112.0 -> 10.714%.
    assert rate[0] == pytest.approx((124.0 / 112.0 - 1) * 100)


def test_implied_yoy_refuses_horizons_beyond_a_year(constant_growth):
    """Past 12 months the base month is itself forecast, so the result would be a forecast of
    a forecast. Return NaN rather than a number with no honest interval."""
    rate = F.implied_yoy(np.full(18, 200.0), constant_growth, 18)
    assert np.all(np.isnan(rate[12:]))
    assert not np.isnan(rate[:12]).any()


def test_rolling_origin_never_trains_on_the_future(constant_growth_long):
    """The defining property of the backtest. If this breaks, every score is inflated."""
    folds = F.rolling_origin_backtest(
        constant_growth_long, F.inflation_persistence, horizon=6, n_folds=5
    )
    assert not folds.empty
    assert (folds["target"] > folds["origin"]).all()
    # Each target must be within the stated horizon of its own origin.
    for _, row in folds.iterrows():
        gap = (row["target"].to_period("M") - row["origin"].to_period("M")).n
        assert 1 <= gap <= 6


def test_backtest_requires_enough_history():
    short = pd.Series(100.0, index=pd.date_range("2020-01-01", periods=50, freq="MS"))
    with pytest.raises(ValueError, match="at least"):
        F.rolling_origin_backtest(short, F.inflation_persistence)


def test_score_fold_results_known_answer():
    folds = pd.DataFrame(
        {
            "actual_yoy": [2.0, 4.0, 3.0],
            "pred_yoy": [2.5, 3.0, 3.0],
        }
    )
    score = F.score_fold_results(folds, "test")
    assert score.mae_pp == pytest.approx((0.5 + 1.0 + 0.0) / 3)
    assert score.n_observations == 3


def test_score_ignores_unscorable_rows():
    folds = pd.DataFrame({"actual_yoy": [2.0, np.nan], "pred_yoy": [2.0, 5.0]})
    assert F.score_fold_results(folds, "test").n_observations == 1


def test_score_handles_no_usable_rows():
    folds = pd.DataFrame({"actual_yoy": [np.nan], "pred_yoy": [np.nan]})
    score = F.score_fold_results(folds, "test")
    assert score.n_observations == 0
    assert np.isnan(score.mae_pp)


def test_interval_coverage_known_answer():
    folds = pd.DataFrame(
        {
            "actual_yoy": [1.0, 5.0, 2.0],
            "lo80_yoy": [0.0, 0.0, 0.0],
            "hi80_yoy": [3.0, 3.0, 3.0],
        }
    )
    # Two of three inside (5.0 falls outside).
    assert F.interval_coverage(folds) == pytest.approx(2 / 3)


def test_guarded_forecaster_returns_nan_instead_of_raising():
    """One bad window must not kill a whole backtest, and must not quietly invent a number."""

    def exploding(train, horizon):
        raise RuntimeError("no convergence")

    guarded = F._guarded(exploding, "boom")
    point, lo, hi = guarded(pd.Series([1.0] * 24), 6)
    assert len(point) == 6
    assert np.isnan(point).all()
    assert np.isnan(lo).all() and np.isnan(hi).all()


def test_forecast_series_selects_the_best_model_by_mae(constant_growth_long):
    """Persistence is exactly right on this series, so it must win."""
    good = F._Callable("perfect", lambda train, h: F.inflation_persistence(train, h))
    bad = F._Callable(
        "deliberately_bad",
        lambda train, h: (
            np.full(h, train.iloc[-1] * 2),
            np.full(h, train.iloc[-1] * 2),
            np.full(h, train.iloc[-1] * 2),
        ),
    )
    result = F.forecast_series(
        constant_growth_long,
        series_id="test",
        label="Test",
        horizon=6,
        n_folds=4,
        models=[F._Callable(F.BASELINE_MODEL, F.inflation_persistence), good, bad],
    )
    assert result.method in {F.BASELINE_MODEL, "perfect"}
    chosen = next(s for s in result.scores if s.model == result.method)
    assert chosen.mae_pp <= min(s.mae_pp for s in result.scores) + 1e-9


def test_forecast_series_says_so_when_nothing_beats_the_baseline(constant_growth_long):
    """THE honesty test.

    When the only models available are the baseline and something worse, the result must
    report `beats_baseline=False` and say so in the verdict and caveats -- not silently
    present the least-bad option as a forecast worth trusting.
    """
    worse = F._Callable(
        "worse",
        lambda train, h: (
            np.full(h, train.iloc[-1] * 1.5),
            np.full(h, train.iloc[-1] * 1.5),
            np.full(h, train.iloc[-1] * 1.5),
        ),
    )
    result = F.forecast_series(
        constant_growth_long,
        series_id="test",
        label="Test",
        horizon=6,
        n_folds=4,
        models=[F._Callable(F.BASELINE_MODEL, F.inflation_persistence), worse],
    )
    assert result.beats_baseline is False
    assert "does NOT beat" in " ".join(result.caveats) or "No model beat" in result.verdict
    assert result.baseline_model == F.BASELINE_MODEL


def test_forecast_series_records_the_interval_in_rate_space(constant_growth_long):
    point, lo, hi = F.inflation_persistence(constant_growth_long, 12)
    assert np.all(lo <= point) and np.all(point <= hi)
    result = F.forecast_series(
        constant_growth_long, series_id="t", label="T", horizon=12, n_folds=3
    )
    for p in result.points:
        assert p.implied_yoy is not None
        assert p.implied_yoy_lo80 is not None
        assert p.implied_yoy_lo80 <= p.implied_yoy <= p.implied_yoy_hi80


def test_forecast_series_raises_when_no_model_is_usable():
    short = pd.Series(100.0, index=pd.date_range("2020-01-01", periods=30, freq="MS"))
    with pytest.raises(ValueError, match="no model produced"):
        F.forecast_series(short, series_id="t", label="T")


@pytest.fixture
def constant_growth_long() -> pd.Series:
    """20 years at a constant rate -- enough for a backtest with a 10-year minimum window."""
    months = pd.date_range("2005-01-01", periods=240, freq="MS")
    return pd.Series(100.0 * 1.003 ** np.arange(240), index=months)
