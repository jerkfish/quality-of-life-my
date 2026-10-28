"""Wage transforms: nominal -> real, and the validation that keeps us honest.

The employer wage survey (``wages_state``) covers **Malaysian citizens only**. There is a
second, near-identically-named DOSM file (``wages_real_state``) covering **all** employees,
citizens and non-citizens. They are not comparable: in Sabah in 2025 the medians differ by
about 24%. Comparing the wrong pair measures a citizenship-composition shift and reports it
as inflation.

So we always deflate the citizen series ourselves and check it against
``wages_real_state_citizen`` -- DOSM's own citizen real series. That check is a test.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from ..registry import MALAYSIA
from .common import annual_means, real_growth_pct

WAGE_REFERENCE_YEAR = 2010
SEX_ALL = "both"


class ValidationResult(BaseModel):
    """How far our independently computed real series is from DOSM's published one."""

    n_compared: int
    max_abs_diff_median: float
    max_abs_diff_mean: float
    worst_state: str | None = None
    passed: bool = False
    tolerance_rm: float = 2.0

    @property
    def summary(self) -> str:
        if not self.n_compared:
            return "no overlapping observations to compare"
        return (
            f"n={self.n_compared} max|d_median|={self.max_abs_diff_median:.2f} RM "
            f"max|d_mean|={self.max_abs_diff_mean:.2f} RM (worst: {self.worst_state})"
        )


def state_price_index(cpi_state: pd.DataFrame) -> pd.DataFrame:
    """Annual mean CPI (division='overall') per state, columns: state, year, cpi."""
    overall = cpi_state[cpi_state["division"] == "overall"]
    return (
        annual_means(overall, value_col="index", entity_cols=("state",))
        .rename(columns={"value": "cpi", "months_observed": "cpi_months"})
        .drop(columns=["is_partial"])
    )


def real_wages_by_state(
    wages_state: pd.DataFrame,
    cpi_state: pd.DataFrame,
    *,
    base_year: int = WAGE_REFERENCE_YEAR,
    sex: str = SEX_ALL,
) -> pd.DataFrame:
    """Deflate the citizen median/mean wage by the state CPI.

    Returns long form: state, year, wages_median, wages_mean, cpi, real_median, real_mean.
    Real values are RM in `base_year` terms.
    """
    wages = wages_state[wages_state["sex"] == sex].copy()
    wages = wages.loc[:, ["state", "date", "wages_median", "wages_mean"]]
    wages["date"] = pd.to_datetime(wages["date"])
    wages["year"] = wages["date"].dt.year

    prices = state_price_index(cpi_state)
    merged = wages.merge(prices, on=["state", "year"], how="inner", validate="one_to_one")

    base = merged[merged["year"] == base_year].set_index("state")["cpi"]
    if base.empty:
        raise ValueError(f"Base year {base_year} absent from the CPI; cannot deflate.")
    if (base <= 0).any():
        raise ValueError(f"Non-positive CPI in base year {base_year}: {base[base <= 0].to_dict()}")

    deflator = merged["cpi"] / merged["state"].map(base)
    merged["real_median"] = merged["wages_median"] / deflator
    merged["real_mean"] = merged["wages_mean"] / deflator
    return merged.sort_values(["state", "year"]).reset_index(drop=True)


def national_real_wages(
    wages_state: pd.DataFrame,
    cpi_headline: pd.DataFrame,
    *,
    base_year: int = WAGE_REFERENCE_YEAR,
    sex: str = SEX_ALL,
) -> pd.DataFrame:
    """The nationwide real wage series.

    This needs its own function because `real_wages_by_state` inner-joins against `cpi_state`,
    which carries the 16 states and **no** nationwide row — correctly, since there is no
    "national CPI by state" observation to join to. Deflating the national wage therefore has
    to use `cpi_headline` instead.

    Getting this wrong is silent: the state frame simply contains no Malaysia entry, and any
    headline national figure reads as missing rather than raising. It was caught by the
    frontend rendering "no data" where a number belonged.
    """
    wages = wages_state[(wages_state["sex"] == sex) & (wages_state["state"] == MALAYSIA)]
    if wages.empty:
        raise ValueError(
            f"wages_state has no '{MALAYSIA}' row for sex={sex!r}; cannot build a national series."
        )
    wages = wages.loc[:, ["date", "wages_median", "wages_mean"]].copy()
    wages["date"] = pd.to_datetime(wages["date"])
    wages["year"] = wages["date"].dt.year

    prices = (
        cpi_headline[cpi_headline["division"] == "overall"]
        .pipe(lambda d: annual_means(d, value_col="index"))
        .rename(columns={"value": "cpi"})
        .drop(columns=["is_partial", "months_observed"])
    )
    merged = wages.merge(prices, on="year", how="inner")
    base = merged.loc[merged["year"] == base_year, "cpi"]
    if base.empty:
        raise ValueError(f"Base year {base_year} absent from the national CPI.")
    deflator = merged["cpi"] / float(base.iloc[0])
    merged["real_median"] = merged["wages_median"] / deflator
    merged["real_mean"] = merged["wages_mean"] / deflator
    return merged.sort_values("year").reset_index(drop=True)


def validate_against_dosm(
    mine: pd.DataFrame, published: pd.DataFrame, *, year: int, tolerance_rm: float = 2.0
) -> ValidationResult:
    """Compare our deflated real wages against DOSM's published real series.

    `published` must be the CITIZEN real series (wages_real_state_citizen). Passing
    `wages_real_state` (all employees) here is the exact bug this function exists to catch,
    and it will fail loudly.
    """
    pub = published.copy()
    pub["date"] = pd.to_datetime(pub["date"])
    pub["year"] = pub["date"].dt.year
    pub = pub[pub["year"] == year]

    a = mine[mine["year"] == year].set_index("state")
    b = pub.set_index("state")
    common = a.index.intersection(b.index)
    if common.empty:
        return ValidationResult(
            n_compared=0, max_abs_diff_median=float("nan"), max_abs_diff_mean=float("nan")
        )

    d_med = (a.loc[common, "real_median"] - b.loc[common, "wages_median_real"]).abs()
    d_mean = (a.loc[common, "real_mean"] - b.loc[common, "wages_mean_real"]).abs()
    worst = d_med.idxmax() if len(d_med) else None
    return ValidationResult(
        n_compared=len(common),
        max_abs_diff_median=float(d_med.max()),
        max_abs_diff_mean=float(d_mean.max()),
        worst_state=str(worst) if worst is not None else None,
        passed=bool(d_med.max() <= tolerance_rm and d_mean.max() <= tolerance_rm),
        tolerance_rm=tolerance_rm,
    )


def real_wage_summary(
    real: pd.DataFrame, prices: pd.DataFrame, *, start_year: int, end_year: int
) -> pd.DataFrame:
    """Per-state real wage growth between two years, ranked.

    `prices` must be the raw cpi_state frame (used for the inflation leg of the ratio).
    """
    px = (
        prices[prices["division"] == "overall"]
        .pipe(lambda d: annual_means(d, value_col="index", entity_cols=("state",)))
        .pivot_table(index="state", columns="year", values="value")
    )
    rows = []
    for state, grp in real.groupby("state"):
        g = grp.set_index("year")
        if start_year not in g.index or end_year not in g.index:
            continue
        rows.append(
            {
                "state": state,
                "start_year": start_year,
                "end_year": end_year,
                "wages_median_start": float(g.loc[start_year, "wages_median"]),
                "wages_median_end": float(g.loc[end_year, "wages_median"]),
                "real_median_start": float(g.loc[start_year, "real_median"]),
                "real_median_end": float(g.loc[end_year, "real_median"]),
                "nominal_growth_pct": (
                    g.loc[end_year, "wages_median"] / g.loc[start_year, "wages_median"] - 1
                )
                * 100,
                "cumulative_inflation_pct": (
                    px.loc[state, end_year] / px.loc[state, start_year] - 1
                )
                * 100,
                "real_growth_pct": real_growth_pct(
                    float(g.loc[start_year, "wages_median"]),
                    float(g.loc[end_year, "wages_median"]),
                    float(px.loc[state, start_year]),
                    float(px.loc[state, end_year]),
                ),
            }
        )
    # Shaped even when empty, for the same reason as the HIES summary: a column-less frame
    # turns a "no overlapping years" situation into a confusing KeyError one line later.
    if not rows:
        return pd.DataFrame(
            columns=[
                "state",
                "start_year",
                "end_year",
                "wages_median_start",
                "wages_median_end",
                "real_median_start",
                "real_median_end",
                "nominal_growth_pct",
                "cumulative_inflation_pct",
                "real_growth_pct",
            ]  # fmt: skip
        )
    return pd.DataFrame(rows).sort_values("real_growth_pct", ascending=False).reset_index(drop=True)
