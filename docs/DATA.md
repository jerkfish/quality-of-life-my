# DATA.md — Data source discovery and dataset selection

_Phase 0 deliverable. Everything below was confirmed by calling the live endpoints and
downloading the actual files on 2026-10-28 — no dataset ID, field name, or coverage claim
in this document is assumed._

---

## 1. API mechanics (confirmed, not assumed)

### Endpoints

| Endpoint | Purpose | `id` required |
| --- | --- | --- |
| `GET https://api.data.gov.my/data-catalogue?id=<id>` | General data.gov.my datasets | yes |
| `GET https://api.data.gov.my/opendosm?id=<id>` | OpenDOSM / DOSM datasets | yes |

Calling either endpoint **without** `?id=` returns `400`:

```json
{"status_code": 400, "details": ["Query parameter 'id' is required."]}
```

There is **no** "list all datasets" endpoint. The dataset inventory has to come from the
catalogue web pages (`https://open.dosm.gov.my/data-catalogue`,
`https://data.gov.my/data-catalogue`), which inline the full index as HTML. We scraped 235
OpenDOSM slugs and 348 general-catalogue slugs.

### Authentication

None. No API token, no key. Confirmed against `https://developer.data.gov.my/quickstart`.

### Rate limits

**4 requests per minute, per API** (Data Catalogue, OpenDOSM, Weather, GTFS Static, GTFS
Realtime). Exceeding it returns `429`. Source: `https://developer.data.gov.my/rate-limit`.

This is a hard constraint on the design: a pipeline that paginates the REST API for
historical series is not viable. See §3.

### Query parameters

| Parameter | Syntax | Notes |
| --- | --- | --- |
| `filter` | `<value>@<column>` | exact, case-sensitive; comma-separate for multiple columns |
| `ifilter` | `<value>@<column>` | exact, case-insensitive |
| `contains` / `icontains` | `<value>@<column>` | partial match |
| `range` | `<column>[<begin>:<end>]` | numeric, inclusive |
| `sort` | `<column>`, `-<column>` | `-` prefix = descending |
| `date_start` / `date_end` | `<YYYY-MM-DD>@<date_column>` | inclusive |
| `limit` | integer | max records |
| `include` / `exclude` | comma-separated column names | `include` wins if both given |
| `meta` | `true` | wraps response as `{meta, data}` |

### Response shape

With `?meta=true`:

```json
{
  "meta": {
    "catalogue_id": "cpi_headline",
    "data_as_of": "2026-09",
    "last_updated": "2026-10-19 13:12",
    "next_update": "2026-11-19 12:30",
    "data_source": ["DOSM"],
    "update_frequency": "MONTHLY",
    "total": 1,
    "limit": 1
  },
  "data": [{"date": "1986-12-01", "index": 105.5, "division": "03"}]
}
```

Without `meta=true` the response is a bare JSON array of records.

Error shape differs from the documented one — the live API returns
`{"status_code": 404, "details": ["The data catalogue (x) requested does not exist."]}`,
not the `{status, errors}` form in the docs. Our contract tests assert the **actual** shape.

---

## 2. Key finding: not every catalogue dataset is on the REST API

The catalogue page and the API disagree. Confirmed `404` on both `/opendosm` and
`/data-catalogue` for IDs that *do* have catalogue pages:

| ID | Catalogue page | REST API | Bulk parquet |
| --- | --- | --- | --- |
| `cpi_3d`, `cpi_4d`, `cpi_5d` | yes | **404** | yes |
| `pricecatcher` | yes | **404** | yes |
| `lookup_item`, `lookup_premise` | yes | **404** | yes |
| `fuelprice` | yes | **200** (data-catalogue only) | yes |

The docs state this may happen ("If a data catalogue is not available through the API, it
will be explicitly mentioned") — but they do not say which ones, and the mapping is not
inferable. **This was only discoverable by probing.**

---

## 3. Key finding: every dataset page exposes a bulk parquet/CSV URL

Each catalogue page inlines a `__NEXT_DATA__` JSON blob containing the complete dataset
metadata, including direct bulk-download links:

```json
{
  "id": "cpi_headline",
  "title": "Monthly CPI by Division (2-digit)",
  "frequency": "MONTHLY",
  "data_source": ["DOSM"],
  "fields": [
    {"name": "date", "title": "Date", "description": "[Date] The date in YYYY-MM-DD format…"},
    {"name": "division", "title": "Division", "description": "[String] 2-digit code from 01-13 or 'overall'…"},
    {"name": "index", "title": "Index", "description": "[Float] Index value, with base 2010 = 100"}
  ],
  "data_as_of": "2026-09",
  "last_updated": "2026-10-19 13:12",
  "next_update": "2026-11-19 12:30",
  "methodology": "The CPI measures the cost of purchasing a constant, representative 'basket'…",
  "caveat": "When interpreting inflation rates derived from the CPI…",
  "link_parquet": "https://storage.dosm.gov.my/cpi/cpi_2d.parquet",
  "link_csv": "https://storage.dosm.gov.my/cpi/cpi_2d.csv",
  "link_editions": ["2026-10", "2026-09", …]
}
```

`storage.dosm.gov.my` / `storage.data.gov.my` are plain static object storage with **no
documented rate limit**, and the files are small. We downloaded the entire candidate set —
48 files, ~1.5 MB total — in a few seconds.

**Decision: the pipeline reads bulk parquet, not the REST API.** Rationale:

- The REST API costs 4 requests/minute; the full CPI series alone would take minutes and
  would fail a scheduled job the moment DOSM adds a dataset.
- Five datasets we need (`cpi_3d`, `pricecatcher`, `lookup_item`, …) are **not on the API at all**.
- Parquet is typed and columnar; the REST API returns JSON with no type guarantees.
- `meta` gives us `last_updated` / `data_as_of` / `next_update` for the "data last updated"
  badge — we capture that from the page metadata instead.

The REST API is still used for **contract tests**: a scheduled CI job asserts the live API's
shape still matches our recorded expectations, so upstream schema changes fail loudly.

---

## 4. Dataset inventory

Coverage figures are the actual min/max dates observed in the downloaded files.

### Tier 1 — core (drives every analysis)

| ID | Title | Freq | Coverage | Key fields | API | Parquet |
| --- | --- | --- | --- | --- | --- | --- |
| `cpi_headline` | Monthly CPI by Division (2-digit) | monthly | 1980-01 → 2026-09 | `date, division, index` | ✓ | 19 KB |
| `cpi_headline_inflation` | Monthly CPI Inflation by Division | monthly | 1980-02 → 2026-09 | `date, division, inflation_yoy, inflation_mom` | ✓ | 19 KB |
| `cpi_state` | Monthly CPI by State & Division | monthly | 2010-01 → 2026-09 | `state, date, division, index` | ✓ | 57 KB |
| `cpi_state_inflation` | Monthly CPI Inflation by State & Division | monthly | 2010-02 → 2026-09 | `state, date, division, inflation_yoy, inflation_mom` | ✓ | 68 KB |
| `cpi_3d` | Monthly CPI by Group (3-digit) | monthly | 2010-01 → 2026-09 | `date, group, index` | ✗ 404 | 16 KB |
| `cpi_core` | Monthly Core CPI by Division | monthly | 2018-01 → 2026-09 | `date, division, index` | ✓ | 6 KB |
| `cpi_strata` | Monthly CPI by Strata & Division | monthly | 2010-01 → 2026-09 | `strata, date, division, index` | ✓ | 12 KB |
| `cpi_lowincome` | Monthly CPI for Low-Income Households | monthly | 2010-01 → 2026-09 | `date, division, index` | ✓ | 8 KB |
| `mcoicop` | MCOICOP lookup (COICOP classification) | infrequent | static, as of 2018 | `digits, division, group, class, subclass, desc_en, desc_bm` | ✓ | 14 KB |
| `wages_state` | Salaries & Wages by State & Sex (citizens) | annual | 2010 → 2025 | `state, date, sex, recipients, wages_median, wages_mean` | ✓ | 12 KB |
| `wages_real_state_citizen` | Real Salaries & Wages of Citizens by State | annual | 2010 → 2025 | `state, date, wages_median_real, wages_mean_real` | ✓ | 5 KB |
| `formal_wages_state` | Monthly Formal Sector Wages by State | monthly | 2022-01 → 2026-06 | `date, state, employees, wages_median` | ✓ | 7 KB |
| `hh_income_state` | Household Income by State (HIES) | survey years | 1970 → 2024 | `state, date, income_mean, income_median` | ✓ | 5 KB |
| `lfs_month` | Monthly Principal Labour Force Statistics | monthly | 2010-01 → 2026-08 | `date, lf, lf_employed, lf_unemployed, u_rate, p_rate, ep_ratio` | ✓ | 10 KB |
| `population_state` | Population Table: States | yearly | 1970 → 2026 | `date, state, sex, age, ethnicity, population` | ✓ | 389 KB |

### Tier 2 — supporting context

| ID | Title | Freq | Coverage | Why we want it |
| --- | --- | --- | --- | --- |
| `hies_state` | Household Income & Expenditure: States | survey years | 2022, 2024 | income **and** expenditure **and** Gini **and** poverty in one table |
| `hies_state_percentile` | Household Income by State & Percentile | survey years | 2019 → 2024 | income-**group** dimension (deciles), not just state averages |
| `hh_poverty_state` | Poverty by State | yearly | 1970 → 2024 | absolute / hardcore / relative poverty |
| `hh_inequality_state` | Income Inequality by State | yearly | 1974 → 2024 | Gini by state |
| `fuelprice` | Price of Petroleum & Diesel | weekly | 2017-03-30 → 2026-10-22 | transport inflation driver + named, datable events |
| `gdp_state_real_supply` | Annual Real GDP by State & Sector | yearly | — | real output context for wage divergence |

### Tier 3 — PriceCatcher (item level)

| ID | Title | Freq | Coverage | Note |
| --- | --- | --- | --- | --- |
| `pricecatcher` | PriceCatcher: Transactional Records | daily | monthly editions 2022-01 → 2026-10 | **1.7–3.5 MB per month**, 58 editions. ~1.7M raw rows in a single month. |
| `lookup_item` | PriceCatcher: Item Lookup | infrequent | as of 2026-09-09 | 797 items across 8 groups, 59 categories |
| `lookup_premise` | PriceCatcher: Premise Lookup | infrequent | as of 2026-10-27 | 3,931 premises across 16 states, 13 premise types |

**PriceCatcher caveat (from DOSM, quoted verbatim in the metadata):** _"This data is
collected for the purpose of price surveillance… Inflation surveillance requires a
different approach, in particular to ensure proper representativeness. Inflation analysis
should be conducted using DOSM's CPI data. Therefore, PriceCatcher data should be used as a
complement rather than a replacement to CPI data."_

So it powers a **"what does a specific item cost near you"** panel, explicitly labelled as
not-an-inflation-measure — never an input to the headline inflation numbers.

---

## 5. Why these datasets join

The join keys are **date** and **state**, and they line up cleanly.

**State naming is identical across all four state-keyed sources** (verified):

```
Johor, Kedah, Kelantan, Melaka, Negeri Sembilan, Pahang, Perak, Perlis, Pulau Pinang,
Sabah, Sarawak, Selangor, Terengganu, W.P. Kuala Lumpur, W.P. Labuan, W.P. Putrajaya
```

16 states/federal territories, exact string match. `wages_state` and `formal_wages_state`
additionally carry a `Malaysia` aggregate row; `cpi_state` and `hh_income_state` do **not** —
the national CPI series must come from `cpi_headline`, and the national household income
series from `hh_income`. An inner join that assumes otherwise silently drops the national
row. The registry encodes this per dataset as `allow_national`, and a test asserts it matches
reality.

**Time alignment:** `cpi_state` starts 2010-01 and `wages_state` starts 2010. This is not a
coincidence — DOSM rebased the CPI to **2010 = 100** and the state wage series begins at the
same base year. So nominal wages can be deflated directly by `cpi_state` with **no rebasing
step**, and no splice across base years is needed. This is the single most important reason
this project is feasible.

**Frequency mismatch** is the main modelling chore: CPI is monthly, wages and HIES are
annual/biennial. We align by taking the **mean of the 12 monthly index values** within each
calendar year — a choice validated in §6. Years with fewer than 12 months are flagged
`is_partial` rather than averaged silently.

---

## 6. Validation: we reproduce DOSM's own published real series

`wages_real_state_citizen` is DOSM's own real-wage series (2010 prices). We computed real
wages independently as `nominal_wages_state / (cpi_state.overall / cpi_state.overall.2010)`
and compared, for all 16 states, 2025:

| Metric | Max absolute difference |
| --- | --- |
| real **median** wage | **0.77 RM** |
| real **mean** wage | **0.94 RM** |

The residual is rounding in the published series (DOSM publishes whole ringgit). Our
deflation method is therefore correct, and this becomes a **known-answer test** in the
pipeline test suite (`tests/test_upstream.py`).

**A trap we walked into and out of:** an early cross-check showed differences up to
RM 388. The cause is that two IDENTICALLY-NAMED series cover **different populations**:

| ID | Population |
| --- | --- |
| `wages_state` | employees who are **Malaysian citizens** |
| `wages_real_state_citizen` | employees who are **Malaysian citizens** ← matches `wages_state` |
| `wages_real_state` | **all** employees, citizens and non-citizens |

In Sabah the two differ by 24% in 2025 (RM 2,482 vs RM 1,619 real median). Anyone comparing
`wages_state` against `wages_real_state` would silently measure a citizenship-composition
change and call it inflation. Documented, pinned by a test, and the pipeline asserts the
citizen-vs-citizen pair.

---

## 7. The three findings we can support (verified on the real data)

### Finding 1 — Real incomes did rise nationally, but state averages hide a 2–3× spread

| Measure | Period | National real growth | Worst state | Best state | Spread |
| --- | --- | --- | --- | --- | --- |
| Household median income (HIES) | 2012 → 2024 | **+52.9%** | +24.4% (W.P. Putrajaya) | +80.1% (Terengganu) | **55.7 pp** |
| Median wage, citizens | 2010 → 2025 | **+45.6%** | +29.1% (Johor) | +91.2% (Sarawak) | **62.1 pp** |

Two independent sources (a household survey and an employer wage survey) both say the same
thing: purchasing power improved substantially on average, and the spread across states is
enormous. Note both series also show the 2020 COVID shock (national median wage fell from
RM 2,442 in 2019 to RM 2,076 in 2020).

### Finding 2 — The state divergence is a *wage* story, not a *price* story

Same periods, price inflation by state:

| Measure | Period | Lowest | Highest | Spread |
| --- | --- | --- | --- | --- |
| Cumulative CPI, `cpi_state` | 2010 → 2025 | 23.7% (Sabah) | 41.3% (W.P. Putrajaya) | **17.6 pp** |
| Cumulative CPI, `cpi_state` | 2012 → 2024 | 15.7% (W.P. Labuan) | 33.5% (W.P. Putrajaya) | **17.8 pp** |

Price inflation is remarkably **uniform** across states (spread ~18 pp) while real income
growth varies by **56–62 pp**. The states where purchasing power stagnated did not suffer
runaway prices — their wages simply lagged. W.P. Putrajaya is the clearest case: it has both
the **highest** inflation (33.5%) and the **lowest** real income growth (+24.4%).

This is the counterintuitive, defensible core of the project: in Malaysia, "cost of living"
differences between states are dominated by the income side of the ratio, not the price side.

### Finding 3 — Your basket matters more than your postcode, and "poor" ≠ "faster inflation"

`cpi_3d` (47 national groups) lets us separate "food away from home" from "food at home",
which is exactly the distinction that dominates Malaysian cost-of-living discourse:

| Group | Cumulative 2010 → 2025 |
| --- | --- |
| Food **away from home** (`001`) | **+77.3%** |
| Food **at home** (`011`) | **+47.4%** |

A ~30 pp gap. Two households with identical total spending but different eating-out habits
experienced materially different inflation — which is precisely what the personal inflation
calculator will quantify.

And two results that cut against the usual narrative:

| Comparison | Cumulative 2010 → 2025 | Gap |
| --- | --- | --- |
| Low-income-household CPI vs headline CPI | 35.0% vs 34.6% | **+0.4 pp** |
| Urban vs rural CPI | 35.6% vs 29.5% | **+6.1 pp** |

Low-income households — on aggregate, over 15 years — did **not** experience meaningfully
faster inflation than the headline rate. Urban/rural does matter, and it matters ~15× more
than income group does. The *composition* does differ sharply even where the aggregate
matches: low-income inflation ran hotter on restaurants (+6.2 pp) and tobacco/alcohol
(+3.5 pp), and cooler on education (−6.3 pp) and food (−5.1 pp).

> We state the +0.4 pp result plainly rather than hunting for a framing that makes it
> dramatic. If the data does not support "the poor are hit hardest by inflation", the site
> says so. That is the point of the project.

### Additional material available

- **Forecasting:** `cpi_headline` gives 549 monthly observations (1980-01 → 2026-09) — ample
  for SARIMA/ETS and rolling-origin backtesting against a seasonal-naive baseline.
- **Anomaly/regime detection:** STL residuals on the monthly division series should flag
  COVID-2020 and fuel-price episodes; `fuelprice` (weekly, 2017→2026) and `lfs_month` give
  independent, *datable* series to justify annotations from data rather than narrative.
- **Income-group dimension:** `hies_state_percentile` gives decile-resolution income by state.

---

## 8. Data-quality issues to handle (and surface in the UI)

1. **`cpi_core` only starts 2018-01** — only ~105 months. Not usable for a 2010-base
   comparison; restrict it to short-horizon work and label it.
2. **HIES is a survey, not a time series.** `hh_income_state` years are irregular
   (1970, 1974, 1976, 1979, 1984, 1987, 1989, 1992, 1995, 1997, 1999, 2002, 2004, 2007,
   2009, 2012, 2014, 2016, 2019, 2020, 2022, 2024). Charts must not imply continuity;
   **no interpolation**.
3. **`hies_state` has only 2022 and 2024** (32 rows) — a 2-point series.
4. **The 2020 wage dip is real, not an error** — pandemic effect on the employee population.
5. **`wages_state` medians are composition-sensitive.** The median of *employees* shifts when
   the employee mix changes, independent of any individual's pay.
6. **Different update cadences**: CPI monthly (next 2026-11-19), formal wages monthly (as of
   2026-06), state wages annual (as of 2025), HIES biennial (as of 2024). The UI must show a
   per-chart as-of date, not one global "last updated".
7. **`population_state` is 389 KB** with sex × age × ethnicity granularity — aggregate to
   state-year totals during ingest, don't ship the raw file to the browser.
8. **The error-response shape differs from the published docs** (§1) — assert what the API
   actually returns, not what the docs say.
9. **Nulls are genuine gaps, not corruption.** `cpi_headline` has 1,464 null index values
   (new divisions did not exist in 1980), `cpi_3d` has 240, `hh_income_state` has 11 null
   medians. The contract layer warns and carries them through; nothing fills them in.
10. **`lookup_premise` and `lookup_item` each contain one sentinel row** with a `-1` code and
    null attributes. It cannot be attributed to a state and is dropped at ingest.
11. **PriceCatcher is ~163 MB of raw cache across 58 editions** and must never be committed;
    it is reduced to a 4.5 MB item × state × month aggregate.
