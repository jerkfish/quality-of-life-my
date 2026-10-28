"""CPI transform tests."""

from __future__ import annotations

import pandas as pd
import pytest

from colcheck.transforms import cpi


def test_division_names_maps_codes_and_adds_overall(mcoicop_frame):
    names = cpi.division_names(mcoicop_frame)
    assert names["01"].en == "Food & Beverages"
    assert names["01"].bm == "Makanan & Minuman"
    # 'overall' is a pseudo-division present in the data but not a COICOP code. Without
    # this it renders as an unlabelled series everywhere.
    assert "overall" in names
    assert names["overall"].en == "All items (headline)"
    # Only 2-digit rows should be picked up.
    assert "001" not in names


def test_group_names_maps_three_digit_groups(mcoicop_frame):
    names = cpi.group_names(mcoicop_frame)
    assert names["001"].en == "Food away from home"
    assert names["011"].en == "Food at home"


def test_series_from_index_known_answer(cpi_national_frame):
    """Division 'overall' rises 0.4/month, so yoy is exactly 4.8pp after 12 months."""
    wide = cpi_national_frame.pipe(
        lambda d: d[d["division"] == "overall"].set_index("date")["index"]
    )
    yoy = cpi.series_from_index(wide)
    # 100.0 -> 104.8 over 12 steps == +4.8%
    assert yoy.iloc[12] == pytest.approx(4.8, abs=1e-9)
    assert yoy.iloc[:12].isna().all(), "the first 12 months have no prior year to compare"


def test_cumulative_from_known_answer(cpi_national_frame):
    """Annual means are 102.2 (2024) and 107.0 (2025), so cumulative is +4.70%."""
    annual = cpi.annual_index(
        cpi_national_frame[cpi_national_frame["division"] == "overall"]
    ).set_index("year")["value"]
    assert annual.loc[2024] == pytest.approx(102.2)
    assert annual.loc[2025] == pytest.approx(107.0)

    cum = cpi.cumulative_from(annual, base_year=2024)
    assert cum.loc[2024] == pytest.approx(0.0)
    assert cum.loc[2025] == pytest.approx((107.0 / 102.2 - 1) * 100)


def test_cumulative_from_returns_empty_when_base_year_absent(cpi_state_frame):
    idx = cpi.annual_index(cpi_state_frame[cpi_state_frame["state"] == "Johor"]).set_index("year")[
        "value"
    ]
    assert cpi.cumulative_from(idx, base_year=1999).empty


def test_state_inflation_ranking_known_answer(cpi_state_frame):
    out = cpi.state_inflation_ranking(cpi_state_frame, start_year=2010, end_year=2025)
    by_state = out.set_index("state")
    assert by_state.loc["Johor", "cumulative_pct"] == pytest.approx(25.0)
    assert by_state.loc["Kedah", "cumulative_pct"] == pytest.approx(50.0)
    # Ranked highest inflation first.
    assert out.iloc[0]["state"] == "Kedah"


def test_state_inflation_ranking_rejects_missing_years(cpi_state_frame):
    with pytest.raises(ValueError, match="not both present"):
        cpi.state_inflation_ranking(cpi_state_frame, start_year=2001, end_year=2025)


def test_latest_month(cpi_state_frame):
    assert cpi.latest_month(cpi_state_frame) == pd.Timestamp("2025-12-01")
