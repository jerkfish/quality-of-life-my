"""Anomaly and regime detection on the monthly CPI.

Two techniques, both chosen because their output can be explained to a non-statistician:

*   **STL residuals with a robust z-score.** Decompose the index into trend, seasonal and
    remainder; flag months where the remainder is far from its typical spread. Crucially the
    spread is measured with the median absolute deviation, not the standard deviation --
    otherwise a single large shock inflates the scale and hides itself, which is exactly the
    failure mode we care about.

*   **Binary segmentation on the year-on-year rate.** Find the points where the level of
    inflation shifts durably, which is a different question from "one weird month". A
    two-month spike and a permanent change of regime look identical to a residual test.

**On annotation.** We flag what the data says and deliberately attach no narrative. Where a
flag coincides with something independently datable in another dataset we fetch (fuel prices,
labour force), we report the coincidence as evidence, not as a cause. We do not write
"because of the pandemic" anywhere -- we write "the largest residual in the series, in
2020-04, coinciding with a 3.1% monthly fall in the labour force."
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SEASONAL_PERIOD = 12
# 3.5 robust sigmas. With ~550 monthly observations, this yields roughly a handful of flags
# rather than a list of every slightly-odd month.
DEFAULT_Z_THRESHOLD = 3.5
MAD_TO_SIGMA = 1.4826  # makes MAD a consistent estimator of sigma for normal data
MEAN_AD_TO_SIGMA = 1.2533  # same, for the mean absolute deviation (sqrt(pi/2))
# The fuelprice dataset packs a price level series and a weekly-change series into the same
# columns, distinguished only by `series_type`. Only one of them is a price in ringgit.
FUEL_LEVEL_SERIES = "level"
# Floor on the residual scale, as a fraction of the series level.
#
# Without this, a series that STL fits essentially exactly -- a pure deterministic trend plus
# seasonal, for instance -- has a remainder of ~1e-14 and a MAD to match. Dividing those
# residuals by that MAD produces z-scores of +-4 from floating-point noise, and the detector
# confidently reports anomalies in data containing none. A scale this small carries no
# information about real variation, so we decline to flag anything rather than invent it.
MIN_SCALE_FRACTION = 1e-8


class Anomaly(BaseModel):
    period: str
    index: float
    residual: float
    # Signed robust z-score: negative means the index came in below its trend+seasonal path.
    z: float
    direction: str  # "above" | "below"
    yoy_at_period: float | None = None


class Regime(BaseModel):
    start_period: str
    end_period: str
    months: int
    mean_yoy: float
    min_yoy: float
    max_yoy: float


class AnomalyReport(BaseModel):
    series_id: str
    label: str
    first_period: str
    last_period: str
    threshold_z: float
    anomalies: list[Anomaly] = Field(default_factory=list)
    regimes: list[Regime] = Field(default_factory=list)
    # Movement in independent series over the same window, as corroboration only.
    # Values are figures, except `anomaly_period` which names the flag they describe.
    context: dict[str, float | str] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)


def robust_z(values: pd.Series, *, floor_fraction: float = MIN_SCALE_FRACTION) -> pd.Series:
    """Median-centred z-scores, scaled robustly but without a blind spot.

    The scale is the median absolute deviation, because a standard deviation would let a
    single large shock inflate the estimate and hide itself.

    MAD has one failure mode, though: it is exactly zero whenever more than half the values
    are identical. That happens for real here -- STL fits a cleanly seasonal series almost
    exactly, so most residuals sit at 1e-14 while one month carries a genuine spike, and a
    MAD-scaled detector would announce "no anomalies" in the one case it most needs to catch.

    So when MAD is degenerate we fall back to the mean absolute deviation about the median,
    which stays well-defined. Only if *both* are below the noise floor do we return zeros --
    that is the case where the series genuinely carries no information, and flagging nothing
    is the honest answer.
    """
    median = float(values.median())
    deviation = (values - median).abs()
    sigma = float(deviation.median()) * MAD_TO_SIGMA

    scale_floor = floor_fraction * max(1.0, float(values.abs().max()))
    if sigma < scale_floor:
        # 1.2533 = sqrt(pi/2): converts mean absolute deviation to a normal-consistent sigma.
        sigma = float(deviation.mean()) * MEAN_AD_TO_SIGMA
    if not sigma or np.isnan(sigma) or sigma < scale_floor:
        return pd.Series(np.zeros(len(values)), index=values.index, dtype=float)
    return (values - median) / sigma


def stl_decompose(series: pd.Series, *, period: int = SEASONAL_PERIOD):
    """STL with robust fitting, on a gap-free monthly series.

    Raises if the series is too short or has gaps: STL will happily produce numbers from an
    irregular series, and those numbers are meaningless.
    """
    from statsmodels.tsa.seasonal import STL

    series = series.sort_index()
    if series.isna().any():
        raise ValueError("STL requires a gap-free series; found nulls")
    if len(series) < 2 * period:
        raise ValueError(f"STL needs at least {2 * period} observations; got {len(series)}")
    return STL(series, period=period, robust=True).fit()


def find_anomalies(
    series: pd.Series,
    *,
    series_id: str,
    label: str,
    threshold_z: float = DEFAULT_Z_THRESHOLD,
    period: int = SEASONAL_PERIOD,
) -> list[Anomaly]:
    """Months whose STL remainder is unusually large in either direction."""
    decomposition = stl_decompose(series, period=period)
    residual = pd.Series(decomposition.resid, index=series.index).dropna()
    z = robust_z(residual)

    yoy = (series / series.shift(period) - 1.0) * 100.0
    flagged = z[z.abs() >= threshold_z].sort_index()
    return [
        Anomaly(
            period=ts.strftime("%Y-%m"),
            index=float(series.loc[ts]),
            residual=float(residual.loc[ts]),
            z=float(z.loc[ts]),
            direction="above" if z.loc[ts] > 0 else "below",
            yoy_at_period=(
                float(yoy.loc[ts]) if ts in yoy.index and pd.notna(yoy.loc[ts]) else None
            ),
        )
        for ts in flagged.index
    ]


def _segment_means(values: np.ndarray) -> float:
    """Total within-segment squared deviation for a single segment."""
    if len(values) == 0:
        return 0.0
    return float(((values - values.mean()) ** 2).sum())


def binary_segmentation(
    series: pd.Series, *, max_regimes: int = 4, min_segment: int = 24
) -> list[int]:
    """Find change points by greedily splitting wherever the fit improves most.

    A deliberately simple method. PELT or a Bayesian change-point model would find more
    nuanced breaks, but binary segmentation is explainable in one sentence, reproducible
    without a random seed, and adequate for a series with a handful of real regime shifts.

    Returns the sorted indices at which a new segment starts (excluding 0).
    """
    values = series.to_numpy(dtype=float)
    n = len(values)
    if n < 2 * min_segment:
        return []

    breakpoints: list[int] = []
    for _ in range(max_regimes - 1):
        best_gain, best_index = 0.0, None
        bounds = [0, *breakpoints, n]
        for left, right in zip(bounds[:-1], bounds[1:], strict=True):
            segment = values[left:right]
            if len(segment) < 2 * min_segment:
                continue
            base_cost = _segment_means(segment)
            for cut in range(left + min_segment, right - min_segment + 1):
                gain = (
                    base_cost - _segment_means(values[left:cut]) - _segment_means(values[cut:right])
                )
                if gain > best_gain:
                    best_gain, best_index = gain, cut
        if best_index is None or best_gain <= 0:
            break
        breakpoints = sorted([*breakpoints, best_index])
    return breakpoints


def find_regimes(series: pd.Series, *, max_regimes: int = 4, min_segment: int = 24) -> list[Regime]:
    """Durable shifts in the year-on-year inflation rate."""
    yoy = ((series / series.shift(SEASONAL_PERIOD) - 1.0) * 100.0).dropna()
    if yoy.empty:
        return []
    breaks = binary_segmentation(yoy, max_regimes=max_regimes, min_segment=min_segment)
    bounds = [0, *breaks, len(yoy)]
    out = []
    for left, right in zip(bounds[:-1], bounds[1:], strict=True):
        segment = yoy.iloc[left:right]
        if segment.empty:
            continue
        out.append(
            Regime(
                start_period=segment.index[0].strftime("%Y-%m"),
                end_period=segment.index[-1].strftime("%Y-%m"),
                months=int(len(segment)),
                mean_yoy=float(segment.mean()),
                min_yoy=float(segment.min()),
                max_yoy=float(segment.max()),
            )
        )
    return out


def _series_span(
    frame: pd.DataFrame | None, column: str
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """First and last period for which a context series actually has data."""
    if frame is None or "date" not in frame.columns or column not in frame.columns:
        return None
    usable = frame.loc[frame[column].notna(), "date"]
    if usable.empty:
        return None
    stamps = pd.to_datetime(usable)
    return stamps.min(), stamps.max()


def corroborating_context(
    anomalies: list[Anomaly],
    *,
    fuelprice: pd.DataFrame | None = None,
    lfs_month: pd.DataFrame | None = None,
    window_months: int = 3,
) -> dict[str, float | str]:
    """Movement in independent series around the most notable anomaly we can speak to.

    **Why not simply take the largest anomaly?** Because on real data it usually predates
    every corroborating series. The single largest flag on headline CPI is 2008-07 (z = 30.5),
    the global oil and food price spike -- and the fuel price series starts in 2017 while the
    labour force series starts in 2010. Selecting the largest anomaly unconditionally made
    this function return nothing on every input, forever. It looked like a feature and was
    dead code.

    So we take the most extreme anomaly that falls inside at least one context series'
    coverage, and return the period alongside the figures so the UI knows which flag the
    context belongs to.

    If nothing qualifies the dict is empty and the UI has nothing to add, which is the honest
    outcome rather than a failure.
    """
    if not anomalies:
        return {}

    fuel_span = _series_span(fuelprice, "ron95")
    lfs_span = _series_span(lfs_month, "lf_employed")
    spans = [s for s in (fuel_span, lfs_span) if s is not None]
    if not spans:
        return {}

    def is_covered(anomaly: Anomaly) -> bool:
        moment = pd.Timestamp(f"{anomaly.period}-01")
        return any(start <= moment <= end for start, end in spans)

    eligible = [a for a in anomalies if is_covered(a)]
    if not eligible:
        return {}

    pivot_anomaly = max(eligible, key=lambda a: abs(a.z))
    pivot = pd.Timestamp(f"{pivot_anomaly.period}-01")
    context: dict[str, float | str] = {
        "anomaly_period": pivot_anomaly.period,
        "anomaly_z": round(float(pivot_anomaly.z), 2),
    }

    if fuelprice is not None and fuel_span is not None and fuel_span[0] <= pivot <= fuel_span[1]:
        fp = fuelprice.copy()
        fp["date"] = pd.to_datetime(fp["date"])
        # CRITICAL: fuelprice stores two different quantities in the same columns, split by
        # `series_type` -- 'level' (the retail price in RM) and 'change_weekly' (the weekly
        # change). Without this filter the window mixes both, and the "price change" it
        # reports is meaningless: the first version of this returned -100% for April 2020
        # because it happened to start on a level row and end on a change row.
        if "series_type" in fp.columns:
            fp = fp[fp["series_type"] == FUEL_LEVEL_SERIES]
        # Sort before taking first and last. The upstream file happens to arrive sorted, so an
        # unsorted read still produces a plausible-looking number -- just with the sign
        # flipped. `iloc[0]`/`iloc[-1]` on an unsorted frame is a silent correctness bug, not a
        # style preference.
        window = (
            fp[
                (fp["date"] >= pivot - pd.DateOffset(months=window_months))
                & (fp["date"] <= pivot + pd.DateOffset(months=window_months))
            ]
            .dropna(subset=["ron95"])
            .sort_values("date")
        )
        if len(window) >= 2 and window["ron95"].iloc[0] > 0:
            context["ron95_price_change_pct_in_window"] = round(
                float((window["ron95"].iloc[-1] / window["ron95"].iloc[0] - 1.0) * 100.0), 2
            )

    if lfs_month is not None and lfs_span is not None and lfs_span[0] <= pivot <= lfs_span[1]:
        lf = lfs_month.copy()
        lf["date"] = pd.to_datetime(lf["date"])
        lf = lf.sort_values("date")  # same ordering assumption as above
        before = lf[lf["date"] <= pivot]["lf_employed"].dropna()
        after = lf[
            (lf["date"] > pivot) & (lf["date"] <= pivot + pd.DateOffset(months=window_months))
        ]["lf_employed"].dropna()
        if len(before) >= 1 and len(after) >= 1 and before.iloc[-1]:
            context["employment_change_pct_after"] = round(
                float((after.iloc[-1] / before.iloc[-1] - 1.0) * 100.0), 2
            )
    return context


def build_report(
    series: pd.Series,
    *,
    series_id: str,
    label: str,
    threshold_z: float = DEFAULT_Z_THRESHOLD,
    max_regimes: int = 4,
    fuelprice: pd.DataFrame | None = None,
    lfs_month: pd.DataFrame | None = None,
) -> AnomalyReport:
    series = series.sort_index().dropna()
    anomalies = find_anomalies(series, series_id=series_id, label=label, threshold_z=threshold_z)
    regimes = find_regimes(series, max_regimes=max_regimes)
    context = corroborating_context(anomalies, fuelprice=fuelprice, lfs_month=lfs_month)

    caveats = [
        f"Anomalies are months where the STL remainder exceeds {threshold_z} robust standard "
        "deviations (median absolute deviation). This is a statistical flag, not an "
        "explanation.",
        "Coinciding movements in other series are shown as corroboration only; they are not "
        "evidence of cause.",
    ]
    return AnomalyReport(
        series_id=series_id,
        label=label,
        first_period=series.index[0].strftime("%Y-%m"),
        last_period=series.index[-1].strftime("%Y-%m"),
        threshold_z=threshold_z,
        anomalies=anomalies,
        regimes=regimes,
        context=context,
        caveats=caveats,
    )
