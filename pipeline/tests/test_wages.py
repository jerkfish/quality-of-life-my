"""Wage transform tests, including the trap that caught us in Phase 0."""

from __future__ import annotations

import pandas as pd
import pytest

from colcheck.transforms import wages


def test_real_wages_known_answer(cpi_state_frame, wages_state_frame):
    """Johor +20% real, Kedah -20% real, from hand-computable inputs."""
    out = wages.real_wages_by_state(wages_state_frame, cpi_state_frame)
    by_state = out[out["year"] == 2025].set_index("state")

    assert by_state.loc["Johor", "real_median"] == pytest.approx(1500.0 / 1.25)
    assert by_state.loc["Kedah", "real_median"] == pytest.approx(1200.0 / 1.50)


def test_real_wages_uses_only_the_requested_sex(cpi_state_frame, wages_state_frame):
    out = wages.real_wages_by_state(wages_state_frame, cpi_state_frame, sex="both")
    # 2 states x 2 years; the male/female rows must not leak in and inflate the count.
    assert len(out) == 4
    assert set(out["state"]) == {"Johor", "Kedah"}


def test_state_price_index_excludes_the_national_row_safely(cpi_state_frame):
    """cpi_state has no 'Malaysia' row. The transform must not invent one."""
    prices = wages.state_price_index(cpi_state_frame)
    assert "Malaysia" not in set(prices["state"])


def test_validation_passes_against_matching_published_series(
    cpi_state_frame, wages_state_frame, published_real_frame
):
    mine = wages.real_wages_by_state(wages_state_frame, cpi_state_frame)
    result = wages.validate_against_dosm(mine, published_real_frame, year=2025)
    assert result.passed
    assert result.n_compared == 2
    assert result.max_abs_diff_median == pytest.approx(0.0, abs=1e-9)


def test_validation_catches_the_wrong_population_series(
    cpi_state_frame, wages_state_frame, published_real_frame
):
    """The Phase 0 bug, pinned as a test.

    `wages_real_state` (all employees) is a near-identically-named file that covers a
    different population. Feeding it here must FAIL, because that is exactly the mistake
    that produced a 388 RM phantom discrepancy during discovery.
    """
    mine = wages.real_wages_by_state(wages_state_frame, cpi_state_frame)
    wrong = published_real_frame.copy()
    is_2025 = pd.to_datetime(wrong["date"]).dt.year == 2025
    wrong.loc[is_2025, "wages_median_real"] *= 0.75  # plausible-looking gap

    result = wages.validate_against_dosm(mine, wrong, year=2025)
    assert not result.passed
    assert result.max_abs_diff_median > result.tolerance_rm


def test_validation_reports_no_overlap_rather_than_passing(
    cpi_state_frame, wages_state_frame, published_real_frame
):
    mine = wages.real_wages_by_state(wages_state_frame, cpi_state_frame)
    result = wages.validate_against_dosm(mine, published_real_frame, year=1900)
    assert result.n_compared == 0
    assert not result.passed


def test_missing_base_year_raises(cpi_state_frame, wages_state_frame):
    with pytest.raises(ValueError, match="Base year"):
        wages.real_wages_by_state(wages_state_frame, cpi_state_frame, base_year=1999)


def test_real_wage_summary_separates_inflation_from_wage_growth(cpi_state_frame, wages_state_frame):
    """Kedah has higher inflation AND lower nominal growth: worst of both."""
    real = wages.real_wages_by_state(wages_state_frame, cpi_state_frame)
    summary = wages.real_wage_summary(
        real, cpi_state_frame, start_year=2010, end_year=2025
    ).set_index("state")

    assert summary.loc["Johor", "cumulative_inflation_pct"] == pytest.approx(25.0)
    assert summary.loc["Kedah", "cumulative_inflation_pct"] == pytest.approx(50.0)
    assert summary.loc["Johor", "nominal_growth_pct"] == pytest.approx(50.0)
    assert summary.loc["Johor", "real_growth_pct"] == pytest.approx(20.0)
    assert summary.loc["Kedah", "real_growth_pct"] == pytest.approx(-20.0)

    # Ranked best-first, so Johor leads.
    ordered = wages.real_wage_summary(real, cpi_state_frame, start_year=2010, end_year=2025)
    assert ordered.iloc[0]["state"] == "Johor"


def test_state_medians_below_zero_do_not_explode():
    """A wage series that is entirely nan must not raise; it must produce nothing."""
    cpi = pd.DataFrame(
        {
            "state": ["Johor"] * 2,
            "date": pd.to_datetime(["2010-01-01", "2025-01-01"]),
            "division": ["overall"] * 2,
            "index": [100.0, 125.0],
        }
    )
    w = pd.DataFrame(
        {
            "state": ["Johor"] * 2,
            "date": pd.to_datetime(["2010-01-01", "2025-01-01"]),
            "sex": ["both"] * 2,
            "wages_median": [float("nan")] * 2,
            "wages_mean": [float("nan")] * 2,
        }
    )
    out = wages.real_wages_by_state(w, cpi)
    assert pd.isna(out["real_median"]).all()
