"""CPI transforms.

DOSM publishes the CPI as an index with base 2010 = 100. We keep it as an index everywhere
and only convert to percentage changes at the presentation boundary, because compounding
percentage changes back into a level is where projects like this quietly go wrong.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from .common import annual_means

# The CPI index base, as stated in the field metadata for cpi_headline:
# "[Float] Index value, with base 2010 = 100". Asserted, not assumed -- see tests.
CPI_BASE_YEAR = 2010
CPI_BASE_VALUE = 100.0


class DivisionName(BaseModel):
    code: str
    en: str
    bm: str


def division_names(mcoicop: pd.DataFrame) -> dict[str, DivisionName]:
    """2-digit division code -> English/Bahasa names, from the MCOICOP lookup.

    Also returns the special 'overall' pseudo-division, which exists in the CPI data but is
    not a COICOP code. Without this, every chart has an unlabelled bar.
    """
    rows = mcoicop[mcoicop["digits"] == 2].drop_duplicates(subset=["division"])
    out = {
        str(r["division"]): DivisionName(
            code=str(r["division"]), en=str(r["desc_en"]), bm=str(r["desc_bm"])
        )
        for _, r in rows.iterrows()
    }
    out["overall"] = DivisionName(code="overall", en="All items (headline)", bm="Semua item")
    return out


def group_names(mcoicop: pd.DataFrame) -> dict[str, DivisionName]:
    """3-digit group code -> names. 47 groups, including the food at/away split."""
    rows = mcoicop[mcoicop["digits"] == 3].drop_duplicates(subset=["group"])
    return {
        str(r["group"]): DivisionName(
            code=str(r["group"]), en=str(r["desc_en"]), bm=str(r["desc_bm"])
        )
        for _, r in rows.iterrows()
    }


def annual_index(cpi: pd.DataFrame, *, entity_cols: tuple[str, ...] = ()) -> pd.DataFrame:
    """Calendar-year mean of the monthly index, with partial-year flags."""
    return annual_means(cpi, value_col="index", entity_cols=entity_cols)


def latest_month(cpi: pd.DataFrame) -> pd.Timestamp:
    return pd.to_datetime(cpi["date"]).max()


def series_from_index(index_series: pd.Series) -> pd.Series:
    """Year-on-year percent change from an index level.

    Note we compute this ourselves rather than trusting the published
    `cpi_headline_inflation` file. Tests assert the two agree, which is how we would notice
    a definitional change upstream.
    """
    s = index_series.sort_index()
    return (s / s.shift(12) - 1.0) * 100.0


def cumulative_from(index_series: pd.Series, base_year: int = CPI_BASE_YEAR) -> pd.Series:
    """Cumulative percent change relative to the base year's annual mean."""
    s = index_series.sort_index()
    base = s.get(base_year)
    if base is None or pd.isna(base) or base == 0:
        return pd.Series(dtype="float64")
    return (s / base - 1.0) * 100.0


def state_inflation_ranking(
    cpi_state: pd.DataFrame, *, start_year: int, end_year: int
) -> pd.DataFrame:
    """Cumulative inflation per state between two years, for the choropleth/ranked view."""
    idx = (
        cpi_state[cpi_state["division"] == "overall"]
        .pipe(lambda d: annual_index(d, entity_cols=("state",)))
        .pivot_table(index="year", columns="state", values="value")
    )
    if start_year not in idx.index or end_year not in idx.index:
        raise ValueError(f"Years {start_year}/{end_year} not both present in CPI index")
    return (
        pd.DataFrame(
            {
                "start_index": idx.loc[start_year],
                "end_index": idx.loc[end_year],
                "cumulative_pct": (idx.loc[end_year] / idx.loc[start_year] - 1.0) * 100.0,
            }
        )
        .sort_values("cumulative_pct", ascending=False)
        .reset_index()
    )
