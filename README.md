# Malaysia Cost-of-Living Reality Check

**Is purchasing power in Malaysia actually improving — and does the answer differ by state,
income group, and spending pattern?**

A public web app built entirely on Malaysia's open data. All figures come from DOSM via
[open.dosm.gov.my](https://open.dosm.gov.my) and [data.gov.my](https://data.gov.my). Nothing
is modelled, interpolated, or invented.

> **Status: Phase 3 (frontend) complete.** A seven-page static site is built and passing
> Lighthouse 99/100/100/100. The Worker and eval harness (Phase 4) and the deploy (Phase 5)
> are still to come, so there is no live URL yet.

## Forecast, reported honestly

Headline CPI is forecast 12 months ahead with two models against a **persistence baseline**
— "inflation stays what it was a year ago". Both are backtested with rolling-origin,
expanding-window folds and scored on the year-on-year rate in percentage points.

| Model | MAE (pp) | MAPE | RMSE (pp) |
| --- | --- | --- | --- |
| **ETS** (Holt-Winters) | **0.17** | 10.5% | 0.22 |
| SARIMA(1,1,1)(1,1,1,12) | 0.18 | 10.9% | 0.24 |
| *baseline:* inflation persistence | 0.46 | 31.5% | 0.53 |

Measured over 96 backtested forecasts. ETS beats the baseline by roughly 2.7×, which is a real
result — see [ADR 0002](docs/adr/0002-forecast-honestly-against-a-real-baseline.md) for why the
textbook seasonal-naive baseline would have flattered this into meaninglessness.

**It does not always win.** Across the national headline series and all 13 CPI divisions, **5
of 14 do not beat the baseline** — Clothing & Footwear (03), Furnishings (05), Transport (07),
Restaurants & Accommodation (11) and Personal Care (13). Those artifacts carry
`beats_baseline: false`, a verdict, and a caveat telling the reader not to trust the
projection. A forecast that loses to "same as last year" has no business being presented as
insight, and saying so is a feature.

## What the data already says

Three findings are confirmed against the real data — see `docs/DATA.md` §7 for the numbers
and their caveats.

1. **Real incomes rose nationally, but state averages hide a 2–3× spread.** Household median
   income grew 52.9% in real terms (2012→2024) nationally, but from +24.4% in W.P. Putrajaya
   to +80.1% in Terengganu. A second, independent source (the employer wage survey,
   2010→2025) shows the same shape: +45.6% nationally, +29.1% in Johor to +91.2% in Sarawak.
2. **That divergence is a wage story, not a price story.** Cumulative CPI inflation is
   remarkably uniform across states (spread ~18 pp over 15 years) while real income growth
   varies by 56–62 pp. Putrajaya has both the highest inflation and the lowest real income
   growth; the states that fell behind did so on wages, not on prices.
3. **Your basket matters more than your postcode — and "poor" ≠ "faster inflation".**
   Food away from home rose 77.3% (2010→2025) against 47.4% for food at home. Urban
   households saw 35.6% cumulative inflation against 29.5% for rural — while low-income
   households tracked the headline rate almost exactly (35.0% vs 34.6%).

## Data

Everything derives from DOSM's open data, published under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The pipeline reads **bulk parquet from `storage.dosm.gov.my`**, not the REST API. The API is
rate limited to **4 requests per minute** and is missing five datasets we need entirely
(`cpi_3d/4d/5d`, `pricecatcher`, `lookup_item` all return 404 on it). Confirmed coverage for
every dataset, and the reasoning, is in [`docs/DATA.md`](docs/DATA.md).

## Quick start

```bash
# Data
cd pipeline
uv sync --extra dev
uv run pytest -m "not network"     # 142 offline tests
uv run pytest -m network           # 18 live upstream contract tests
uv run colcheck run                # full pipeline -> ../data
uv run colcheck run --skip-pricecatcher --skip-forecast   # fast iteration

# Site
cd ../web
bun install
bun x astro build                  # static site -> web/dist
bun x astro preview                # serve it locally
```

The site reads `data/*.json` at **build time** and never contacts `data.gov.my`. If an
artifact is missing the build fails, rather than rendering a chart that quietly means nothing.

## The site

Seven pages, eleven charts, no runtime data fetching.

| Page | What it shows |
| --- | --- |
| `/` | The three findings, with the caveats that matter |
| `/income` | Nominal vs real wages and household income, per state |
| `/states` | The full ranking, and the divergence against inflation |
| `/calculator` | Your own inflation rate, from your own spending split |
| `/forecast` | 12-month projection, including where the model fails |
| `/prices` | What specific items cost, by state |
| `/methodology` | Every formula, assumption and limitation |

**Lighthouse: performance 99, accessibility 100, best practices 100, SEO 100**, zero failing
audits. Dark mode, no webfonts, and a page with no charts ships zero JavaScript.

## Repository layout

```
pipeline/   Python data pipeline: fetch -> validate -> transform -> artifacts
web/        Astro static site — reads only the committed artifacts, at build time
worker/     Cloudflare Worker — /api/ask agent loop                    [Phase 4]
evals/      Agent evaluation harness                                   [Phase 4]
docs/       Documentation (below)
data/       Versioned static data artifacts
```

### Documentation

| Document | What it covers |
| --- | --- |
| [docs/DATA.md](docs/DATA.md) | Dataset inventory, confirmed coverage, API mechanics, why parquet over REST |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | Every formula, every assumption, and what would falsify each one |
| [docs/KNOWN-GAPS.md](docs/KNOWN-GAPS.md) | What this does not do, cannot do, or has not verified |
| [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | Decisions taken without asking, and what to do if they are wrong |
| [docs/adr/](docs/adr/) | Architecture decision records |

## How this is kept honest

- **Contract tests fail the build** if upstream renames a column, changes a state name, or
  adds a new CPI division.
- **A base-year test asserts the CPI is still 2010=100.** A DOSM rebase would silently
  invalidate every real-terms figure on the site; instead it fails CI.
- **Our deflation is validated against DOSM's own published real-wage series** — currently
  within 0.77 RM across all 16 states. That is a test, not a claim.
- **Nothing is published until every check passes.** A failed run writes no artifacts.
- **No interpolation, anywhere.** Survey-year gaps stay gaps; partial years are labelled.
- **A forecast that loses to the baseline says so in the data**, not just in a comment.
- Findings that contradict the expected narrative are reported as found — see finding 3, and
  [ADR 0004](docs/adr/0004-report-inconvenient-results-as-found.md).

## Anomaly detection

STL decomposition with robust (MAD-scaled) residual scoring, plus binary segmentation for
durable regime shifts. With no hard-coded dates, it independently isolates the 2020 deflation
(z = −25.9) and the 2022 inflation surge, and finds four inflation regimes since 1981.

Flags are **statistical, not explanatory**. Where a flag coincides with movement in an
independently datable series, that is reported as corroboration and never as cause.

## Licence

Data © Department of Statistics Malaysia (DOSM), CC BY 4.0. Code: see `LICENSE`.
