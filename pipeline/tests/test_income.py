"""Household income (HIES) transform tests.

The defining property of this source is that it is a survey with irregular gaps, so the
tests focus on gaps staying gaps.
"""

from __future__ import annotations

import pandas as pd
import pytest

from colcheck.transforms import income


@pytest.fixture
def hies_state_frame() -> pd.DataFrame:
    """Johor 2012 -> 2024: income 2000 -> 4000, CPI 100 -> 125.

    Nominal +100%, inflation +25%, so real is (2.0 / 1.25 - 1) = +60%.
    """
    rows = []
    for year, income_val, cpi_val in ((2012, 2000.0, 100.0), (2024, 4000.0, 125.0)):
        rows.append(
            {
                "state": "Johor",
                "date": pd.Timestamp(year=year, month=1, day=1),
                "income_mean": income_val * 1.2,
                "income_median": income_val,
                "expenditure_mean": income_val * 0.7,
                "gini": 0.4,
                "poverty": 5.0,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def cpi_for_income() -> pd.DataFrame:
    rows = []
    for year, cpi_val in ((2012, 100.0), (2024, 125.0)):
        for month in (1, 6):
            rows.append(
                {
                    "state": "Johor",
                    "date": pd.Timestamp(year=year, month=month, day=1),
                    "division": "overall",
                    "index": cpi_val,
                }
            )
    return pd.DataFrame(rows)


def test_survey_years_are_reported_not_invented():
    frame = pd.DataFrame(
        {
            "state": ["Johor"] * 3,
            "date": pd.to_datetime(["2009-01-01", "2012-01-01", "2024-01-01"]),
            "income_median": [1.0, 2.0, 3.0],
        }
    )
    assert income.survey_years(frame) == [2009, 2012, 2024]


def test_household_summary_known_answer(hies_state_frame, cpi_for_income):
    out = income.household_income_summary(
        hies_state_frame, cpi_for_income, start_year=2012, end_year=2024
    )
    row = out.iloc[0]
    assert row["nominal_growth_pct"] == pytest.approx(100.0)
    assert row["cumulative_inflation_pct"] == pytest.approx(25.0)
    assert row["real_growth_pct"] == pytest.approx(60.0)


def test_household_summary_skips_states_missing_either_year(hies_state_frame, cpi_for_income):
    """A state present in only one survey year must be skipped, not filled forward."""
    extra = pd.DataFrame(
        [
            {
                "state": "Kedah",
                "date": pd.Timestamp(2024, 1, 1),
                "income_mean": 3000.0,
                "income_median": 2500.0,
                "expenditure_mean": 1800.0,
                "gini": 0.35,
                "poverty": 8.0,
            },
            {
                "state": "Kedah",
                "date": pd.Timestamp(2024, 1, 1),
                "income_mean": 3000.0,
                "income_median": 2500.0,
                "expenditure_mean": 1800.0,
                "gini": 0.35,
                "poverty": 8.0,
            },
        ]
    )
    frame = pd.concat([hies_state_frame, extra.head(1)], ignore_index=True)
    cpi = pd.concat(
        [
            cpi_for_income,
            pd.DataFrame(
                [
                    {
                        "state": "Kedah",
                        "date": pd.Timestamp(2024, 1, 1),
                        "division": "overall",
                        "index": 120.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    out = income.household_income_summary(frame, cpi, start_year=2012, end_year=2024)
    assert list(out["state"]) == ["Johor"]


def test_national_household_income_attaches_national_cpi():
    income_frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2012-01-01", "2024-01-01"]),
            "income_mean": [4400.0, 9000.0],
            "income_median": [3600.0, 7000.0],
        }
    )
    cpi = pd.DataFrame(
        {
            "date": pd.to_datetime(["2012-01-01", "2024-01-01"]),
            "division": ["overall", "overall"],
            "index": [100.0, 126.6],
        }
    )
    out = income.national_household_income(income_frame, cpi)
    assert len(out) == 2
    assert out.iloc[-1]["cpi"] == pytest.approx(126.6)
    assert out.iloc[-1]["income_median"] == pytest.approx(7000.0)


def test_income_by_state_keeps_only_declared_columns(hies_state_frame, cpi_for_income):
    out = income.household_income_by_state(hies_state_frame, cpi_for_income)
    assert set(out.columns) == {"state", "year", "income_mean", "income_median", "cpi"}
