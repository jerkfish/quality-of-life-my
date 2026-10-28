"""Pipeline orchestration: fetch -> validate -> transform -> write artifacts.

Kept separate from the CLI so tests can drive a whole run without spawning a process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from . import artifacts, registry
from .artifacts import ArtifactWriter
from .contracts import ContractError, DatasetReport, validate_frame
from .fetch import CatalogueMeta, Fetcher
from .transforms import anomaly as anomaly_t
from .transforms import cpi as cpi_t
from .transforms import forecast as forecast_t
from .transforms import pricecatcher as pc_t

log = logging.getLogger(__name__)

# Everything needed for the core artifacts. Order matters only for logging clarity.
CORE_DATASETS: tuple[str, ...] = (
    "cpi_headline",
    "cpi_state",
    "cpi_3d",
    "cpi_strata",
    "mcoicop",
    "wages_state",
    "wages_real_state_citizen",
    "hh_income_state",
    "hh_income",
)

ITEM_DATASETS: tuple[str, ...] = ("lookup_item", "lookup_premise", "pricecatcher")

# Fewer folds than the 12 used to be the ceiling: at ~12s per series, backtesting 14 series
# needs to stay inside a sensible CI budget. Eight folds still spans eight different forecast
# origins, which is enough to rank the models against each other.
FORECAST_FOLDS = 8
FORECAST_HORIZON = 12
# Below this, a seasonal model has nothing to estimate; the division is skipped, not padded.
MIN_SERIES_MONTHS = 120


@dataclass
class RunConfig:
    raw_dir: Path = Path(".cache/raw")
    meta_dir: Path = Path(".cache/meta")
    out_dir: Path = Path("../data")
    offline: bool = False
    skip_pricecatcher: bool = False
    only_pricecatcher: bool = False
    # Cap the number of PriceCatcher editions. None means all.
    pricecatcher_limit: int | None = None
    # Forecasting is the slowest analytical stage (~12s per series). None means the default
    # set: national headline plus every CPI division.
    skip_forecast: bool = False
    forecast_limit: int | None = None
    forecast_folds: int = FORECAST_FOLDS


def _division_series(
    cpi: pd.DataFrame, names: dict[str, Any] | None = None
) -> list[tuple[str, str, pd.Series]]:
    """Split a national CPI frame into one monthly index series per division.

    Returns (series_id, label, series) with 'overall' first, then divisions by code.

    Series are trimmed to their first non-null observation: divisions added after 1980 have a
    run of leading nulls, and both STL and the seasonal models require a regular, gap-free
    series. Trimming the head is safe; trimming from the middle would not be, and the
    contract layer has already rejected any frame containing interior gaps in the key.
    """
    names = names or {}
    ready: list[tuple[str, str, pd.Series]] = []
    codes = sorted(c for c in cpi["division"].unique() if c != registry.CPI_OVERALL)
    for code in [registry.CPI_OVERALL, *codes]:
        block = cpi[cpi["division"] == code]
        if block.empty:
            continue
        values = block.set_index(pd.to_datetime(block["date"]))["index"].sort_index().dropna()
        if len(values) < MIN_SERIES_MONTHS:
            log.warning(
                "division %s has only %d usable months; skipping analysis", code, len(values)
            )
            continue
        entry = names.get(code)
        label = getattr(entry, "en", None) or (
            "All items (headline)" if code == registry.CPI_OVERALL else code
        )
        ready.append((f"cpi_headline:{code}", label, values))
    return ready


@dataclass
class RunResult:
    manifest: dict
    reports: dict[str, DatasetReport] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, config: RunConfig, *, fetcher: Fetcher | None = None) -> None:
        self.config = config
        # Injectable so orchestration can be tested without the network. Everything else
        # about the runner is deliberately side-effect free.
        self.fetcher = fetcher or Fetcher(config.raw_dir, config.meta_dir, offline=config.offline)
        self._owns_fetcher = fetcher is None
        self.reports: dict[str, DatasetReport] = {}
        self.metas: dict[str, CatalogueMeta] = {}
        self.frames: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ loading

    def load(self, dataset_id: str, *, catalogue_meta: bool = True) -> pd.DataFrame:
        """Fetch, read and contract-check one dataset. Cached per run."""
        if dataset_id in self.frames:
            return self.frames[dataset_id]

        ds = registry.get(dataset_id)
        path = self.fetcher.fetch_parquet(ds)
        frame = pd.read_parquet(path)
        report = validate_frame(ds, frame)
        self.reports[dataset_id] = report
        log.info(
            "%s: %s rows, %s -> %s, %d warning(s)",
            dataset_id,
            f"{report.rows:,}",
            report.date_min,
            report.date_max,
            len(report.warnings),
        )
        for w in report.warnings:
            log.warning("%s: [%s] %s", dataset_id, w.code, w.message)

        if catalogue_meta:
            try:
                self.metas[dataset_id] = self.fetcher.fetch_catalogue_meta(ds)
            except Exception as exc:  # noqa: BLE001 - metadata is nice-to-have, not load-bearing
                log.warning("%s: could not read catalogue metadata (%s)", dataset_id, exc)

        self.frames[dataset_id] = frame
        return frame

    def _optional(self, dataset_id: str) -> pd.DataFrame | None:
        """Load a dataset if it is reachable, otherwise carry on without it."""
        try:
            return self.load(dataset_id, catalogue_meta=False)
        except Exception as exc:  # noqa: BLE001 - optional context, any failure is survivable
            log.warning("%s unavailable, continuing without it: %s", dataset_id, exc)
            return None

    # --------------------------------------------------------------- pricecatcher

    def build_pricecatcher_aggregate(self) -> pd.DataFrame:
        """Reduce every PriceCatcher edition to item x state x month.

        This is the only genuinely heavy step: ~1.7M raw rows per edition, 58 editions.
        """
        ds = registry.get("pricecatcher")
        items = pc_t.clean_items(self.load("lookup_item", catalogue_meta=False))
        premises = pc_t.clean_premises(self.load("lookup_premise", catalogue_meta=False))

        editions = self.fetcher.editions_for(ds)
        if not editions:
            editions = list(ds.editions)
        editions = sorted(editions)
        if self.config.pricecatcher_limit:
            editions = editions[-self.config.pricecatcher_limit :]
        log.info("PriceCatcher: reducing %d edition(s)", len(editions))

        parts: list[pd.DataFrame] = []
        for i, edition in enumerate(editions, start=1):
            path = self.fetcher.fetch_parquet(ds, edition)
            raw = pd.read_parquet(path)
            part = pc_t.aggregate_edition(raw, items, premises)
            parts.append(part)
            log.info(
                "PriceCatcher %s (%d/%d): %s raw rows -> %s aggregates",
                edition,
                i,
                len(editions),
                f"{len(raw):,}",
                f"{len(part):,}",
            )
            del raw

        if not parts:
            return pd.DataFrame()

        combined = pd.concat(parts, ignore_index=True)
        combined = combined.sort_values(["item_code", "state", "month"]).reset_index(drop=True)

        # The aggregate is not covered by a registry contract (it is our own construct), so
        # assert its core invariants directly.
        assert combined["month"].notna().all(), "PriceCatcher aggregate has null months"
        assert (combined["n_obs"] > 0).all(), "PriceCatcher aggregate has zero-observation rows"
        return combined

    # ------------------------------------------------------------------- running

    def run(self) -> RunResult:
        cfg = self.config
        started = artifacts.now_utc()
        writer = ArtifactWriter(cfg.out_dir)
        self.reports = {}
        self.metas = {}
        self.frames = {}

        if cfg.only_pricecatcher:
            log.info("Running PriceCatcher stage only")
            self._build_item_level(writer)
        else:
            self._build_core(writer)
            if cfg.skip_pricecatcher:
                log.info("Skipping PriceCatcher stage (--skip-pricecatcher)")
            else:
                self._build_item_level(writer)

        manifest = artifacts.write_manifest(
            writer, reports=self.reports, metas=self.metas, started_at=started
        )
        warnings = [
            f"{ds_id}: [{w.code}] {w.message}"
            for ds_id, rep in self.reports.items()
            for w in rep.warnings
        ]
        return RunResult(
            manifest=manifest,
            reports=self.reports,
            warnings=warnings,
            artifacts=sorted(writer.index),
        )

    def _build_core(self, writer: ArtifactWriter) -> None:
        cpi_headline = self.load("cpi_headline")
        cpi_state = self.load("cpi_state")
        cpi_3d = self.load("cpi_3d")
        cpi_strata = self.load("cpi_strata")
        mcoicop = self.load("mcoicop")
        wages_state = self.load("wages_state")
        published_real = self.load("wages_real_state_citizen")
        hh_income_state = self.load("hh_income_state")
        hh_income = self.load("hh_income")

        # Build everything first, validate, and only then write. Nothing is published until
        # every check has passed -- otherwise a failed run leaves a half-updated data/ behind
        # and the site silently serves a mix of old and new numbers.
        wages_artifact = artifacts.build_real_wages(
            wages_state,
            cpi_state,
            published_real,
            self.metas.get("wages_state"),
            self.metas.get("wages_real_state_citizen"),
        )
        income_artifact = artifacts.build_household_income(
            hh_income_state, hh_income, cpi_state, cpi_headline, self.metas.get("hh_income_state")
        )
        wages_summary = pd.DataFrame(wages_artifact["summary"])
        income_summary = pd.DataFrame(income_artifact["summary"])
        regional_artifact = artifacts.build_regional(cpi_state, wages_summary, income_summary)

        # --- the gate -------------------------------------------------------------
        validation = wages_artifact["meta"]["validation_against_dosm"]
        if not validation["passed"]:
            raise ContractError(
                "Real-wage validation against DOSM's published series FAILED: "
                f"max|d_median|={validation['max_abs_diff_median']} RM "
                f"(tolerance {validation['tolerance_rm']} RM). Either our deflation is wrong "
                "or upstream changed the base year. Nothing was written."
            )
        log.info("Real-wage validation passed: %s", validation)

        # --- publish --------------------------------------------------------------
        writer.write_json(
            "cpi_national.json",
            artifacts.build_cpi_national(cpi_headline, mcoicop, self.metas.get("cpi_headline")),
        )
        writer.write_json(
            "cpi_state.json",
            artifacts.build_cpi_state(cpi_state, mcoicop, self.metas.get("cpi_state")),
        )
        writer.write_json(
            "cpi_groups.json",
            artifacts.build_cpi_groups(cpi_3d, mcoicop, self.metas.get("cpi_3d")),
        )
        writer.write_json(
            "cpi_strata.json",
            artifacts.build_cpi_strata(cpi_strata, self.metas.get("cpi_strata")),
        )
        writer.write_json("real_wages.json", wages_artifact)
        writer.write_json("household_income.json", income_artifact)
        writer.write_json("regional.json", regional_artifact)

        self._build_analysis(writer, cpi_headline)

    def _build_analysis(self, writer: ArtifactWriter, cpi_headline: pd.DataFrame) -> None:
        """Forecasting and anomaly detection.

        Both run over the national headline series and every CPI division. Anomaly detection
        is cheap; forecasting is not, so it can be skipped or truncated.
        """
        mcoicop = self.frames.get("mcoicop")
        names = cpi_t.division_names(mcoicop) if mcoicop is not None else {}
        series = _division_series(cpi_headline, names)
        if not series:
            log.warning("no CPI series available; skipping analysis stage")
            return

        chosen = (
            series if self.config.forecast_limit is None else series[: self.config.forecast_limit]
        )

        # --- anomalies (fast) -----------------------------------------------------
        # Corroborating series are optional: if either is unavailable the anomaly flags are
        # still valid, they just carry less context. Failing the whole run over a nice-to-have
        # would be the wrong trade.
        fuelprice = self._optional("fuelprice")
        lfs_month = self._optional("lfs_month")

        reports = []
        for name, label, values in series:
            try:
                reports.append(
                    anomaly_t.build_report(
                        values,
                        series_id=name,
                        label=label,
                        fuelprice=fuelprice,
                        lfs_month=lfs_month,
                    )
                )
            except ValueError as exc:
                log.warning("anomaly detection skipped for %s: %s", name, exc)
        if reports:
            writer.write_json("anomalies.json", artifacts.build_anomalies(reports, self.metas))

        # --- forecasts (slow) -----------------------------------------------------
        if self.config.skip_forecast:
            log.info("Skipping forecast stage (--skip-forecast)")
            return

        results = []
        for name, label, values in chosen:
            try:
                results.append(
                    forecast_t.forecast_series(
                        values,
                        series_id=name,
                        label=label,
                        horizon=FORECAST_HORIZON,
                        n_folds=self.config.forecast_folds,
                    )
                )
            except ValueError as exc:
                log.warning("forecast skipped for %s: %s", name, exc)
            else:
                last = results[-1]
                log.info(
                    "forecast %s: %s, %s",
                    name,
                    last.method,
                    "beats baseline" if last.beats_baseline else "DOES NOT beat baseline",
                )
        if results:
            writer.write_json("forecast.json", artifacts.build_forecast(results, self.metas))

    def _build_item_level(self, writer: ArtifactWriter) -> None:
        aggregate = self.build_pricecatcher_aggregate()
        if aggregate.empty:
            log.warning("PriceCatcher produced no aggregates; skipping item artifacts")
            return
        items = pc_t.clean_items(self.load("lookup_item", catalogue_meta=False))
        writer.write_parquet("pricecatcher/aggregate.parquet", aggregate)
        writer.write_json("pricecatcher/items.json", artifacts.build_pricecatcher(aggregate, items))

    def close(self) -> None:
        # Only close a fetcher we created. A caller who injected one owns its lifecycle.
        if self._owns_fetcher:
            self.fetcher.close()

    def __enter__(self) -> Runner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def run_pipeline(config: RunConfig) -> RunResult:
    with Runner(config) as runner:
        return runner.run()
