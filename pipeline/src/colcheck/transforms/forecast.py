"""CPI forecasting, with rolling-origin backtesting and an honest verdict.

Three deliberate choices, each of which changes the answer:

1.  **We model the index level, but we score the implied year-on-year inflation rate.**
    The index is a near-monotone trend with a clean seasonal structure, which is what SARIMA
    and ETS are good at. But a model that nails the index level to within 0.5 points has told
    you almost nothing, because the index always goes up. Percentage errors on a series
    sitting at ~130 also flatter everything. So accuracy is measured on the quantity the
    site actually makes a claim about: the year-on-year rate, in percentage points.

2.  **The baseline is a real one: "inflation stays what it was a year ago."**
    Seasonal-naive on the index means predicting next October's index is this October's
    index times last year's October-to-October change. That is a genuinely hard baseline to
    beat for CPI, and saying so when we fail to beat it is the point of the exercise.

3.  **Every backtest is rolling-origin and expanding-window.** No shuffling, no training on
    the future. For each fold we fit only on data available at that moment and forecast
    `horizon` months forward.

Because a year-on-year rate at horizon h ≤ 12 refers to an index 12 months earlier, and that
value is always inside the training window, implied YoY is computable directly from the
forecast without look-ahead.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SEASONAL_PERIOD = 12
DEFAULT_HORIZON = 12
DEFAULT_FOLDS = 12
# The benchmark every other model must beat. See `inflation_persistence` for why this is not
# the textbook seasonal-naive baseline.
BASELINE_MODEL = "inflation_persistence"
MIN_TRAIN_MONTHS = 120  # ten years: enough to estimate a stable seasonal pattern


class ForecastPoint(BaseModel):
    """One forecast month.

    Both the index path and the implied year-on-year rate are carried, in point and interval
    form, because the site charts the rate while the model works in levels. Handing the
    frontend only the level would force it to re-derive the rate and get the base month wrong.
    """

    period: str
    point: float
    lo80: float | None = None
    hi80: float | None = None
    implied_yoy: float | None = None
    implied_yoy_lo80: float | None = None
    implied_yoy_hi80: float | None = None


class ModelScore(BaseModel):
    """Backtest accuracy, measured on year-on-year inflation in percentage points."""

    model: str
    mae_pp: float
    mape_pct: float
    rmse_pp: float
    n_observations: int

    def better_than(self, other: ModelScore) -> bool:
        return self.mae_pp < other.mae_pp


class ForecastResult(BaseModel):
    series_id: str
    label: str
    method: str
    horizon: int
    last_observed_period: str
    last_observed_index: float
    points: list[ForecastPoint] = Field(default_factory=list)
    scores: list[ModelScore] = Field(default_factory=list)
    baseline_model: str = BASELINE_MODEL
    beats_baseline: bool = False
    verdict: str = ""
    caveats: list[str] = Field(default_factory=list)


class Forecaster(Protocol):
    """A model is anything that turns a training series into a forward path."""

    name: str

    def __call__(self, train: pd.Series, horizon: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (point, lower80, upper80) arrays of length `horizon`."""


@dataclass
class _Callable:
    name: str
    fn: Callable[[pd.Series, int], tuple[np.ndarray, np.ndarray, np.ndarray]]

    def __call__(self, train: pd.Series, horizon: int):
        return self.fn(train, horizon)


# --------------------------------------------------------------------- the models


def inflation_persistence(
    train: pd.Series, horizon: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """THE baseline: the year-on-year inflation rate stays what it was a year ago.

    Implements ``level(t) = level(t-12) * (1 + yoy(t-12))``, equivalently
    ``level(t-12)^2 / level(t-24)``.

    **Why not the textbook seasonal-naive baseline (``level(t) = level(t-12)``)?**
    Because we score on the year-on-year *rate*, and under that scoring seasonal-naive
    predicts exactly 0.0% inflation at every horizon, by construction -- it isn't a forecast
    of anything, it's an identity. Scoring a real model against a straw man that says
    "inflation is always zero" would flatter the result and prove nothing. Persistence of the
    inflation rate is the benchmark that actually has to be beaten, and for CPI it is a hard
    one.

    Intervals come from the empirical spread of 12-month changes in the inflation rate, so
    they widen at the rate the baseline's own error actually widens.
    """
    if len(train) < SEASONAL_PERIOD * 2:
        raise ValueError("inflation_persistence needs at least two full years of history")

    values = train.to_numpy(dtype=float)
    point = np.empty(horizon, dtype=float)
    for h in range(horizon):
        here = values[-SEASONAL_PERIOD + (h % SEASONAL_PERIOD)]
        year_ago = values[-2 * SEASONAL_PERIOD + (h % SEASONAL_PERIOD)]
        point[h] = here * (here / year_ago) if year_ago else np.nan

    yoy = (values[SEASONAL_PERIOD:] / values[:-SEASONAL_PERIOD] - 1.0) * 100.0
    rate_changes = (
        np.diff(yoy[::1], n=SEASONAL_PERIOD) if len(yoy) > SEASONAL_PERIOD else np.array([])
    )
    sigma = float(np.nanstd(rate_changes, ddof=1)) if len(rate_changes) > 1 else 0.0
    # Translate a spread in the inflation rate into a spread in the index level.
    z80 = 1.2816  # 80% two-sided normal quantile
    widths = 1.0 + 0.01 * z80 * sigma * np.sqrt(np.arange(1, horizon + 1) / SEASONAL_PERIOD + 1.0)
    return point, point / widths, point * widths


def _guarded(
    model_fn: Callable[[pd.Series, int], tuple[np.ndarray, np.ndarray, np.ndarray]], name: str
):
    """Wrap a forecaster so a numerical failure returns a clearly-marked gap.

    SARIMA occasionally fails to converge on a particular window. Raising would abort a whole
    backtest over one fold; silently returning a plausible number would be worse. Returning
    NaN makes the fold drop out and the observation count record why.
    """

    def inner(train: pd.Series, horizon: int):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return model_fn(train, horizon)
        except Exception as exc:  # noqa: BLE001 - numerical failure of any kind
            log.warning("%s failed on a %d-point window: %s", name, len(train), exc)
            nan = np.full(horizon, np.nan)
            return nan, nan, nan

    return _Callable(name, inner)


def ets_forecast(train: pd.Series, horizon: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Holt-Winters additive trend + additive seasonality."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    model = ExponentialSmoothing(
        train,
        trend="add",
        seasonal="add",
        seasonal_periods=SEASONAL_PERIOD,
        initialization_method="estimated",
    ).fit()
    point = np.asarray(model.forecast(horizon), dtype=float)
    # ExponentialSmoothing exposes intervals only for the in-sample path in this version, so
    # we build them from the residual scale, widening with the square root of the horizon.
    resid_sigma = float(np.nanstd(np.asarray(model.resid, dtype=float), ddof=1))
    widths = 1.2816 * resid_sigma * np.sqrt(np.arange(1, horizon + 1))
    return point, point - widths, point + widths


def sarima_forecast(train: pd.Series, horizon: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """SARIMAX(1,1,1)(1,1,1,12) -- a standard, deliberately unexotic airline-style model.

    Chosen over an auto-ARIMA search so the result is reproducible and the parameters can be
    stated in the methodology. We are not trying to win a competition; we are trying to find
    out whether a standard model beats a seasonal-naive baseline, and to be able to say why.
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    model = SARIMAX(
        train,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, SEASONAL_PERIOD),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fit = model.fit(disp=False)
    pred = fit.get_forecast(steps=horizon)
    mean = np.asarray(pred.predicted_mean, dtype=float)
    ci = np.asarray(pred.conf_int(alpha=0.20), dtype=float)  # 80% interval
    return mean, ci[:, 0], ci[:, 1]


def default_models() -> list[_Callable]:
    return [
        _Callable(BASELINE_MODEL, inflation_persistence),
        _guarded(ets_forecast, "ets"),
        _guarded(sarima_forecast, "sarima"),
    ]


# ------------------------------------------------------------------- evaluation


def _monthly_freq(index: pd.DatetimeIndex) -> str:
    """The offset to extend a monthly index by.

    Everything this module forecasts is month-start CPI, so the answer is always "MS". We
    verify that rather than calling `pd.infer_freq`, which would happily characterise an
    irregular index as regular and let a broken series through.
    """
    if len(index) < 3:
        return "MS"
    gaps = pd.Series(index).diff().dropna().dt.days
    if not gaps.between(28, 31).all():
        raise ValueError(
            "forecast expects a regular month-start index; found gaps of "
            f"{sorted(gaps.unique())} days"
        )
    return "MS"


def implied_yoy(index_forecast: np.ndarray, history: pd.Series, horizon: int) -> np.ndarray:
    """Convert a forecast index path into implied year-on-year inflation percentages.

    Valid for horizon <= 12, where the year-earlier index is still inside `history`. For
    longer horizons the year-earlier value would itself be a forecast, and the resulting
    number would be a forecast of a forecast with no honest interval -- so we return NaN
    rather than pretend.
    """
    values = history.to_numpy(dtype=float)
    out = np.full(horizon, np.nan)
    for h in range(min(horizon, SEASONAL_PERIOD)):
        base = values[-SEASONAL_PERIOD + h]
        if base and not np.isnan(base) and not np.isnan(index_forecast[h]):
            out[h] = (index_forecast[h] / base - 1.0) * 100.0
    return out


def rolling_origin_backtest(
    series: pd.Series,
    forecaster: Forecaster,
    *,
    horizon: int = DEFAULT_HORIZON,
    n_folds: int = DEFAULT_FOLDS,
    step: int = 1,
) -> pd.DataFrame:
    """Expanding-window rolling-origin backtest.

    Returns one row per fold with the realised and predicted year-on-year rate for each
    horizon step. Nothing here touches data that would not have been available at the time.
    """
    series = series.sort_index().dropna()
    if len(series) < MIN_TRAIN_MONTHS + horizon:
        raise ValueError(
            f"need at least {MIN_TRAIN_MONTHS + horizon} observations to backtest "
            f"{horizon} steps; got {len(series)}"
        )

    last_train_end = len(series) - horizon
    first_train_end = max(MIN_TRAIN_MONTHS, last_train_end - (n_folds - 1) * step)
    train_ends = list(range(first_train_end, last_train_end + 1, step))[-n_folds:]

    rows = []
    for offset, end in enumerate(train_ends):
        train = series.iloc[:end]
        actual_index = series.iloc[end : end + horizon]
        if len(actual_index) < horizon:
            continue
        point, lo, hi = forecaster(train, horizon)
        pred_yoy = implied_yoy(point, train, horizon)
        lo_yoy = implied_yoy(lo, train, horizon)
        hi_yoy = implied_yoy(hi, train, horizon)
        actual_yoy = {
            d: (actual_index.iloc[h] / train.iloc[-SEASONAL_PERIOD + h] - 1.0) * 100.0
            for h, d in enumerate(actual_index.index)
            if SEASONAL_PERIOD - h <= len(train)
        }
        for h, date in enumerate(actual_index.index):
            if h >= SEASONAL_PERIOD:
                break
            rows.append(
                {
                    "fold": offset,
                    "origin": train.index[-1],
                    "target": date,
                    "horizon_step": h + 1,
                    "actual_yoy": actual_yoy.get(date, np.nan),
                    "pred_yoy": pred_yoy[h],
                    "lo80_yoy": lo_yoy[h],
                    "hi80_yoy": hi_yoy[h],
                }
            )
    return pd.DataFrame(rows)


def score_fold_results(results: pd.DataFrame, model_name: str) -> ModelScore:
    valid = results.dropna(subset=["actual_yoy", "pred_yoy"])
    if valid.empty:
        return ModelScore(
            model=model_name, mae_pp=float("nan"), mape_pct=float("nan"),
            rmse_pp=float("nan"), n_observations=0,
        )  # fmt: skip
    error = valid["pred_yoy"] - valid["actual_yoy"]
    # MAPE on a rate near zero explodes; guard the denominator rather than let one month in
    # a near-deflationary period dominate the whole score.
    denom = valid["actual_yoy"].abs().clip(lower=0.25)
    return ModelScore(
        model=model_name,
        mae_pp=float(error.abs().mean()),
        mape_pct=float((error.abs() / denom).mean() * 100.0),
        rmse_pp=float(np.sqrt((error**2).mean())),
        n_observations=int(len(valid)),
    )


def interval_coverage(results: pd.DataFrame, *, nominal: float = 0.80) -> float:
    """Share of outcomes inside the stated interval.

    Reported alongside accuracy because a forecast with good MAE and 40% interval coverage is
    worse than one with slightly worse MAE and honest intervals.
    """
    valid = results.dropna(subset=["actual_yoy", "lo80_yoy", "hi80_yoy"])
    if valid.empty:
        return float("nan")
    inside = (valid["actual_yoy"] >= valid["lo80_yoy"]) & (valid["actual_yoy"] <= valid["hi80_yoy"])
    return float(inside.mean())


def forecast_series(
    series: pd.Series,
    *,
    series_id: str,
    label: str,
    horizon: int = DEFAULT_HORIZON,
    n_folds: int = DEFAULT_FOLDS,
    models: list[Forecaster] | None = None,
) -> ForecastResult:
    """Backtest every model, pick the best by MAE, and produce a forward path with intervals.

    The chosen method is whichever had the lowest mean absolute error on the backtest. If
    that is the seasonal-naive baseline, we select it and say so -- a forecast that loses to
    "same as last year" has no business being presented as insight.
    """
    series = series.sort_index().dropna()
    models = models or default_models()

    scores: list[ModelScore] = []
    fold_frames: dict[str, pd.DataFrame] = {}
    coverage: dict[str, float] = {}
    for model in models:
        try:
            folds = rolling_origin_backtest(series, model, horizon=horizon, n_folds=n_folds)
        except ValueError as exc:
            log.warning("%s: backtest skipped (%s)", model.name, exc)
            continue
        fold_frames[model.name] = folds
        scores.append(score_fold_results(folds, model.name))
        coverage[model.name] = interval_coverage(folds)

    if not scores:
        raise ValueError(f"no model produced a usable backtest for {series_id}")

    ranked = sorted(scores, key=lambda s: (np.isnan(s.mae_pp), s.mae_pp))
    best = ranked[0]
    baseline = next((s for s in scores if s.model == BASELINE_MODEL), None)
    beats = bool(baseline and not np.isnan(best.mae_pp) and best.mae_pp < baseline.mae_pp)

    chosen = next(m for m in models if m.name == best.model)
    point, lo, hi = chosen(series, horizon)
    freq = _monthly_freq(series.index)
    future = pd.date_range(series.index[-1], periods=horizon + 1, freq=freq)[1:]
    yoy = implied_yoy(point, series, horizon)
    lo_yoy = implied_yoy(lo, series, horizon)
    hi_yoy = implied_yoy(hi, series, horizon)

    caveats = [
        "Backtests are rolling-origin and expanding-window: each fold fits only on data "
        "available at that time.",
        "Accuracy is measured on the year-on-year inflation rate in percentage points, not "
        "on the index level. Scoring on the index would flatter every model, since the index "
        "only goes up.",
        "The baseline is persistence of the inflation rate (last year's rate), not the "
        "textbook seasonal-naive index baseline -- under year-on-year scoring that one "
        "predicts exactly 0% inflation by construction and would be a straw man.",
    ]
    if not beats:
        caveats.append(
            f"The best model ({best.model}) does NOT beat the persistence baseline "
            f"(MAE {best.mae_pp:.2f}pp vs {baseline.mae_pp:.2f}pp). Treat these projections as "
            "a continuation of current trends, not as a forecast that has earned trust."
        )
    if coverage.get(best.model, 0) < 0.60:
        caveats.append(
            f"The 80% interval contained the outcome only {coverage.get(best.model, 0) * 100:.0f}% "
            "of the time in backtesting, so the interval is narrower than it should be."
        )

    verdict = (
        f"{best.model} beat the persistence baseline on the backtest "
        f"(MAE {best.mae_pp:.2f}pp vs {baseline.mae_pp:.2f}pp over {best.n_observations} forecasts)."
        if beats and baseline
        else f"No model beat the persistence baseline over {best.n_observations} "
        f"backtested forecasts; the baseline is shown."
    )

    return ForecastResult(
        series_id=series_id,
        label=label,
        method=best.model,
        horizon=horizon,
        last_observed_period=series.index[-1].strftime("%Y-%m"),
        last_observed_index=float(series.iloc[-1]),
        points=[
            ForecastPoint(
                period=future[i].strftime("%Y-%m"),
                point=float(point[i]),
                lo80=float(lo[i]) if not np.isnan(lo[i]) else None,
                hi80=float(hi[i]) if not np.isnan(hi[i]) else None,
                implied_yoy=float(yoy[i]) if not np.isnan(yoy[i]) else None,
                implied_yoy_lo80=float(lo_yoy[i]) if not np.isnan(lo_yoy[i]) else None,
                implied_yoy_hi80=float(hi_yoy[i]) if not np.isnan(hi_yoy[i]) else None,
            )
            for i in range(horizon)
        ],
        scores=scores,
        beats_baseline=beats,
        verdict=verdict,
        caveats=caveats,
    )
