"""Build the static artifacts the site and the Worker read.

Rule: the frontend never touches api.data.gov.my or storage.dosm.gov.my. It reads these
files and nothing else. That keeps the site fast, keeps upstream load off DOSM, and means a
DOSM outage cannot take the site down.

Every artifact carries its own provenance (`meta` block: dataset id, as-of date,
last-updated, upstream source) so a chart can render "data as of ..." without a second
lookup. There is deliberately no single global "last updated", because the sources refresh
on different cadences -- CPI monthly, formal wages monthly, state wages annually, HIES
biennially. One badge for all of them would be a lie.

Layout (all under `data/`):

    manifest.json               provenance for everything, plus contract reports
    cpi_national.json           monthly CPI by division, 1980 onwards
    cpi_state.json              monthly CPI by state x division, 2010 onwards
    cpi_groups.json             monthly CPI by 3-digit group (food away vs at home)
    cpi_strata.json             urban/rural CPI
    real_wages.json             nominal + real wages by state, plus the ranking
    household_income.json       HIES household income by state and nationally
    regional.json               the combined "inflation vs real income" ranking
    pricecatcher/items.json     item metadata
    pricecatcher/aggregate.parquet   item x state x month medians
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__, registry
from .contracts import DatasetReport, validate_frame
from .fetch import CatalogueMeta, Fetcher, sha256_file
from .transforms import cpi as cpi_t
from .transforms import forecast as forecast_t
from .transforms import income as income_t
from .transforms import pricecatcher as pc_t
from .transforms import wages as wages_t
from .transforms.anomaly import AnomalyReport
from .transforms.common import pivot_index, round_or_none
from .transforms.forecast import ForecastResult

# Analysis periods. Chosen by data availability, not to flatter the result -- the two
# sources simply start in different years. Reported side by side in the UI.
WAGE_START_YEAR = 2010
WAGE_END_YEAR = 2025
HIES_START_YEAR = 2012
HIES_END_YEAR = 2024


def _period(ts: pd.Timestamp | str) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m")


class ArtifactWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.index: dict[str, dict[str, Any]] = {}

    def write_json(self, name: str, payload: Any, *, indent: int | None = None) -> Path:
        path = self.out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=indent, ensure_ascii=False, allow_nan=False)
        path.write_text(text, encoding="utf-8")
        self.index[name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        return path

    def write_parquet(self, name: str, df: pd.DataFrame) -> Path:
        path = self.out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False, compression="zstd")
        self.index[name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        return path


def _series_payload(
    ds_id: str,
    meta: CatalogueMeta | None,
    *,
    periods: list[str],
    series: dict[str, list[float | None]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta_block: dict[str, Any] = {
        "dataset": ds_id,
        "source": meta.data_source if meta else [],
        "as_of": meta.data_as_of if meta else None,
        "last_updated": meta.last_updated if meta else None,
        "next_update": meta.next_update if meta else None,
        "frequency": meta.frequency if meta else None,
    }
    if extra:
        meta_block.update(extra)
    return {"meta": meta_block, "periods": periods, "series": series}


def _to_arrays(wide: pd.DataFrame) -> tuple[list[str], dict[str, list[float | None]]]:
    """Snowflake-shaped (periods + aligned series arrays) representation.

    Chosen over a list of records because it roughly halves the payload and lets the
    browser index by position.
    """
    periods = [_period(i) for i in wide.index]
    series = {str(col): [round_or_none(v, 3) for v in wide[col].tolist()] for col in wide.columns}
    return periods, series


# --------------------------------------------------------------------------- builders


def build_cpi_national(
    cpi: pd.DataFrame, mcoicop: pd.DataFrame, meta: CatalogueMeta | None
) -> dict[str, Any]:
    wide = pivot_index(cpi, column_col="division", value_col="index")
    periods, series = _to_arrays(wide)
    names = cpi_t.division_names(mcoicop)
    return _series_payload(
        "cpi_headline",
        meta,
        periods=periods,
        series=series,
        extra={
            "base_year": cpi_t.CPI_BASE_YEAR,
            "unit": "index (2010 = 100)",
            "division_names": {k: {"en": v.en, "bm": v.bm} for k, v in names.items()},
        },
    )


def build_cpi_state(
    cpi_state: pd.DataFrame, mcoicop: pd.DataFrame, meta: CatalogueMeta | None
) -> dict[str, Any]:
    """State x division monthly CPI, keyed 'State|division'."""
    frame = cpi_state.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    wide = frame.pivot_table(
        index="date", columns=["state", "division"], values="index"
    ).sort_index()
    wide.columns = [f"{s}|{d}" for s, d in wide.columns]
    periods, series = _to_arrays(wide)
    names = cpi_t.division_names(mcoicop)
    return _series_payload(
        "cpi_state",
        meta,
        periods=periods,
        series=series,
        extra={
            "base_year": cpi_t.CPI_BASE_YEAR,
            "unit": "index (2010 = 100)",
            "states": list(registry.STATES),
            "division_names": {k: {"en": v.en, "bm": v.bm} for k, v in names.items()},
        },
    )


def build_cpi_groups(
    cpi_3d: pd.DataFrame, mcoicop: pd.DataFrame, meta: CatalogueMeta | None
) -> dict[str, Any]:
    frame = cpi_3d.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    wide = frame.pivot_table(index="date", columns="group", values="index").sort_index()
    periods, series = _to_arrays(wide)
    names = cpi_t.group_names(mcoicop)

    base = wide.groupby(wide.index.year).mean()
    cumulative = {
        str(g): round_or_none(
            (base.loc[base.index.max(), g] / base.loc[cpi_t.CPI_BASE_YEAR, g] - 1) * 100, 2
        )
        for g in wide.columns
        if cpi_t.CPI_BASE_YEAR in base.index and pd.notna(base.loc[cpi_t.CPI_BASE_YEAR, g])
    }
    return _series_payload(
        "cpi_3d",
        meta,
        periods=periods,
        series=series,
        extra={
            "base_year": cpi_t.CPI_BASE_YEAR,
            "unit": "index (2010 = 100)",
            "group_names": {k: {"en": v.en, "bm": v.bm} for k, v in names.items()},
            "cumulative_from_base_pct": cumulative,
        },
    )


def build_cpi_strata(cpi_strata: pd.DataFrame, meta: CatalogueMeta | None) -> dict[str, Any]:
    frame = cpi_strata.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    wide = frame.pivot_table(
        index="date", columns=["strata", "division"], values="index"
    ).sort_index()
    wide.columns = [f"{s}|{d}" for s, d in wide.columns]
    periods, series = _to_arrays(wide)
    return _series_payload(
        "cpi_strata",
        meta,
        periods=periods,
        series=series,
        extra={"base_year": cpi_t.CPI_BASE_YEAR, "unit": "index (2010 = 100)"},
    )


def build_real_wages(
    wages_state: pd.DataFrame,
    cpi_state: pd.DataFrame,
    published_real: pd.DataFrame,
    meta_wages: CatalogueMeta | None,
    meta_real: CatalogueMeta | None,
) -> dict[str, Any]:
    real = wages_t.real_wages_by_state(wages_state, cpi_state)
    validation = wages_t.validate_against_dosm(real, published_real, year=WAGE_END_YEAR)
    summary = wages_t.real_wage_summary(
        real, cpi_state, start_year=WAGE_START_YEAR, end_year=WAGE_END_YEAR
    )

    return {
        "meta": {
            "datasets": ["wages_state", "cpi_state", "wages_real_state_citizen"],
            "source": ["DOSM"],
            "as_of": meta_wages.data_as_of if meta_wages else None,
            "last_updated": meta_wages.last_updated if meta_wages else None,
            "cpi_as_of": meta_real.data_as_of if meta_real else None,
            "base_year": wages_t.WAGE_REFERENCE_YEAR,
            "currency": "MYR (real values in 2010 ringgit)",
            "population": "employees who are Malaysian citizens",
            "period": {"start_year": WAGE_START_YEAR, "end_year": WAGE_END_YEAR},
            "validation_against_dosm": validation.model_dump(),
        },
        "series": [
            {
                "state": str(r["state"]),
                "year": int(r["year"]),
                "wages_median": round_or_none(r["wages_median"]),
                "wages_mean": round_or_none(r["wages_mean"]),
                "cpi": round_or_none(r["cpi"], 3),
                "real_median": round_or_none(r["real_median"]),
                "real_mean": round_or_none(r["real_mean"]),
            }
            for _, r in real.iterrows()
        ],
        "summary": [
            {k: (round_or_none(v) if isinstance(v, float) else v) for k, v in row.items()}
            for row in summary.to_dict(orient="records")
        ],
        "caveats": [
            "Covers employees only. The self-employed and informal sector are not included, "
            "and they are a large share of Malaysian workers.",
            "Medians are composition-sensitive: the median of the employee population shifts "
            "when the mix of employees changes, independently of anyone's pay.",
            "2020 reflects the pandemic shock to the employee population, not a normal year.",
        ],
    }


def build_household_income(
    hh_income_state: pd.DataFrame,
    hh_income: pd.DataFrame,
    cpi_state: pd.DataFrame,
    cpi_headline: pd.DataFrame,
    meta_state: CatalogueMeta | None,
) -> dict[str, Any]:
    by_state = income_t.household_income_by_state(hh_income_state, cpi_state)
    summary = income_t.household_income_summary(
        hh_income_state, cpi_state, start_year=HIES_START_YEAR, end_year=HIES_END_YEAR
    )
    national = income_t.national_household_income(hh_income, cpi_headline)

    years = income_t.survey_years(hh_income_state)
    return {
        "meta": {
            "datasets": ["hh_income_state", "hh_income", "cpi_state"],
            "source": ["DOSM"],
            "as_of": meta_state.data_as_of if meta_state else None,
            "last_updated": meta_state.last_updated if meta_state else None,
            "unit": "MYR per month (household)",
            "frequency": "survey years only -- irregular gaps, never interpolated",
            "survey_years": years,
            "period": {"start_year": HIES_START_YEAR, "end_year": HIES_END_YEAR},
        },
        "series": [
            {
                "state": str(r["state"]),
                "year": int(r["year"]),
                "income_median": round_or_none(r["income_median"]),
                "income_mean": round_or_none(r["income_mean"]),
                "cpi": round_or_none(r["cpi"], 3),
            }
            for _, r in by_state.iterrows()
        ],
        "national": [
            {
                "year": int(r["year"]),
                "income_median": round_or_none(r["income_median"]),
                "income_mean": round_or_none(r["income_mean"]),
                "cpi": round_or_none(r["cpi"], 3),
            }
            for _, r in national.iterrows()
        ],
        "summary": [
            {k: (round_or_none(v) if isinstance(v, float) else v) for k, v in row.items()}
            for row in summary.to_dict(orient="records")
        ],
        "caveats": [
            "HIES is a survey with irregular gaps between years. Lines between survey points "
            "are not drawn.",
            "Household income is not the same measure as individual wages; household size and "
            "the number of earners both move over time.",
        ],
    }


def build_regional(
    cpi_state: pd.DataFrame, wages_summary: pd.DataFrame, income_summary: pd.DataFrame
) -> dict[str, Any]:
    """The two rankings side by side -- the core of the regional divergence story."""
    inflation = cpi_t.state_inflation_ranking(
        cpi_state, start_year=WAGE_START_YEAR, end_year=WAGE_END_YEAR
    )
    by_state = {str(r["state"]): r for _, r in inflation.iterrows()}

    rows = []
    for _, r in wages_summary.iterrows():
        state = str(r["state"])
        rows.append(
            {
                "state": state,
                "cumulative_inflation_pct": round_or_none(r["cumulative_inflation_pct"]),
                "nominal_wage_growth_pct": round_or_none(r["nominal_growth_pct"]),
                "real_wage_growth_pct": round_or_none(r["real_growth_pct"]),
                "wages_median_start": round_or_none(r["wages_median_start"], 0),
                "wages_median_end": round_or_none(r["wages_median_end"], 0),
            }
        )
    wages_df = pd.DataFrame(rows)

    income_rows = []
    for _, r in income_summary.iterrows():
        income_rows.append(
            {
                "state": str(r["state"]),
                "cumulative_inflation_pct": round_or_none(r["cumulative_inflation_pct"]),
                "nominal_income_growth_pct": round_or_none(r["nominal_growth_pct"]),
                "real_income_growth_pct": round_or_none(r["real_growth_pct"]),
            }
        )
    income_df = pd.DataFrame(income_rows)

    def spread(df: pd.DataFrame, col: str) -> dict[str, Any]:
        """Min/max/spread for one metric.

        Returns {} rather than raising when the column is absent or entirely null. That
        happens legitimately: if a source's survey years stop overlapping the period, the
        ranking is empty, and the site should render "no comparable data here" instead of
        the whole build failing.
        """
        if col not in df.columns:
            return {}
        s = df[col].dropna()
        if s.empty:
            return {}
        return {
            "min_pct": round_or_none(s.min()),
            "min_state": str(df.loc[s.idxmin(), "state"]),
            "max_pct": round_or_none(s.max()),
            "max_state": str(df.loc[s.idxmax(), "state"]),
            "spread_pp": round_or_none(s.max() - s.min()),
        }

    return {
        "meta": {
            "source": ["DOSM"],
            "note": (
                "Inflation is uniform across states; income growth is not. Comparing these "
                "two spreads is the core finding."
            ),
            "wages_period": {"start_year": WAGE_START_YEAR, "end_year": WAGE_END_YEAR},
            "income_period": {"start_year": HIES_START_YEAR, "end_year": HIES_END_YEAR},
            "inflation_by_state_wages_period": [
                {"state": k, "cumulative_pct": round_or_none(v["cumulative_pct"])}
                for k, v in by_state.items()
            ],
        },
        "wages_ranking": wages_df.to_dict(orient="records"),
        "income_ranking": income_df.to_dict(orient="records"),
        "spreads": {
            "wages": {
                "inflation": spread(wages_df, "cumulative_inflation_pct"),
                "nominal_growth": spread(wages_df, "nominal_wage_growth_pct"),
                "real_growth": spread(wages_df, "real_wage_growth_pct"),
            },
            "income": {
                "inflation": spread(income_df, "cumulative_inflation_pct"),
                "nominal_growth": spread(income_df, "nominal_income_growth_pct"),
                "real_growth": spread(income_df, "real_income_growth_pct"),
            },
        },
    }


def build_pricecatcher(aggregate: pd.DataFrame, items: pd.DataFrame) -> dict[str, Any]:
    """Item metadata plus per-item national series. The full aggregate ships as parquet."""
    national = pc_t.national_item_series(aggregate)
    items_meta = (
        items.loc[:, ["item_code", "item", "unit", "item_group", "item_category"]]
        .sort_values("item_code")
        .to_dict(orient="records")
    )
    by_item: dict[str, dict[str, Any]] = {}
    for item_code, grp in national.groupby("item_code"):
        g = grp.sort_values("month")
        by_item[str(item_code)] = {
            "months": [str(m) for m in g["month"]],
            "median_price": [round_or_none(v) for v in g["median_price"]],
            "n_obs": [int(v) for v in g["n_obs"]],
            "n_states": [int(v) for v in g["n_states"]],
        }
    return {
        "meta": {
            "datasets": ["pricecatcher", "lookup_item", "lookup_premise"],
            "source": ["KPDN (PriceCatcher)"],
            "editions": sorted(aggregate["month"].unique().tolist()),
            "n_items": len(items_meta),
            "unit": "median observed price, MYR, per item's own unit",
            "caveat": (
                "PriceCatcher is a price-surveillance dataset. DOSM states explicitly that "
                "inflation analysis should use CPI, not this data. Figures here are for "
                "item-level illustration only and never feed an inflation number."
            ),
        },
        "items": [{**r, "item_code": int(r["item_code"])} for r in items_meta],
        "series": by_item,
    }


def _load_existing_manifest(path: Path) -> dict[str, Any]:
    """Read the previous manifest, so a partial run can extend rather than clobber it.

    `--only-pricecatcher` rebuilds one stage; without this it would write a manifest whose
    `datasets` and `artifacts` blocks cover only that stage, and the site would lose the
    provenance for everything else.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def build_forecast(
    results: list[ForecastResult], metas: dict[str, CatalogueMeta]
) -> dict[str, Any]:
    """Forecast artifact, one entry per series.

    The `verdict` and `beats_baseline` fields are the point: the UI is required to show when
    a model failed to beat the baseline, so that state has to be in the data, not inferred
    from the absence of a field.
    """
    meta = metas.get("cpi_headline")
    return {
        "meta": {
            "dataset": "cpi_headline",
            "source": meta.data_source if meta else ["DOSM"],
            "as_of": meta.data_as_of if meta else None,
            "last_updated": meta.last_updated if meta else None,
            "unit": "index (2010 = 100) for the path; percentage points for the rate",
            "baseline": forecast_t.BASELINE_MODEL,
            "scored_on": "year-on-year inflation rate, percentage points",
            "method_note": (
                "Every model is backtested with rolling-origin, expanding-window folds. "
                "Nothing is trained on data that would not have been available at the time."
            ),
        },
        "series": [result.model_dump() for result in results],
    }


def build_anomalies(
    reports: list[AnomalyReport], metas: dict[str, CatalogueMeta]
) -> dict[str, Any]:
    meta = metas.get("cpi_headline")
    return {
        "meta": {
            "dataset": "cpi_headline",
            "source": meta.data_source if meta else ["DOSM"],
            "as_of": meta.data_as_of if meta else None,
            "last_updated": meta.last_updated if meta else None,
            "method": "STL decomposition, robust z-scores on the remainder",
            "note": (
                "Flags are statistical, not explanatory. Coinciding movements in other series "
                "are corroboration only and are never presented as causes."
            ),
        },
        "series": [report.model_dump() for report in reports],
    }


def write_manifest(
    writer: ArtifactWriter,
    *,
    reports: dict[str, DatasetReport],
    metas: dict[str, CatalogueMeta],
    started_at: datetime,
) -> dict[str, Any]:
    previous = _load_existing_manifest(writer.out_dir / "manifest.json")
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "started_at": started_at.isoformat(timespec="seconds"),
        "pipeline_version": __version__,
        "source": "DOSM / data.gov.my open data (CC BY 4.0)",
        "datasets": {
            ds_id: {
                "tier": registry.DATASETS[ds_id].tier.value,
                "title": registry.DATASETS[ds_id].title,
                "as_of": metas[ds_id].data_as_of if ds_id in metas else None,
                "last_updated": metas[ds_id].last_updated if ds_id in metas else None,
                "next_update": metas[ds_id].next_update if ds_id in metas else None,
                "upstream_source": metas[ds_id].data_source if ds_id in metas else [],
                "report": reports[ds_id].model_dump(),
            }
            for ds_id in reports
        },
        "artifacts": {**previous.get("artifacts", {}), **writer.index},
    }
    # Datasets touched by this run win; untouched ones carry forward with their original
    # as-of dates, which is exactly what we want for a per-chart "data as of" badge.
    manifest["datasets"] = {**previous.get("datasets", {}), **manifest["datasets"]}
    # Drop artifact entries for files that no longer exist (a stage that got skipped).
    manifest["artifacts"] = {
        name: info
        for name, info in manifest["artifacts"].items()
        if name == "manifest.json" or (writer.out_dir / name).exists()
    }
    writer.write_json("manifest.json", manifest, indent=1)
    return manifest


def now_utc() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ArtifactWriter",
    "Fetcher",
    "build_anomalies",
    "build_cpi_groups",
    "build_cpi_national",
    "build_cpi_state",
    "build_cpi_strata",
    "build_forecast",
    "build_household_income",
    "build_pricecatcher",
    "build_real_wages",
    "build_regional",
    "now_utc",
    "validate_frame",
    "write_manifest",
]
