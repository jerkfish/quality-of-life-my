"""Live upstream contract tests.

These are the tests that make the "fail loudly if the upstream schema changes" requirement
real. They reach the network, so they are marked `network` and skipped by default in local
runs; CI runs them on a schedule.

Two of them guard assumptions the entire project rests on:

*   the CPI base year is still 2010, and
*   our deflation still reproduces DOSM's own published real-wage series.

If either breaks, every real-terms number on the site is wrong. Neither failure would raise
anywhere else.
"""

from __future__ import annotations

import pandas as pd
import pytest

from colcheck import registry
from colcheck.contracts import validate_frame
from colcheck.fetch import Fetcher
from colcheck.transforms import cpi as cpi_t
from colcheck.transforms import wages as wages_t

pytestmark = pytest.mark.network

CORE = (
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


@pytest.fixture(scope="module")
def fetcher(tmp_path_factory):
    with Fetcher(tmp_path_factory.mktemp("raw"), tmp_path_factory.mktemp("meta")) as f:
        yield f


@pytest.fixture(scope="module")
def frames(fetcher) -> dict[str, pd.DataFrame]:
    return {ds_id: pd.read_parquet(fetcher.fetch_parquet(registry.get(ds_id))) for ds_id in CORE}


@pytest.mark.parametrize("dataset_id", CORE)
def test_live_dataset_satisfies_its_contract(fetcher, dataset_id):
    """Upstream shape, columns, domains and keys still match the registry declaration."""
    ds = registry.get(dataset_id)
    frame = pd.read_parquet(fetcher.fetch_parquet(ds))
    report = validate_frame(ds, frame)
    assert report.ok
    assert report.rows > 0


def test_cpi_base_year_is_still_2010(frames):
    """THE assumption. A rebase would silently invalidate every real-terms figure."""
    national = frames["cpi_headline"]
    overall = national[national["division"] == "overall"]
    annual = cpi_t.annual_index(overall).set_index("year")["value"]
    assert annual.loc[2010] == pytest.approx(100.0, abs=0.05), (
        "CPI no longer equals 100 in 2010 -- DOSM has rebased. All real-terms analysis "
        "must be redone against the new base before this pipeline can ship."
    )


def test_state_cpi_also_uses_the_2010_base(frames):
    state = frames["cpi_state"]
    overall = state[state["division"] == "overall"]
    annual = cpi_t.annual_index(overall, entity_cols=("state",))
    at_base = annual[annual["year"] == 2010]
    # Every state should be at 100.0 in the base year.
    assert at_base["value"].max() == pytest.approx(100.0, abs=0.05)
    assert at_base["value"].min() == pytest.approx(100.0, abs=0.05)


def test_our_deflation_reproduces_dosms_published_real_wages(frames):
    """The strongest test in the suite, run against real data.

    We deflate the citizen wage series ourselves and compare with DOSM's own published
    citizen real series. Phase 0 measured a maximum difference of 0.77 RM; the tolerance is
    2 RM to absorb rounding in the published figures.
    """
    real = wages_t.real_wages_by_state(frames["wages_state"], frames["cpi_state"])
    latest_year = int(real["year"].max())
    result = wages_t.validate_against_dosm(
        real, frames["wages_real_state_citizen"], year=latest_year
    )
    assert result.n_compared == len(registry.STATES)
    assert result.passed, f"Deflation drifted from DOSM: {result.summary}"


def test_state_wage_series_still_starts_at_the_cpi_base_year(frames):
    """If wages start before 2010 we would need to rebase; if after, we lose the base."""
    wages = frames["wages_state"]
    years = pd.to_datetime(wages["date"]).dt.year
    assert years.min() == cpi_t.CPI_BASE_YEAR


def test_wages_state_still_covers_citizens_only(frames):
    """If upstream widened this to all employees, our citizen-vs-citizen check breaks.

    A crude but effective canary: the all-employee median is materially lower in Sabah,
    so if `wages_state` suddenly matches `wages_real_state` rather than
    `wages_real_state_citizen`, the population changed.
    """
    wages = frames["wages_state"]
    malaysia = wages[(wages["sex"] == "both") & (wages["state"] == "Malaysia")]
    assert not malaysia.empty, "wages_state no longer carries a Malaysia aggregate row"

    latest = pd.Timestamp(malaysia["date"].max())
    published_citizen = frames["wages_real_state_citizen"]
    citizen_latest = pd.Timestamp(pd.to_datetime(published_citizen["date"]).max())
    assert latest == citizen_latest, "wage and published-real series are out of step"


def test_recorded_states_are_exactly_the_ones_upstream_publishes(frames):
    """The registry's `allow_national` flag must match reality, not the other way round.

    This is what the flag is for: cpi_state has no Malaysia row, wages_state does, and
    hh_income_state does not. Divergence here means a join somewhere is silently dropping
    or inventing a national row.
    """
    for ds_id in ("cpi_state", "wages_state", "hh_income_state"):
        ds = registry.get(ds_id)
        found = set(frames[ds_id]["state"].dropna().unique())
        expected = set(registry.STATES) | ({registry.MALAYSIA} if ds.allow_national else set())
        assert found == expected, (
            f"{ds_id}: state set drifted: {found ^ expected}; allow_national={ds.allow_national}"
        )


def test_cpi_divisions_are_still_01_to_13_plus_overall(frames):
    found = set(frames["cpi_headline"]["division"].unique())
    assert found == {*registry.CPI_DIVISIONS, registry.CPI_OVERALL}


def test_hies_still_has_gaps_and_we_still_report_them(frames):
    """If HIES ever became annual, the 'no interpolation' guard could be relaxed -- but we
    should notice, not assume."""
    years = sorted(pd.to_datetime(frames["hh_income_state"]["date"]).dt.year.unique())
    gaps = [b - a for a, b in zip(years, years[1:], strict=False)]
    assert max(gaps) > 1, "HIES now appears continuous; revisit the gap-handling assumptions"


def test_catalogue_metadata_is_reachable_and_carries_an_as_of_date(fetcher):
    """The 'data last updated' badge depends on this metadata being present."""
    meta = fetcher.fetch_catalogue_meta(registry.get("cpi_headline"), refresh=True)
    assert meta.id == "cpi_headline"
    assert meta.data_as_of, "no data_as_of; the data-freshness badge would have nothing to show"
    assert meta.last_updated
