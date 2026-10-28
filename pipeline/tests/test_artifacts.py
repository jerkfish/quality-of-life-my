"""Artifact builder and writer tests.

The site reads these files and nothing else, so their shape is a contract with the frontend.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from colcheck.artifacts import (
    ArtifactWriter,
    build_cpi_groups,
    build_cpi_national,
    build_real_wages,
    build_regional,
    write_manifest,
)
from colcheck.contracts import DatasetReport


@pytest.fixture
def writer(tmp_path) -> ArtifactWriter:
    return ArtifactWriter(tmp_path)


def test_writer_records_an_index_with_hashes(writer):
    writer.write_json("thing.json", {"a": 1})
    entry = writer.index["thing.json"]
    assert entry["bytes"] > 0
    assert len(entry["sha256"]) == 64


def test_write_json_refuses_non_standard_json_tokens(writer):
    """NaN is not valid JSON. Emitting it would break every strict parser downstream."""
    with pytest.raises(ValueError):
        writer.write_json("bad.json", {"x": float("nan")})


def test_cpi_national_artifact_shape(writer, cpi_national_frame, mcoicop_frame):
    payload = build_cpi_national(cpi_national_frame, mcoicop_frame, None)
    assert set(payload) == {"meta", "periods", "series"}
    assert payload["meta"]["dataset"] == "cpi_headline"
    assert payload["meta"]["base_year"] == 2010
    assert payload["periods"][0] == "2024-01"
    assert len(payload["series"]["01"]) == len(payload["periods"])
    assert payload["meta"]["division_names"]["01"]["en"] == "Food & Beverages"


def test_cpi_national_series_arrays_align_with_periods(cpi_national_frame, mcoicop_frame):
    payload = build_cpi_national(cpi_national_frame, mcoicop_frame, None)
    n = len(payload["periods"])
    assert all(len(v) == n for v in payload["series"].values())


def test_cpi_groups_without_the_base_year_reports_no_cumulative_change(mcoicop_frame):
    """Base year 2010 absent from the series: report nothing rather than fabricate."""
    months = pd.date_range("2024-01-01", periods=12, freq="MS")
    frame = pd.DataFrame([{"date": d, "group": "001", "index": 100.0} for d in months])
    payload = build_cpi_groups(frame, mcoicop_frame, None)
    assert payload["meta"]["dataset"] == "cpi_3d"
    assert payload["meta"]["cumulative_from_base_pct"] == {}


def test_cpi_groups_cumulative_uses_only_observed_years(mcoicop_frame):
    """Base year present: cumulative should be computable and correct."""
    months = pd.date_range("2010-01-01", periods=12, freq="MS")
    frame = pd.DataFrame([{"date": d, "group": "001", "index": 100.0} for d in months])
    later = pd.DataFrame(
        [{"date": d, "group": "001", "index": 150.0} for d in months + pd.DateOffset(years=1)]
    )
    payload = build_cpi_groups(pd.concat([frame, later], ignore_index=True), mcoicop_frame, None)
    assert payload["meta"]["cumulative_from_base_pct"]["001"] == pytest.approx(50.0)


def test_real_wages_artifact_includes_the_validation_block(
    cpi_state_frame, wages_state_frame, published_real_frame
):
    payload = build_real_wages(wages_state_frame, cpi_state_frame, published_real_frame, None, None)
    validation = payload["meta"]["validation_against_dosm"]
    assert validation["passed"] is True
    assert validation["n_compared"] == 2
    assert payload["meta"]["population"] == "employees who are Malaysian citizens"
    assert len(payload["summary"]) == 2


def test_real_wages_artifact_states_the_caveats(
    cpi_state_frame, wages_state_frame, published_real_frame
):
    """The composition caveat is not optional; medians move when the population mix moves."""
    payload = build_real_wages(wages_state_frame, cpi_state_frame, published_real_frame, None, None)
    joined = " ".join(payload["caveats"]).lower()
    assert "self-employed" in joined
    assert "composition" in joined


def test_regional_spreads_compute_both_sides(
    cpi_state_frame, wages_state_frame, published_real_frame
):
    wages_artifact = build_real_wages(
        wages_state_frame, cpi_state_frame, published_real_frame, None, None
    )
    income_summary = pd.DataFrame(
        [
            {
                "state": "Johor",
                "cumulative_inflation_pct": 25.0,
                "nominal_growth_pct": 90.0,
                "real_growth_pct": 52.0,
            },
            {
                "state": "Kedah",
                "cumulative_inflation_pct": 50.0,
                "nominal_growth_pct": 60.0,
                "real_growth_pct": 6.6,
            },
        ]
    )
    payload = build_regional(
        cpi_state_frame, pd.DataFrame(wages_artifact["summary"]), income_summary
    )

    assert payload["spreads"]["wages"]["real_growth"]["max_state"] == "Johor"
    assert payload["spreads"]["wages"]["real_growth"]["min_state"] == "Kedah"
    assert payload["spreads"]["wages"]["real_growth"]["spread_pp"] == pytest.approx(40.0)
    assert payload["spreads"]["income"]["real_growth"]["spread_pp"] == pytest.approx(45.4)


def test_manifest_merges_with_a_previous_run(tmp_path):
    """A partial run (`--only-pricecatcher`) must not erase the other stage's provenance."""
    writer = ArtifactWriter(tmp_path)
    write_manifest(
        writer,
        reports={"cpi_headline": DatasetReport(dataset_id="cpi_headline", rows=10)},
        metas={},
        started_at=pd.Timestamp("2026-01-01").to_pydatetime(),
    )
    first = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert "cpi_headline" in first["datasets"]

    writer2 = ArtifactWriter(tmp_path)
    merged = write_manifest(
        writer2,
        reports={"pricecatcher": DatasetReport(dataset_id="pricecatcher", rows=5)},
        metas={},
        started_at=pd.Timestamp("2026-01-02").to_pydatetime(),
    )
    assert set(merged["datasets"]) == {"cpi_headline", "pricecatcher"}


def test_manifest_drops_artifacts_that_no_longer_exist(tmp_path):
    writer = ArtifactWriter(tmp_path)
    writer.write_json("gone.json", {"a": 1})
    write_manifest(
        writer, reports={}, metas={}, started_at=pd.Timestamp("2026-01-01").to_pydatetime()
    )
    (tmp_path / "gone.json").unlink()

    writer2 = ArtifactWriter(tmp_path)
    merged = write_manifest(
        writer2, reports={}, metas={}, started_at=pd.Timestamp("2026-01-02").to_pydatetime()
    )
    assert "gone.json" not in merged["artifacts"]
