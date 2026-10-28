"""The dataset registry.

Every upstream dataset the project uses, declared once. The URLs and field lists here were
read off the live catalogue pages rather than typed from memory -- see docs/DATA.md.

Two things worth knowing before editing this file:

1. Bulk parquet from storage.dosm.gov.my / storage.data.gov.my is the transport, not the
   REST API. The API is rate limited to 4 requests/minute and is missing several datasets
   entirely (cpi_3d/4d/5d, pricecatcher, lookup_item, lookup_premise all 404 on it).
2. `tier` records *why* a dataset is in the project, so a reviewer can tell the load-bearing
   data from the supporting context.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    IRREGULAR = "irregular"  # survey years, lookup tables


class Tier(StrEnum):
    CORE = "core"  # drives a headline analysis
    SUPPORT = "support"  # context for a core analysis
    ITEM = "item"  # PriceCatcher item-level panel


class Dataset(BaseModel):
    """Declaration of one upstream dataset."""

    id: str
    title: str
    tier: Tier
    frequency: Frequency
    url: str | None = Field(
        default=None, description="Static parquet URL. None means the dataset is not fetched."
    )
    date_col: str | None = None
    # Categorical columns that identify a series. These are the join keys.
    entity_cols: tuple[str, ...] = ()
    # Numeric columns we rely on.
    value_cols: tuple[str, ...] = ()
    # If set, the dataset has one file per edition (e.g. PriceCatcher monthly parquet).
    editions: tuple[str, ...] = ()
    # True if the dataset carries an extra nationwide row alongside the 16 states.
    # wages_state and formal_wages_state do; cpi_state does NOT. Getting this wrong makes
    # an inner join silently drop the national compare. See docs/DATA.md section 5.
    allow_national: bool = False
    # Columns that must uniquely identify a row. Defaults to date + entity columns.
    # Lookup tables need this spelled out: `lookup_premise` has a legitimate null `state`
    # on its sentinel row, and treating every attribute column as part of the key would
    # both flag that null as corruption and mis-state what uniqueness means here.
    key_cols: tuple[str, ...] | None = None
    notes: str = ""

    @property
    def resolved_key_cols(self) -> tuple[str, ...]:
        if self.key_cols is not None:
            return self.key_cols
        return tuple(c for c in (self.date_col, *self.entity_cols) if c)

    @property
    def is_editioned(self) -> bool:
        return bool(self.editions)


# The 16 states and federal territories, exactly as DOSM spells them. This string set is
# consistent across cpi_state, wages_state, hh_income_state and formal_wages_state --
# verified in Phase 0. Matching is exact; do not "normalise" these.
STATES: tuple[str, ...] = (
    "Johor",
    "Kedah",
    "Kelantan",
    "Melaka",
    "Negeri Sembilan",
    "Pahang",
    "Perak",
    "Perlis",
    "Pulau Pinang",
    "Sabah",
    "Sarawak",
    "Selangor",
    "Terengganu",
    "W.P. Kuala Lumpur",
    "W.P. Labuan",
    "W.P. Putrajaya",
)

# The national aggregate row that wages_state and formal_wages_state carry but cpi_state
# does not. A naive inner join on state silently drops it -- see docs/DATA.md section 5.
MALAYSIA = "Malaysia"

CPI_DIVISIONS: tuple[str, ...] = (
    "01", "02", "03", "04", "05", "06", "07",
    "08", "09", "10", "11", "12", "13",
)  # fmt: skip
CPI_OVERALL = "overall"

_CPI_TIERS = Tier.CORE

# Months for which DOSM publishes a PriceCatcher edition, oldest first. This is the fallback
# list read off the catalogue page's `link_editions` on 2026-10-28; the fetch layer re-reads
# that list live on each run, so new months are picked up without a code change.
PRICECATCHER_EDITIONS: tuple[str, ...] = tuple(
    f"{year}-{month:02d}"
    for year in range(2022, 2027)
    for month in range(1, 13)
    if (year, month) <= (2026, 10)
)

PRICECATCHER_URL = "https://storage.data.gov.my/pricecatcher/pricecatcher_{edition}.parquet"

DATASETS: dict[str, Dataset] = {
    d.id: d
    for d in [
        # ---------------------------------------------------------------- CPI, national
        Dataset(
            id="cpi_headline",
            title="Monthly CPI by Division (2-digit)",
            tier=_CPI_TIERS,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d.parquet",
            date_col="date",
            entity_cols=("division",),
            value_cols=("index",),
            notes="National headline CPI, 1980-01 onwards, base 2010=100. 13 divisions + 'overall'.",
        ),
        Dataset(
            id="cpi_headline_inflation",
            title="Monthly CPI Inflation by Division (2-digit)",
            tier=_CPI_TIERS,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_inflation.parquet",
            date_col="date",
            entity_cols=("division",),
            value_cols=("inflation_yoy", "inflation_mom"),
            notes="Pre-computed yoy/mom inflation. We recompute from the index in transforms "
            "and use this only to cross-check our own arithmetic.",
        ),
        Dataset(
            id="cpi_state",
            title="Monthly CPI by State & Division (2-digit)",
            tier=_CPI_TIERS,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_state.parquet",
            date_col="date",
            entity_cols=("state", "division"),
            value_cols=("index",),
            notes="The backbone of the regional analysis. Starts 2010-01, same base year as "
            "the wage series, so no rebasing is ever needed.",
        ),
        Dataset(
            id="cpi_state_inflation",
            title="Monthly CPI Inflation by State & Division (2-digit)",
            tier=_CPI_TIERS,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_state_inflation.parquet",
            date_col="date",
            entity_cols=("state", "division"),
            value_cols=("inflation_yoy", "inflation_mom"),
        ),
        Dataset(
            id="cpi_3d",
            title="Monthly CPI by Group (3-digit)",
            tier=Tier.CORE,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_3d.parquet",
            date_col="date",
            entity_cols=("group",),
            value_cols=("index",),
            notes="47 groups. Separates 'food away from home' from 'food at home', which is "
            "the finding that makes the personal-inflation calculator interesting. "
            "NOT available over the REST API (404) -- parquet only.",
        ),
        Dataset(
            id="cpi_core",
            title="Monthly Core CPI by Division (2-digit)",
            tier=Tier.SUPPORT,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_core.parquet",
            date_col="date",
            entity_cols=("division",),
            value_cols=("index",),
            notes="Only starts 2018-01. Not usable for a 2010-base comparison.",
        ),
        Dataset(
            id="cpi_strata",
            title="Monthly CPI by Strata & Division (2-digit)",
            tier=Tier.SUPPORT,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_strata.parquet",
            date_col="date",
            entity_cols=("strata", "division"),
            value_cols=("index",),
            notes="urban/rural. Urban inflation ran ~6pp hotter than rural 2010->2025.",
        ),
        Dataset(
            id="cpi_lowincome",
            title="Monthly CPI for Low-Income Households",
            tier=Tier.SUPPORT,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_lowincome.parquet",
            date_col="date",
            entity_cols=("division",),
            value_cols=("index",),
            notes="Households under RM3000/month. Tracks the headline rate almost exactly "
            "over 2010->2025 (+0.4pp). We report that rather than massage it.",
        ),
        Dataset(
            id="mcoicop",
            title="MCOICOP",
            tier=Tier.CORE,
            frequency=Frequency.IRREGULAR,
            key_cols=("digits", "division", "group", "class", "subclass"),
            url="https://storage.dosm.gov.my/dictionaries/mcoicop.parquet",
            entity_cols=("digits", "division", "group", "class", "subclass"),
            value_cols=(),
            notes="COICOP lookup: English and Bahasa Malaysia names for every code level. "
            "Turns '07' into 'Transport'.",
        ),
        # ------------------------------------------------------------------- wages
        Dataset(
            id="wages_state",
            title="Annual Salaries & Wages by State & Sex",
            tier=Tier.CORE,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/labour/wages_state.parquet",
            date_col="date",
            entity_cols=("state", "sex"),
            value_cols=("recipients", "wages_median", "wages_mean"),
            notes="MALAYSIAN CITIZENS ONLY. Not comparable with wages_real_state, which "
            "covers all employees. See docs/DATA.md section 6.",
            allow_national=True,
        ),
        Dataset(
            id="wages_real_state_citizen",
            title="Annual Real Salaries & Wages of Citizens by State",
            tier=Tier.CORE,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/labour/wages_real_state_citizen.parquet",
            date_col="date",
            entity_cols=("state",),
            value_cols=("wages_median_real", "wages_mean_real"),
            notes="DOSM's own 2010-price series. We do not publish this directly; we use it "
            "as the known-answer test for our own deflation.",
            allow_national=True,
        ),
        Dataset(
            id="formal_wages_state",
            title="Monthly Formal Sector Wages by State",
            tier=Tier.CORE,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/labour/formal_wages_state.parquet",
            date_col="date",
            entity_cols=("state",),
            value_cols=("employees", "wages_median"),
            notes="Monthly, but only from 2022-01. Gives the recent, high-frequency view "
            "that the annual series cannot.",
            allow_national=True,
        ),
        Dataset(
            id="formal_wages",
            title="Monthly Formal Sector Wages by Sex, Ethnicity & Age",
            tier=Tier.SUPPORT,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/labour/formal_wages.parquet",
            date_col="date",
            entity_cols=("sex", "ethnicity", "age"),
            value_cols=("employees", "wages_median"),
        ),
        Dataset(
            id="wages_sector",
            title="Annual Salaries & Wages by Sector & Sex",
            tier=Tier.SUPPORT,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/labour/wages_sector.parquet",
            date_col="date",
            entity_cols=("sex", "sector"),
            value_cols=("recipients", "wages_median", "wages_mean"),
        ),
        # ------------------------------------------------- household income (HIES)
        Dataset(
            id="hh_income",
            title="Household Income (Malaysia)",
            tier=Tier.CORE,
            frequency=Frequency.IRREGULAR,
            url="https://storage.dosm.gov.my/hies/hh_income.parquet",
            date_col="date",
            entity_cols=(),
            value_cols=("income_mean", "income_median"),
            notes="The NATIONAL series. hh_income_state has no Malaysia row, so this is the "
            "only source for the national household-income line.",
        ),
        Dataset(
            id="hh_income_state",
            title="Household Income by State (HIES)",
            tier=Tier.CORE,
            frequency=Frequency.IRREGULAR,
            url="https://storage.dosm.gov.my/hies/hh_income_state.parquet",
            date_col="date",
            entity_cols=("state",),
            value_cols=("income_mean", "income_median"),
            notes="Survey years only and irregularly spaced (1970, 1974, 1976, ... 2024). "
            "Charts must show gaps; never interpolate.",
        ),
        Dataset(
            id="hies_state",
            title="Household Income and Expenditure: States",
            tier=Tier.CORE,
            frequency=Frequency.IRREGULAR,
            url="https://storage.dosm.gov.my/hies/hies_state.parquet",
            date_col="date",
            entity_cols=("state",),
            value_cols=("income_mean", "income_median", "expenditure_mean", "gini", "poverty"),
            notes="Only 2022 and 2024 -- a two-point series.",
        ),
        Dataset(
            id="hies_state_percentile",
            title="Household Income by State & Percentile",
            tier=Tier.SUPPORT,
            frequency=Frequency.IRREGULAR,
            url="https://storage.dosm.gov.my/hies/hies_state_percentile.parquet",
            date_col="date",
            entity_cols=("state", "percentile", "variable"),
            value_cols=("income",),
            notes="The income-GROUP dimension, which state averages cannot give.",
        ),
        Dataset(
            id="hh_poverty_state",
            title="Poverty by State",
            tier=Tier.SUPPORT,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/hies/hh_poverty_state.parquet",
            date_col="date",
            entity_cols=("state",),
            value_cols=("poverty_absolute", "poverty_hardcore", "poverty_relative"),
        ),
        Dataset(
            id="hh_inequality_state",
            title="Income Inequality by State",
            tier=Tier.SUPPORT,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/hies/hh_inequality_state.parquet",
            date_col="date",
            entity_cols=("state",),
            value_cols=("gini",),
        ),
        # ------------------------------------------------------------------ labour
        Dataset(
            id="lfs_month",
            title="Monthly Principal Labour Force Statistics",
            tier=Tier.SUPPORT,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/labour/lfs_month.parquet",
            date_col="date",
            entity_cols=(),
            value_cols=("lf", "lf_employed", "lf_unemployed", "u_rate", "p_rate", "ep_ratio"),
            notes="Independent, monthly, and datable -- useful when justifying anomaly "
            "annotations from data rather than narrative.",
        ),
        # -------------------------------------------------------------------- price
        Dataset(
            id="fuelprice",
            title="Price of Petroleum & Diesel",
            tier=Tier.SUPPORT,
            frequency=Frequency.WEEKLY,
            url="https://storage.data.gov.my/commodities/fuelprice.parquet",
            date_col="date",
            entity_cols=("series_type",),
            value_cols=(
                "ron95",
                "ron97",
                "diesel",
                "diesel_eastmsia",
                "ron95_budi95",
                "ron95_skps",
            ),
        ),
        # ------------------------------------------------------- context (population/GDP)
        Dataset(
            id="population_state",
            title="Population Table: States",
            tier=Tier.SUPPORT,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/population/population_state.parquet",
            date_col="date",
            entity_cols=("state", "sex", "age", "ethnicity"),
            value_cols=("population",),
            notes="389 KB with sex x age x ethnicity granularity. Aggregate to state-year "
            "totals during ingest; ship only the aggregate.",
        ),
        Dataset(
            id="gdp_state_real_supply",
            title="Annual Real GDP by State & Economic Sector",
            tier=Tier.SUPPORT,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/gdp/gdp_state_real_supply.parquet",
            date_col="date",
            entity_cols=("state", "sector", "series"),
            value_cols=("value",),
        ),
        # ----------------------------------------- long-run annual CPI and distribution
        Dataset(
            id="cpi_annual",
            title="Annual CPI by Division (2-digit)",
            tier=Tier.SUPPORT,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_annual.parquet",
            date_col="date",
            entity_cols=("division",),
            value_cols=("index",),
            notes="Back to 1960. The long-run view; the monthly series starts 1980.",
        ),
        Dataset(
            id="cpi_annual_inflation",
            title="Annual CPI Inflation by Division (2-digit)",
            tier=Tier.SUPPORT,
            frequency=Frequency.YEARLY,
            url="https://storage.dosm.gov.my/cpi/cpi_2d_annual_inflation.parquet",
            date_col="date",
            entity_cols=("division",),
            value_cols=("inflation",),
        ),
        Dataset(
            id="hies_state_percentile",
            title="Household Income by State & Percentile",
            tier=Tier.SUPPORT,
            frequency=Frequency.IRREGULAR,
            url="https://storage.dosm.gov.my/hies/hies_state_percentile.parquet",
            date_col="date",
            entity_cols=("state", "percentile", "variable"),
            value_cols=("income",),
            notes="The income-GROUP dimension. State averages cannot answer 'who fell behind'.",
        ),
        Dataset(
            id="formal_wages_percentile",
            title="Monthly Formal Sector Wage Percentiles",
            tier=Tier.SUPPORT,
            frequency=Frequency.MONTHLY,
            url="https://storage.dosm.gov.my/labour/formal_wages_percentile.parquet",
            date_col="date",
            entity_cols=(),
            value_cols=(),
            notes="Wide format: one column per percentile (p10 ... p90). Distribution shape "
            "over time, which is how you tell 'everyone gained' from 'the middle gained'.",
        ),
        Dataset(
            id="lfs_qtr_state",
            title="Quarterly Principal Labour Force Statistics by State",
            tier=Tier.SUPPORT,
            frequency=Frequency.QUARTERLY,
            url="https://storage.dosm.gov.my/labour/lfs_qtr_state.parquet",
            date_col="date",
            entity_cols=("state",),
            value_cols=("lf", "lf_employed", "lf_unemployed", "u_rate", "p_rate"),
            notes="State unemployment, quarterly. Context for whether wage growth tracks "
            "tight labour markets.",
        ),
        # --------------------------------------------------------- PriceCatcher (item level)
        Dataset(
            id="lookup_item",
            title="PriceCatcher: Item Lookup",
            tier=Tier.ITEM,
            frequency=Frequency.IRREGULAR,
            key_cols=("item_code",),
            url="https://storage.data.gov.my/pricecatcher/lookup_item.parquet",
            entity_cols=("item_code", "item", "unit", "item_group", "item_category"),
            value_cols=(),
            notes="NOT on the REST API (404).",
        ),
        Dataset(
            id="lookup_premise",
            title="PriceCatcher: Premise Lookup",
            tier=Tier.ITEM,
            frequency=Frequency.IRREGULAR,
            key_cols=("premise_code",),
            url="https://storage.data.gov.my/pricecatcher/lookup_premise.parquet",
            entity_cols=("premise_code", "premise", "premise_type", "state", "district"),
            value_cols=(),
            notes="This is where item-level prices acquire a state. 142 KB.",
        ),
        Dataset(
            id="pricecatcher",
            title="PriceCatcher: Transactional Records",
            tier=Tier.ITEM,
            frequency=Frequency.DAILY,
            url=None,  # editioned: one parquet per month, see PRICECATCHER_URL
            editions=PRICECATCHER_EDITIONS,
            date_col="date",
            entity_cols=("premise_code", "item_code"),
            value_cols=("price",),
            notes="~1.66 MB per monthly edition. NEVER committed raw -- reduced to "
            "item x state x month aggregates at ingest. Not an inflation measure (DOSM).",
        ),
    ]
}

# Catalogue pages carry the metadata the API's `meta` block gives (last_updated,
# data_as_of). We read them instead of spending API quota.
CATALOGUE_BASES: tuple[str, ...] = ("https://open.dosm.gov.my", "https://data.gov.my")


def get(dataset_id: str) -> Dataset:
    try:
        return DATASETS[dataset_id]
    except KeyError:
        raise KeyError(
            f"Unknown dataset {dataset_id!r}. Known: {', '.join(sorted(DATASETS))}"
        ) from None


def by_tier(tier: Tier) -> list[Dataset]:
    return [d for d in DATASETS.values() if d.tier == tier]
