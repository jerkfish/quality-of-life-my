"""Household income transforms (HIES).

Unlike the wage survey, HIES is a *household* measure and is the conventional basis for
cost-of-living comparisons. Its catch is that it is a survey with irregular gaps -- years
like 2010 and 2011 simply do not exist -- so everything here preserves the gaps and exposes
the survey years as data rather than pretending to a continuous series.

Note also that ``hh_income_state`` carries the 16 states only; the national series lives in
``hh_income``. An inner join against ``cpi_state`` will therefore never produce a national
row, which is fine as long as you know it (see docs/DATA.md section 5).
"""

from __future__ import annotations

import pandas as pd

from .common import annual_means, real_growth_pct


def survey_years(income_state: pd.DataFrame) -> list[int]:
    """The years HIES actually covers, so the UI can render a stepped x-axis."""
    years = pd.to_datetime(income_state["date"]).dt.year
    return sorted(int(y) for y in years.unique())


def state_price_index(cpi_state: pd.DataFrame) -> pd.DataFrame:
    overall = cpi_state[cpi_state["division"] == "overall"]
    return (
        annual_means(overall, value_col="index", entity_cols=("state",))
        .rename(columns={"value": "cpi"})
        .drop(columns=["is_partial", "months_observed"])
    )


def national_price_index(cpi_headline: pd.DataFrame) -> pd.DataFrame:
    overall = cpi_headline[cpi_headline["division"] == "overall"]
    return (
        annual_means(overall, value_col="index")
        .rename(columns={"value": "cpi"})
        .drop(columns=["is_partial", "months_observed"])
    )


def household_income_by_state(income_state: pd.DataFrame, cpi_state: pd.DataFrame) -> pd.DataFrame:
    """State household income with the matching annual CPI attached."""
    inc = income_state.copy()
    inc["date"] = pd.to_datetime(inc["date"])
    inc["year"] = inc["date"].dt.year
    inc = inc.loc[:, ["state", "year", "income_mean", "income_median"]]
    return inc.merge(state_price_index(cpi_state), on=["state", "year"], how="inner")


def household_income_summary(
    income_state: pd.DataFrame, cpi_state: pd.DataFrame, *, start_year: int, end_year: int
) -> pd.DataFrame:
    """Per-state real household income growth between two survey years, ranked."""
    merged = household_income_by_state(income_state, cpi_state)
    rows = []
    for state, grp in merged.groupby("state"):
        g = grp.set_index("year")
        if start_year not in g.index or end_year not in g.index:
            continue
        rows.append(
            {
                "state": state,
                "start_year": start_year,
                "end_year": end_year,
                "income_median_start": float(g.loc[start_year, "income_median"]),
                "income_median_end": float(g.loc[end_year, "income_median"]),
                "nominal_growth_pct": (
                    g.loc[end_year, "income_median"] / g.loc[start_year, "income_median"] - 1
                )
                * 100,
                "cumulative_inflation_pct": (g.loc[end_year, "cpi"] / g.loc[start_year, "cpi"] - 1)
                * 100,
                "real_growth_pct": real_growth_pct(
                    float(g.loc[start_year, "income_median"]),
                    float(g.loc[end_year, "income_median"]),
                    float(g.loc[start_year, "cpi"]),
                    float(g.loc[end_year, "cpi"]),
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("real_growth_pct", ascending=False).reset_index(drop=True)


def national_household_income(income: pd.DataFrame, cpi_headline: pd.DataFrame) -> pd.DataFrame:
    """National household income (from `hh_income`) with national CPI attached."""
    inc = income.copy()
    inc["date"] = pd.to_datetime(inc["date"])
    inc["year"] = inc["date"].dt.year
    inc = inc.loc[:, ["year", "income_mean", "income_median"]]
    return inc.merge(national_price_index(cpi_headline), on="year", how="inner")
