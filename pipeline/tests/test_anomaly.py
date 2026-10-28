"""Anomaly and regime detection tests.

The bar here is different from the forecast module. A detector that flags nothing, or flags
everything, is useless, so the tests pin both directions: an injected shock must be found,
and a clean series must stay quiet.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from colcheck.transforms import anomaly as A


@pytest.fixture
def clean_seasonal() -> pd.Series:
    """5 years of a stable seasonal pattern with a mild trend and no shocks."""
    months = pd.date_range("2015-01-01", periods=72, freq="MS")
    trend = 100.0 + 0.2 * np.arange(72)
    seasonal = 2.0 * np.sin(2 * np.pi * np.arange(72) / 12)
    return pd.Series(trend + seasonal, index=months)


@pytest.fixture
def shocked_seasonal(clean_seasonal) -> pd.Series:
    """The same series with one large one-month spike in the middle."""
    shocked = clean_seasonal.copy()
    shocked.iloc[36] += 15.0
    return shocked


def test_robust_z_is_median_centred(clean_seasonal):
    z = A.robust_z(clean_seasonal)
    assert z.median() == pytest.approx(0.0, abs=1e-9)


def test_robust_z_does_not_blow_up_on_a_constant_series():
    """Zero spread must yield zeros, not infinities that flag every observation."""
    constant = pd.Series([5.0] * 24)
    z = A.robust_z(constant)
    assert np.isfinite(z).all()
    assert (z == 0).all()


def test_robust_z_resists_a_single_outlier(clean_seasonal, shocked_seasonal):
    """The whole reason for using MAD: a standard deviation would be dragged by the shock
    and hide the very point we are looking for."""
    z_clean = A.robust_z(clean_seasonal.diff().dropna())
    z_shock = A.robust_z(shocked_seasonal.diff().dropna())
    # The shock's own z stays large rather than being absorbed into the scale estimate.
    assert z_shock.abs().max() > 3.5
    assert z_shock.abs().max() > z_clean.abs().max()


def test_stl_rejects_gaps(clean_seasonal):
    gappy = clean_seasonal.copy()
    gappy.iloc[10] = np.nan
    with pytest.raises(ValueError, match="gap-free"):
        A.stl_decompose(gappy)


def test_stl_rejects_too_short_a_series():
    short = pd.Series(100.0, index=pd.date_range("2020-01-01", periods=10, freq="MS"))
    with pytest.raises(ValueError, match="at least"):
        A.stl_decompose(short)


def test_clean_series_produces_no_flags(clean_seasonal):
    """A detector that always fires is as bad as one that never does."""
    found = A.find_anomalies(clean_seasonal, series_id="t", label="T")
    assert found == []


def test_injected_shock_is_found(shocked_seasonal):
    found = A.find_anomalies(shocked_seasonal, series_id="t", label="T")
    periods = {a.period for a in found}
    assert "2018-01" in periods, f"expected the injected spike, got {sorted(periods)}"
    spike = next(a for a in found if a.period == "2018-01")
    assert spike.direction == "above"
    assert spike.z > 0


def test_anomaly_carries_the_year_on_year_rate(shocked_seasonal):
    found = A.find_anomalies(shocked_seasonal, series_id="t", label="T")
    assert any(a.yoy_at_period is not None for a in found)


def test_threshold_is_respected(clean_seasonal):
    """A lower threshold can only find more, never fewer."""
    loose = A.find_anomalies(clean_seasonal, series_id="t", label="T", threshold_z=1.0)
    tight = A.find_anomalies(clean_seasonal, series_id="t", label="T", threshold_z=8.0)
    assert len(loose) >= len(tight)


def test_median_direction_is_symmetric(clean_seasonal):
    dipped = clean_seasonal.copy()
    dipped.iloc[40] -= 15.0
    found = A.find_anomalies(dipped, series_id="t", label="T")
    assert any(a.direction == "below" for a in found)
    assert all(a.z < 0 for a in found if a.direction == "below")


# ------------------------------------------------------------------ regime detection


def test_binary_segmentation_finds_an_obvious_step():
    """A series that jumps from mean 1 to mean 5 at a known index."""
    values = np.concatenate([np.full(60, 1.0), np.full(60, 5.0)])
    series = pd.Series(values, index=pd.date_range("2010-01-01", periods=120, freq="MS"))
    breaks = A.binary_segmentation(series, max_regimes=2, min_segment=24)
    assert len(breaks) == 1
    assert breaks[0] == 60


def test_binary_segmentation_finds_nothing_in_a_flat_series(clean_seasonal):
    flat = pd.Series(100.0, index=clean_seasonal.index)
    assert A.binary_segmentation(flat, max_regimes=3, min_segment=24) == []


def test_binary_segmentation_respects_min_segment():
    """Segments shorter than the minimum must never be produced."""
    values = np.concatenate([np.full(50, 1.0), np.full(50, 9.0)])
    series = pd.Series(values, index=pd.date_range("2010-01-01", periods=100, freq="MS"))
    breaks = A.binary_segmentation(series, max_regimes=4, min_segment=40)
    bounds = [0, *breaks, len(series)]
    assert all(right - left >= 40 for left, right in zip(bounds[:-1], bounds[1:], strict=True))


def test_find_regimes_partitions_the_whole_series():
    """Regimes must tile the series with no gap and no overlap -- otherwise the UI shows
    periods that belong to no regime, or to two."""
    months = pd.date_range("2010-01-01", periods=180, freq="MS")
    yoy_shift = np.concatenate([np.full(90, 1.0), np.full(90, 5.0)])
    index = 100.0 * np.cumprod(1 + np.concatenate([yoy_shift[:12] / 100, yoy_shift[12:] / 100]))
    series = pd.Series(index, index=months)

    regimes = A.find_regimes(series, max_regimes=3, min_segment=24)
    assert regimes
    # The regime series starts a year in: the first year-on-year rate needs 12 months of
    # index history before it exists at all.
    assert regimes[0].start_period == "2011-01"
    assert regimes[-1].end_period == "2024-12"
    for earlier, later in zip(regimes[:-1], regimes[1:], strict=True):
        earlier_end = pd.Period(earlier.end_period, "M")
        later_start = pd.Period(later.start_period, "M")
        assert later_start == earlier_end + 1, "regimes must be contiguous"


def test_regime_months_sum_to_the_series_length(clean_seasonal):
    regimes = A.find_regimes(clean_seasonal, max_regimes=3, min_segment=24)
    assert sum(r.months for r in regimes) == len(clean_seasonal) - 12  # one year lost to YoY


# ------------------------------------------------------------------------- context


def test_context_is_empty_when_there_is_nothing_to_corroborate():
    """The honest outcome when no independent series is available."""
    assert A.corroborating_context([]) == {}


def test_context_reports_fuel_movement_around_the_shock(shocked_seasonal):
    found = A.find_anomalies(shocked_seasonal, series_id="t", label="T")
    assert found

    dates = pd.date_range("2017-06-01", periods=24, freq="MS")
    fuel = pd.DataFrame({"date": dates, "ron95": np.linspace(2.0, 2.6, 24)})
    context = A.corroborating_context(found, fuelprice=fuel)
    assert "ron95_price_change_pct_in_window" in context


def test_context_survives_a_fuel_frame_missing_its_columns():
    found = [A.Anomaly(period="2020-04", index=100.0, residual=-5.0, z=-10.0, direction="below")]
    assert A.corroborating_context(found, fuelprice=pd.DataFrame({"date": []})) == {}


def test_context_filters_to_the_fuel_level_series():
    """fuelprice packs a price level and a weekly-change series into the same columns.

    Mixing them produced a reported -100% RON95 move through April 2020 -- the window happened
    to start on a level row and end on a change row. Any result must come from 'level' only.
    """
    # The pivot month is 2020-05, so the 3-month window runs 2020-02-01 .. 2020-08-01.
    dates = pd.date_range("2020-02-01", "2020-08-01", freq="MS")
    level = pd.DataFrame({"series_type": "level", "date": dates, "ron95": 2.00})
    level.loc[level.index[-1], "ron95"] = 1.60  # a clean -20% across the window

    # The change series sits in the same columns with values that would give a wildly
    # different answer if it leaked into the window.
    change = pd.DataFrame({"series_type": "change_weekly", "date": dates, "ron95": 50.0})

    fuel = pd.concat([level, change], ignore_index=True).sample(frac=1, random_state=0)
    found = [A.Anomaly(period="2020-05", index=100.0, residual=-5.0, z=-12.0, direction="below")]

    context = A.corroborating_context(found, fuelprice=fuel)
    assert context["ron95_price_change_pct_in_window"] == pytest.approx(-20.0)


def test_context_targets_an_anomaly_it_can_actually_speak_to():
    """Choosing the single largest anomaly makes this dead code on real data.

    Headline CPI's biggest flag is 2008-07, which predates every corroborating series. The
    function must fall through to the largest anomaly that its context series actually cover.
    """
    anomalies = [
        A.Anomaly(period="2008-07", index=100.0, residual=9.0, z=30.5, direction="above"),
        A.Anomaly(period="2020-05", index=110.0, residual=-6.0, z=-25.9, direction="below"),
    ]
    fuel = pd.DataFrame(
        {
            "series_type": "level",
            "date": pd.date_range("2017-01-01", periods=48, freq="MS"),
            "ron95": np.linspace(2.2, 1.4, 48),
        }
    )
    context = A.corroborating_context(anomalies, fuelprice=fuel)
    assert context["anomaly_period"] == "2020-05"
    assert context["anomaly_z"] == pytest.approx(-25.9)
    assert "ron95_price_change_pct_in_window" in context


def test_context_is_empty_when_no_anomaly_is_covered():
    """2008 flags with fuel data starting 2017: nothing to say, and it must say nothing."""
    anomalies = [A.Anomaly(period="2008-07", index=100.0, residual=9.0, z=30.5, direction="above")]
    fuel = pd.DataFrame(
        {
            "series_type": "level",
            "date": pd.date_range("2017-01-01", periods=24, freq="MS"),
            "ron95": np.linspace(2.2, 1.4, 24),
        }
    )
    assert A.corroborating_context(anomalies, fuelprice=fuel) == {}


def test_build_report_assembles_everything(shocked_seasonal):
    report = A.build_report(shocked_seasonal, series_id="t", label="T")
    assert report.series_id == "t"
    assert report.anomalies
    assert report.regimes
    assert report.caveats
    assert "statistical flag, not an explanation" in " ".join(report.caveats)
