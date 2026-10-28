"""Known-answer tests for the shared numeric helpers."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from colcheck.transforms.common import (
    MONTHS_PER_YEAR,
    annual_means,
    cumulative_pct,
    deflate,
    pivot_index,
    real_growth_pct,
    round_or_none,
)


def test_annual_means_averages_within_the_year():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-02-01", "2025-01-01"]),
            "index": [100.0, 110.0, 200.0],
        }
    )
    out = annual_means(df, value_col="index").set_index("year")
    assert out.loc[2024, "value"] == pytest.approx(105.0)
    assert out.loc[2025, "value"] == pytest.approx(200.0)


def test_annual_means_flags_partial_years():
    """A year with 2 of 12 months must be marked partial, not silently averaged."""
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([f"2026-0{m}-01" for m in (1, 2)]),
            "index": [100.0, 102.0],
        }
    )
    out = annual_means(df, value_col="index").iloc[0]
    assert out["months_observed"] == 2
    assert bool(out["is_partial"]) is True

    full = pd.DataFrame(
        {"date": pd.date_range("2025-01-01", periods=12, freq="MS"), "index": [1.0] * 12}
    )
    full_out = annual_means(full, value_col="index").iloc[0]
    assert full_out["months_observed"] == MONTHS_PER_YEAR
    assert bool(full_out["is_partial"]) is False


def test_annual_means_respects_entity_columns():
    df = pd.DataFrame(
        {
            "state": ["Johor"] * 2 + ["Kedah"] * 2,
            "date": pd.to_datetime(["2024-01-01", "2024-02-01"] * 2),
            "index": [100.0, 200.0, 10.0, 20.0],
        }
    )
    out = annual_means(df, value_col="index", entity_cols=("state",)).set_index("state")
    assert out.loc["Johor", "value"] == pytest.approx(150.0)
    assert out.loc["Kedah", "value"] == pytest.approx(15.0)


def test_deflate_known_answer():
    """Index doubles; a nominal 200 must deflate to 100 in base-year terms."""
    nominal = pd.Series([100.0, 200.0])
    index = pd.Series([100.0, 200.0])
    out = deflate(nominal, index, pd.Series([100.0, 100.0]))
    assert out.tolist() == pytest.approx([100.0, 100.0])


def test_real_growth_is_a_ratio_not_a_subtraction():
    """+100% nominal against +100% inflation is 0% real, not 0pp-by-subtraction drift.

    The subtraction shortcut gives the same answer here; the point of the test is to pin the
    ratio form, which diverges over long horizons and compounding.
    """
    assert real_growth_pct(100.0, 200.0, 100.0, 200.0) == pytest.approx(0.0)

    # +121% nominal against +100% inflation: ratio says +10.5%, subtraction would say +21%.
    result = real_growth_pct(100.0, 221.0, 100.0, 200.0)
    assert result == pytest.approx(10.5, abs=0.05)
    assert result != pytest.approx(21.0)


def test_real_growth_known_answer():
    # 1000 -> 1500 nominal (+50%), inflation 100 -> 125 (+25%) => real +20%
    assert real_growth_pct(1000.0, 1500.0, 100.0, 125.0) == pytest.approx(20.0)
    # 1000 -> 1200 nominal (+20%), inflation +50% => real -20%
    assert real_growth_pct(1000.0, 1200.0, 100.0, 150.0) == pytest.approx(-20.0)


def test_real_growth_handles_degenerate_inputs():
    assert math.isnan(real_growth_pct(0.0, 100.0, 100.0, 110.0))
    assert math.isnan(real_growth_pct(100.0, 100.0, 0.0, 110.0))
    assert math.isnan(real_growth_pct(float("nan"), 100.0, 100.0, 110.0))


def test_cumulative_pct_known_answer():
    assert cumulative_pct(100.0, 150.0) == pytest.approx(50.0)
    assert cumulative_pct(0.0, 150.0) != cumulative_pct(0.0, 150.0)  # nan


def test_round_or_none_survives_nan():
    """NaN and inf must become null, not the non-standard JSON tokens NaN/Infinity."""
    assert round_or_none(float("nan")) is None
    assert round_or_none(float("inf")) is None
    assert round_or_none(None) is None
    assert round_or_none(1.23456, 2) == 1.23


def test_pivot_index_shapes_long_to_wide():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-02-01"]),
            "division": ["01", "07", "01"],
            "index": [1.0, 2.0, 3.0],
        }
    )
    wide = pivot_index(df, column_col="division", value_col="index")
    assert list(wide.columns) == ["01", "07"]
    assert len(wide) == 2
    assert wide.loc["2024-02-01", "01"] == 3.0
