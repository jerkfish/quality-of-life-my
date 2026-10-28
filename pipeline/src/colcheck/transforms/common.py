"""Shared transform helpers.

The one rule that governs this whole package: **never silently fill, smooth, or
interpolate.** If a year has 9 months of data, it is labelled as a partial year. If a survey
skipped 2011, the gap stays a gap. Charts are built to cope with that, rather than being fed
invented numbers.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

MONTHS_PER_YEAR = 12


class AnnualPoint(BaseModel):
    """An annual aggregate, with the honesty metadata attached."""

    year: int
    value: float
    months_observed: int
    is_partial: bool


def annual_means(
    df: pd.DataFrame,
    *,
    value_col: str,
    entity_cols: tuple[str, ...] = (),
    date_col: str = "date",
) -> pd.DataFrame:
    """Mean of a monthly series within each calendar year, per entity.

    Returns columns: entity_cols + year + value + months_observed + is_partial.

    ``is_partial`` is True when the year has fewer than 12 observations. 2026, for example,
    currently has 9 months of CPI -- its annual mean is real but not comparable with a full
    year, and any chart that plots it without saying so is lying.
    """
    frame = df.copy()
    frame[date_col] = pd.to_datetime(frame[date_col])
    frame["year"] = frame[date_col].dt.year

    group = [*entity_cols, "year"]
    out = (
        frame.groupby(group, dropna=False)[value_col]
        .agg(value="mean", months_observed="count")
        .reset_index()
    )
    out["is_partial"] = out["months_observed"] < MONTHS_PER_YEAR
    return out


def deflate(nominal: pd.Series, price_index: pd.Series, base_year_index: pd.Series) -> pd.Series:
    """Convert a nominal series to base-year terms.

    ``price_index`` is the price index in each observation's year; ``base_year_index`` is the
    same index restricted to the base year. Because DOSM bases the CPI at 2010=100 and the
    state wage series also starts in 2010, this is a plain ratio with no rebasing or
    splicing anywhere in the project.

    Returns RM in base-year terms.
    """
    deflator = price_index / base_year_index
    return nominal / deflator


def cumulative_pct(start: float, end: float) -> float:
    """Percent change from start to end. Returns nan if start is not usable."""
    if start in (0, None) or pd.isna(start) or pd.isna(end):
        return float("nan")
    return (end / start - 1.0) * 100.0


def real_growth_pct(
    nominal_start: float, nominal_end: float, infl_start: float, infl_end: float
) -> float:
    """Real percentage change, given nominal endpoints and price-index endpoints.

    Computed as (nominal growth factor) / (inflation factor) - 1, i.e. the ratio of the two
    growth factors. This is the correct way to combine them -- subtracting inflation from
    nominal growth is a common and wrong shortcut that drifts over long horizons.
    """
    if any(pd.isna(v) for v in (nominal_start, nominal_end, infl_start, infl_end)):
        return float("nan")
    if nominal_start == 0 or infl_start == 0:
        return float("nan")
    return ((nominal_end / nominal_start) / (infl_end / infl_start) - 1.0) * 100.0


def round_or_none(value: float, ndigits: int = 2) -> float | None:
    """Round, mapping nan/inf to None so the value survives a JSON round-trip honestly."""
    if value is None or pd.isna(value) or value in (float("inf"), float("-inf")):
        return None
    return round(float(value), ndigits)


def pivot_index(
    df: pd.DataFrame,
    *,
    column_col: str,
    value_col: str,
    period_col: str = "date",
) -> pd.DataFrame:
    """Long -> wide: period index, one column per `column_col` value.

    Turns tidy CPI frames into the compact date-indexed arrays the artifacts ship. Using
    aggfunc="mean" rather than raising on duplicates is deliberate nowhere -- callers are
    expected to have already passed the contract's duplicate-key check.
    """
    frame = df.copy()
    frame[period_col] = pd.to_datetime(frame[period_col])
    return frame.pivot_table(
        index=period_col, columns=column_col, values=value_col, aggfunc="mean"
    ).sort_index()
