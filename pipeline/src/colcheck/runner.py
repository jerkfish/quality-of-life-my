"""Pipeline orchestration: fetch -> validate -> transform -> write artifacts.

Kept separate from the CLI so tests can drive a whole run without spawning a process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import artifacts, registry
from .artifacts import ArtifactWriter
from .contracts import ContractError, DatasetReport, validate_frame
from .fetch import CatalogueMeta, Fetcher
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


@dataclass
class RunResult:
    manifest: dict
    reports: dict[str, DatasetReport] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.fetcher = Fetcher(config.raw_dir, config.meta_dir, offline=config.offline)
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

        wages_artifact = artifacts.build_real_wages(
            wages_state,
            cpi_state,
            published_real,
            self.metas.get("wages_state"),
            self.metas.get("wages_real_state_citizen"),
        )
        writer.write_json("real_wages.json", wages_artifact)

        income_artifact = artifacts.build_household_income(
            hh_income_state, hh_income, cpi_state, cpi_headline, self.metas.get("hh_income_state")
        )
        writer.write_json("household_income.json", income_artifact)

        wages_summary = pd.DataFrame(wages_artifact["summary"])
        income_summary = pd.DataFrame(income_artifact["summary"])
        writer.write_json(
            "regional.json", artifacts.build_regional(cpi_state, wages_summary, income_summary)
        )

        validation = wages_artifact["meta"]["validation_against_dosm"]
        if not validation["passed"]:
            raise ContractError(
                "Real-wage validation against DOSM's published series FAILED: "
                f"max|d_median|={validation['max_abs_diff_median']} RM "
                f"(tolerance {validation['tolerance_rm']} RM). Either our deflation is wrong "
                "or upstream changed the base year. Not shipping artifacts."
            )
        log.info("Real-wage validation passed: %s", validation)

    def _build_item_level(self, writer: ArtifactWriter) -> None:
        aggregate = self.build_pricecatcher_aggregate()
        if aggregate.empty:
            log.warning("PriceCatcher produced no aggregates; skipping item artifacts")
            return
        items = pc_t.clean_items(self.load("lookup_item", catalogue_meta=False))
        writer.write_parquet("pricecatcher/aggregate.parquet", aggregate)
        writer.write_json("pricecatcher/items.json", artifacts.build_pricecatcher(aggregate, items))

    def close(self) -> None:
        self.fetcher.close()

    def __enter__(self) -> Runner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def run_pipeline(config: RunConfig) -> RunResult:
    with Runner(config) as runner:
        return runner.run()
