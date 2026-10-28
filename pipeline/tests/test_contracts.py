"""Contract tests: the tripwire that turns silent wrongness into loud failure.

Each test here corresponds to a way upstream could change underneath us and corrupt the site
without raising anything anywhere.
"""

from __future__ import annotations

import pandas as pd
import pytest

from colcheck import registry
from colcheck.contracts import ContractError, validate_frame
from colcheck.registry import STATES


@pytest.fixture
def cpi_state_ds():
    return registry.get("cpi_state")


@pytest.fixture
def good_cpi_state() -> pd.DataFrame:
    rows = []
    for state in STATES:
        for month in (1, 2):
            rows.append(
                {
                    "state": state,
                    "date": pd.Timestamp(2026, month, 1),
                    "division": "overall",
                    "index": 130.0,
                }
            )
    return pd.DataFrame(rows)


def test_valid_frame_passes(cpi_state_ds, good_cpi_state):
    report = validate_frame(cpi_state_ds, good_cpi_state)
    assert report.ok
    assert report.rows == len(good_cpi_state)
    assert report.date_min == "2026-01-01"
    assert report.date_max == "2026-02-01"
    assert report.entities["state"] == 16


def test_missing_column_fails_loudly(cpi_state_ds, good_cpi_state):
    broken = good_cpi_state.drop(columns=["index"])
    with pytest.raises(ContractError, match="Missing required columns"):
        validate_frame(cpi_state_ds, broken)


def test_empty_frame_fails(cpi_state_ds, good_cpi_state):
    with pytest.raises(ContractError, match="zero rows"):
        validate_frame(cpi_state_ds, good_cpi_state.iloc[0:0])


def test_duplicate_keys_fail(cpi_state_ds, good_cpi_state):
    broken = pd.concat([good_cpi_state, good_cpi_state.head(1)], ignore_index=True)
    with pytest.raises(ContractError, match="duplicate row"):
        validate_frame(cpi_state_ds, broken)


def test_unparsable_dates_fail(cpi_state_ds, good_cpi_state):
    # Object dtype so the bad value survives to the validator, the way it would if upstream
    # started emitting a different date format.
    broken = good_cpi_state.copy()
    broken["date"] = broken["date"].astype(object)
    broken.loc[0, "date"] = "not a date"
    with pytest.raises(ContractError, match="unparsable"):
        validate_frame(cpi_state_ds, broken)


def test_unknown_state_fails(cpi_state_ds, good_cpi_state):
    """A new upstream spelling would silently drop out of every join. Fail instead."""
    broken = good_cpi_state.copy()
    broken.loc[0, "state"] = "Pulau Pinang "  # trailing space
    with pytest.raises(ContractError, match="Unexpected state"):
        validate_frame(cpi_state_ds, broken)


def test_national_row_is_rejected_where_it_does_not_belong(cpi_state_ds, good_cpi_state):
    """cpi_state has no 'Malaysia' row. If upstream adds one, we want to know."""
    broken = good_cpi_state.copy()
    broken.loc[0, "state"] = "Malaysia"
    with pytest.raises(ContractError, match="Unexpected state"):
        validate_frame(cpi_state_ds, broken)


def test_national_row_is_allowed_where_it_does_belong(good_cpi_state):
    """wages_state DOES carry a Malaysia row; the same frame must pass there."""
    ds = registry.get("wages_state")
    frame = good_cpi_state.assign(
        sex="both", wages_median=3000.0, wages_mean=3600.0, recipients=100
    )
    # Add a Malaysia row alongside the states rather than replacing one, which is how
    # upstream actually shapes it.
    malaysia = frame.head(2).copy()
    malaysia["state"] = "Malaysia"
    malaysia["date"] = pd.to_datetime(["2023-01-01", "2023-02-01"])
    report = validate_frame(ds, pd.concat([frame, malaysia], ignore_index=True))
    assert report.ok


def test_missing_state_is_a_warning_not_an_error(cpi_state_ds, good_cpi_state):
    """A state vanishing would change every ranking -- surface it, don't crash the build."""
    partial = good_cpi_state[good_cpi_state["state"] != "Perlis"]
    report = validate_frame(cpi_state_ds, partial)
    assert report.ok
    codes = {w.code for w in report.warnings}
    assert "missing_states" in codes


def test_unexpected_cpi_division_fails(cpi_state_ds, good_cpi_state):
    broken = good_cpi_state.copy()
    broken.loc[0, "division"] = "14"  # beyond the 13 COICOP divisions
    with pytest.raises(ContractError, match="Unexpected CPI division"):
        validate_frame(cpi_state_ds, broken)


def test_overall_division_is_accepted(cpi_state_ds, good_cpi_state):
    assert validate_frame(cpi_state_ds, good_cpi_state).ok


def test_non_numeric_value_fails(cpi_state_ds, good_cpi_state):
    broken = good_cpi_state.copy()
    broken["index"] = broken["index"].astype(object)
    broken.loc[0, "index"] = "one hundred and thirty"
    with pytest.raises(ContractError, match="non-numeric"):
        validate_frame(cpi_state_ds, broken)


def test_null_values_warn_and_are_carried_through(cpi_state_ds, good_cpi_state):
    """Upstream gaps are real (inflation_yoy is null in the first year). Warn, never fill."""
    frame = good_cpi_state.copy()
    frame.loc[0, "index"] = None
    report = validate_frame(cpi_state_ds, frame)
    assert report.ok
    assert report.null_counts["index"] == 1
    assert any(w.code == "null_values" for w in report.warnings)


def test_null_in_a_key_column_is_an_error(cpi_state_ds, good_cpi_state):
    frame = good_cpi_state.copy()
    frame.loc[0, "state"] = None
    with pytest.raises(ContractError, match="null value"):
        validate_frame(cpi_state_ds, frame)


def test_lookup_premise_tolerates_its_sentinel_null_state():
    """lookup_premise has one row with no state. It must warn, not fail."""
    ds = registry.get("lookup_premise")
    frame = pd.DataFrame(
        {
            "premise_code": [1, 2],
            "premise": ["A", "B"],
            "premise_type": ["Pasar Mini", None],
            "state": ["Johor", None],
            "district": ["JB", None],
        }
    )
    report = validate_frame(ds, frame)
    assert report.ok
    assert report.null_counts.get("state") == 1
    assert any(w.code == "null_entity" for w in report.warnings)


def test_lookup_premise_still_requires_unique_premise_codes():
    ds = registry.get("lookup_premise")
    frame = pd.DataFrame(
        {
            "premise_code": [1, 1],
            "premise": ["A", "B"],
            "premise_type": ["Pasar Mini", "Hypermarket"],
            "state": ["Johor", "Kedah"],
            "district": ["JB", "AS"],
        }
    )
    with pytest.raises(ContractError, match="duplicate row"):
        validate_frame(ds, frame)


def test_registry_declares_a_url_or_editions_for_every_fetchable_dataset():
    for ds in registry.DATASETS.values():
        if ds.id.startswith("lookup") or ds.id == "pricecatcher":
            continue
        assert ds.url, f"{ds.id} has no URL and is not declared editioned"


def test_every_dataset_declares_a_tier_and_frequency():
    for ds in registry.DATASETS.values():
        assert ds.tier is not None
        assert ds.frequency is not None


def test_state_list_matches_dosm_spelling_exactly():
    """These strings are join keys. A typo silently drops a state from every analysis."""
    assert len(STATES) == 16
    assert "W.P. Kuala Lumpur" in STATES
    assert "Pulau Pinang" in STATES  # not "Penang"
    assert len(set(STATES)) == 16
