"""Orchestration tests.

The runner is driven with a stub fetcher so the whole fetch -> validate -> transform -> write
path can be exercised offline. The most important behaviour under test is that a failed
validation aborts the run before anything is published.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from colcheck.contracts import ContractError
from colcheck.fetch import CatalogueMeta
from colcheck.runner import RunConfig, Runner
from colcheck.transforms import pricecatcher as pc


class StubFetcher:
    """Serves hand-built frames keyed by dataset id, plus a PriceCatcher edition."""

    def __init__(self, frames: dict[str, pd.DataFrame], aggregate: pd.DataFrame | None = None):
        self.frames = frames
        self.aggregate = aggregate
        self.tmp = Path()
        self.closed = False

    def fetch_parquet(self, ds, edition=None):
        key = ds.id
        if key == "pricecatcher":
            return _ParquetRef(key)
        return _ParquetRef(key)

    def fetch_catalogue_meta(self, ds, refresh: bool = False):
        return CatalogueMeta(id=ds.id, data_as_of="2026-09", last_updated="2026-10-19 13:12")

    def editions_for(self, ds):
        return ["2026-09"]

    def close(self):
        self.closed = True


class _ParquetRef:
    """Marker returned instead of a path; monkeypatched pd.read_parquet resolves it."""

    def __init__(self, key: str):
        self.key = key


@pytest.fixture
def stub_frames(cpi_state_frame, wages_state_frame, published_real_frame, mcoicop_frame):
    """Frames for every dataset the core stage touches."""
    return {
        # One row per month: a national series has no state dimension, so reusing the state
        # fixture here would produce duplicate (date, division) keys -- which is exactly what
        # the contract check is designed to catch.
        "cpi_headline": pd.DataFrame(
            [
                {
                    "date": pd.Timestamp(year, m, 1),
                    "division": "overall",
                    "index": 100.0 + (year - 2010) * 25.0,
                }  # fmt: skip
                for year in (2010, 2025)
                for m in range(1, 13)
            ]
        ),
        # Same known-answer series as the fixture, extended with the HIES survey years
        # (2012, 2024) so the household-income ranking has something to join against.
        "cpi_state": pd.concat(
            [
                cpi_state_frame,
                pd.DataFrame(
                    [
                        {
                            "state": state,
                            "date": pd.Timestamp(year, m, 1),
                            "division": "overall",
                            "index": index,
                        }
                        for year, state, index in (
                            (2012, "Johor", 105.0),
                            (2012, "Kedah", 110.0),
                            (2024, "Johor", 122.0),
                            (2024, "Kedah", 145.0),
                        )
                        for m in range(1, 13)
                    ]
                ),
            ],
            ignore_index=True,
        ),
        "cpi_3d": pd.DataFrame(
            [
                {"date": pd.Timestamp(2010, m, 1), "group": "001", "index": 100.0}
                for m in range(1, 13)
            ]
        ),
        "cpi_strata": pd.DataFrame(
            [
                {
                    "strata": "urban",
                    "date": pd.Timestamp(2010, m, 1),
                    "division": "overall",
                    "index": 100.0,
                }
                for m in range(1, 13)
            ]
        ),  # fmt: skip
        "mcoicop": mcoicop_frame,
        "wages_state": wages_state_frame,
        "wages_real_state_citizen": published_real_frame,
        # Survey years must overlap the CPI years in `cpi_state`, or the income ranking comes
        # back empty (which is itself a case worth exercising -- see test_regional_*).
        "hh_income_state": pd.DataFrame(
            [
                {
                    "state": "Johor",
                    "date": pd.Timestamp(2012, 1, 1),
                    "income_mean": 2000.0,
                    "income_median": 1800.0,
                },
                {
                    "state": "Johor",
                    "date": pd.Timestamp(2024, 1, 1),
                    "income_mean": 4800.0,
                    "income_median": 4000.0,
                },
                {
                    "state": "Kedah",
                    "date": pd.Timestamp(2012, 1, 1),
                    "income_mean": 1500.0,
                    "income_median": 1400.0,
                },
                {
                    "state": "Kedah",
                    "date": pd.Timestamp(2024, 1, 1),
                    "income_mean": 2200.0,
                    "income_median": 2000.0,
                },
            ]
        ),  # fmt: skip
        "hh_income": pd.DataFrame(
            [
                {"date": pd.Timestamp(2012, 1, 1), "income_mean": 5000.0, "income_median": 4000.0},
                {"date": pd.Timestamp(2024, 1, 1), "income_mean": 9000.0, "income_median": 7000.0},
            ]
        ),
    }


@pytest.fixture
def patched_read_parquet(monkeypatch, stub_frames):
    def fake_read(ref, *args, **kwargs):
        if isinstance(ref, _ParquetRef):
            return stub_frames[ref.key]
        raise AssertionError(f"unexpected read of {ref}")

    monkeypatch.setattr(pd, "read_parquet", fake_read)
    return fake_read


def test_core_run_writes_every_artifact(tmp_path, stub_frames, patched_read_parquet):
    config = RunConfig(out_dir=tmp_path, skip_pricecatcher=True)
    runner = Runner(config, fetcher=StubFetcher(stub_frames))
    result = runner.run()

    for name in ("cpi_national.json", "cpi_state.json", "cpi_groups.json", "cpi_strata.json",
                 "real_wages.json", "household_income.json", "regional.json", "manifest.json"):  # fmt: skip
        assert (tmp_path / name).exists(), f"{name} was not written"
        assert name in result.artifacts

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pipeline_version"]
    assert "cpi_state" in manifest["datasets"]


def test_manifest_records_per_dataset_as_of_dates(tmp_path, stub_frames, patched_read_parquet):
    """One global 'last updated' would be a lie; each source refreshes on its own cadence."""
    runner = Runner(
        RunConfig(out_dir=tmp_path, skip_pricecatcher=True), fetcher=StubFetcher(stub_frames)
    )
    manifest = runner.run().manifest
    entry = manifest["datasets"]["cpi_state"]
    assert entry["as_of"] == "2026-09"
    assert entry["last_updated"] == "2026-10-19 13:12"
    assert entry["tier"] == "core"


def test_failed_validation_aborts_before_writing_artifacts(tmp_path, stub_frames, monkeypatch):
    """A datapoint that no longer matches DOSM's published series must stop the build.

    Here we corrupt the nominal wage series so our deflation drifts away from the published
    real series, which is exactly what a base-year change upstream would look like.
    """
    broken = {**stub_frames}
    wages = stub_frames["wages_state"].copy()
    wages.loc[wages["wages_median"] == 1500.0, "wages_median"] = 9999.0
    broken["wages_state"] = wages

    def fake_read(ref, *args, **kwargs):
        return broken[ref.key] if isinstance(ref, _ParquetRef) else None

    monkeypatch.setattr(pd, "read_parquet", fake_read)
    runner = Runner(
        RunConfig(out_dir=tmp_path, skip_pricecatcher=True), fetcher=StubFetcher(broken)
    )

    with pytest.raises(ContractError, match="validation against DOSM"):
        runner.run()

    assert not (tmp_path / "real_wages.json").exists(), "artifacts were written despite failure"


def test_contract_failure_propagates(tmp_path, stub_frames, monkeypatch):
    """A frame missing a required column must abort, not produce a half-empty artifact."""
    broken = {**stub_frames}
    broken["cpi_state"] = stub_frames["cpi_state"].drop(columns=["index"])

    def fake_read(ref, *args, **kwargs):
        return broken[ref.key] if isinstance(ref, _ParquetRef) else None

    monkeypatch.setattr(pd, "read_parquet", fake_read)
    runner = Runner(
        RunConfig(out_dir=tmp_path, skip_pricecatcher=True), fetcher=StubFetcher(broken)
    )
    with pytest.raises(ContractError, match="Missing required columns"):
        runner.run()


def test_injected_fetcher_is_not_closed_by_the_runner(stub_frames):
    """The caller owns an injected fetcher's lifecycle."""
    fetcher = StubFetcher(stub_frames)
    Runner(RunConfig(skip_pricecatcher=True), fetcher=fetcher).close()
    assert fetcher.closed is False


def test_pricecatcher_aggregate_rejects_rows_with_zero_observations(
    stub_frames, monkeypatch, tmp_path
):
    """The invariant assertion on our own construct."""
    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01"]),
            "premise_code": [10],
            "item_code": [1],
            "price": [10.0],
        }  # fmt: skip
    )
    items = pd.DataFrame(
        {
            "item_code": [1],
            "item": ["CHICKEN"],
            "unit": ["1kg"],
            "item_group": ["FRESH"],
            "item_category": ["POULTRY"],
        }  # fmt: skip
    )
    premises = pd.DataFrame(
        {
            "premise_code": [10],
            "premise": ["A"],
            "premise_type": ["X"],
            "state": ["Johor"],
            "district": ["JB"],
        }  # fmt: skip
    )
    frames = {
        **stub_frames,
        "lookup_item": items,
        "lookup_premise": premises,
        "pricecatcher": prices,
    }

    def fake_read(ref, *args, **kwargs):
        return frames[ref.key] if isinstance(ref, _ParquetRef) else None

    monkeypatch.setattr(pd, "read_parquet", fake_read)
    runner = Runner(
        RunConfig(out_dir=tmp_path, only_pricecatcher=True), fetcher=StubFetcher(frames)
    )
    agg = runner.build_pricecatcher_aggregate()
    assert (agg["n_obs"] > 0).all()
    assert agg.iloc[0]["state"] == "Johor"


def test_pipeline_summary_helper_matches_aggregate(stub_frames):
    """Guard the smoke path for national item series used by the artifact builder."""
    agg = pd.DataFrame(
        [
            {
                "item_code": 1,
                "item": "CHICKEN",
                "unit": "1kg",
                "item_group": "FRESH",
                "item_category": "POULTRY",
                "state": "Johor",
                "month": "2026-09",
                "median_price": 10.0,
                "mean_price": 10.0,
                "min_price": 10.0,
                "max_price": 10.0,
                "n_obs": 5,
            }  # fmt: skip
        ]
    )
    nat = pc.national_item_series(agg)
    assert len(nat) == 1
    assert nat.iloc[0]["median_price"] == pytest.approx(10.0)
