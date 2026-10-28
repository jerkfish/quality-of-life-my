"""Personal inflation calculator.

Given a spending split across CPI divisions, weight the division-level inflation rates by
that split and compare the result to the headline rate.

**Provenance caveat, and it matters.** DOSM does not publish CPI expenditure weights as open
data. We searched the full catalogue: the only "weight" hits are child-nutrition datasets.
The official weights live in the CPI Technical Notes PDF
(https://open.dosm.gov.my/publications/technical-notes/cpi-technotes), which the dataset
metadata cites but which is not a machine-readable dataset.

Consequence: this calculator takes *the user's own* spending split as its input. We do not
ship a default basket pretending to be the official one. Illustrative presets, if we add
them, are labelled as illustrative and never presented as DOSM weights. See KNOWN-GAPS.md.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field

from .cpi import cumulative_from, series_from_index


class Basket(BaseModel):
    """A spending split across CPI divisions.

    Weights need not sum to 1 -- they are normalised -- so a user can enter ringgit amounts
    directly, which is much easier than percentages.
    """

    name: str
    weights: dict[str, float]
    source: str = "user"
    illustrative: bool = False

    def normalised(self) -> dict[str, float]:
        total = sum(v for v in self.weights.values() if v > 0)
        if total <= 0:
            raise ValueError(f"Basket {self.name!r} has no positive weights.")
        return {k: v / total for k, v in self.weights.items() if v > 0}


class Contribution(BaseModel):
    division: str
    name_en: str
    name_bm: str
    weight: float
    rate_pct: float
    contribution_pp: float


class PersonalInflation(BaseModel):
    personal_pct: float
    headline_pct: float
    gap_pp: float
    period_label: str
    contributions: list[Contribution] = Field(default_factory=list)
    # Divisions named in the basket but absent from the data. Surfaced, never dropped quietly.
    missing_divisions: list[str] = Field(default_factory=list)
    # Share of the basket (by weight) we could actually price.
    priced_weight_share: float = 0.0
    caveats: list[str] = Field(default_factory=list)


def inflation_by_division(
    cpi: pd.DataFrame, *, base_year: int | None = None, yoy: bool = False
) -> dict[str, float]:
    """Division-level inflation as a dict {division_code: percent}.

    Either cumulative from `base_year`, or the latest year-on-year rate.
    """
    frame = cpi.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    wide = frame.pivot_table(
        index="date", columns="division", values="index", aggfunc="mean"
    ).sort_index()

    if yoy:
        latest = series_from_index(wide).iloc[-1]
        return {str(k): float(v) for k, v in latest.items() if pd.notna(v)}

    if base_year is None:
        raise ValueError("base_year is required unless yoy=True")
    annual = wide.groupby(wide.index.year).mean()
    if base_year not in annual.index:
        raise ValueError(f"Base year {base_year} not present in the CPI series.")
    latest_year = annual.index.max()
    base = annual.loc[base_year]
    end = annual.loc[latest_year]
    return {
        str(k): float((end[k] / base[k] - 1.0) * 100.0) for k in annual.columns if pd.notna(base[k])
    }


def compute_personal_inflation(
    basket: Basket,
    rates: dict[str, float],
    *,
    headline_rate: float,
    names: dict[str, tuple[str, str]] | None = None,
    period_label: str = "",
) -> PersonalInflation:
    """Weight division-level inflation by the user's basket.

    The personal rate is sum(w_i * r_i) over the basket, with weights normalised to 1.

    Divisions the user named but that we cannot price are reported in `missing_divisions`
    and excluded from the normalisation -- not treated as zero inflation, which would
    silently understate the result.
    """
    normalised = basket.normalised()
    names = names or {}

    priced = {k: w for k, w in normalised.items() if k in rates}
    missing = sorted(k for k in normalised if k not in rates)
    priced_share = sum(priced.values())

    caveats: list[str] = []
    if missing:
        caveats.append(
            f"{len(missing)} division(s) in your basket have no price data and were excluded: "
            f"{', '.join(missing)}."
        )
    if priced_share < 0.5:
        caveats.append(
            f"Only {priced_share * 100:.0f}% of your spending could be priced, so this figure "
            "is not reliable."
        )
    if abs(sum(normalised.values()) - 1.0) > 1e-9:
        caveats.append("Basket weights were normalised to sum to 100%.")

    if not priced or priced_share <= 0:
        return PersonalInflation(
            personal_pct=float("nan"),
            headline_pct=headline_rate,
            gap_pp=float("nan"),
            period_label=period_label,
            missing_divisions=missing,
            priced_weight_share=0.0,
            caveats=[*caveats, "No division in the basket could be priced."],
        )

    total_weight = sum(priced.values())
    contributions = []
    personal = 0.0
    for code, weight in sorted(priced.items(), key=lambda kv: -kv[1]):
        w = weight / total_weight
        r = rates[code]
        contribution = w * r
        personal += contribution
        en, bm = names.get(code, (code, code))
        contributions.append(
            Contribution(
                division=code,
                name_en=en,
                name_bm=bm,
                weight=w,
                rate_pct=float(r),
                contribution_pp=float(contribution),
            )
        )

    return PersonalInflation(
        personal_pct=float(personal),
        headline_pct=float(headline_rate),
        gap_pp=float(personal - headline_rate),
        period_label=period_label,
        contributions=contributions,
        missing_divisions=missing,
        priced_weight_share=float(priced_share / sum(normalised.values())),
        caveats=caveats,
    )


def explain_gap(result: PersonalInflation, *, top_n: int = 3) -> str:
    """A plain-language sentence for the UI. No jargon, no hand-waving."""
    if result.personal_pct != result.personal_pct:  # nan
        return "There is not enough price data to compute your personal inflation rate."

    direction = "above" if result.gap_pp >= 0 else "below"
    magnitude = abs(result.gap_pp)
    if magnitude < 0.05:
        return (
            f"Your spending pattern gives you essentially the same inflation rate as the "
            f"headline figure ({result.personal_pct:.1f}%)."
        )

    # Explain by comparing each item's contribution against what it would have been at the
    # headline rate: that isolates "you spend more on things that moved more".
    ranked = sorted(
        result.contributions,
        key=lambda c: abs(c.contribution_pp - c.weight * result.headline_pct),
        reverse=True,
    )
    drivers = [c for c in ranked if c.weight * result.headline_pct != c.contribution_pp][:top_n]
    parts = ", ".join(
        f"{c.name_en.lower()} ({c.weight * 100:.0f}% of your spending)" for c in drivers
    )
    return (
        f"Your inflation is {magnitude:.1f} percentage points {direction} the headline rate "
        f"({result.personal_pct:.1f}% vs {result.headline_pct:.1f}%). "
        f"The main drivers are {parts}."
    )


__all__ = [
    "Basket",
    "Contribution",
    "PersonalInflation",
    "compute_personal_inflation",
    "cumulative_from",
    "explain_gap",
    "inflation_by_division",
]
