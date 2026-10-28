# Malaysia Cost-of-Living Reality Check

**Is purchasing power in Malaysia actually improving — and does the answer differ by state,
income group, and spending pattern?**

A public web app built entirely on Malaysia's open data. All figures come from DOSM via
[open.dosm.gov.my](https://open.dosm.gov.my) and [data.gov.my](https://data.gov.my). Nothing
is modelled, interpolated, or invented.

> **Status: Phase 1 (pipeline) complete.** No frontend, Worker, or eval harness yet.
> This README will lead with charts once they ship.

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
cd pipeline
uv sync --extra dev

uv run pytest -m "not network"     # 95 offline tests
uv run pytest -m network           # 18 live upstream contract tests
uv run colcheck run                # full pipeline -> ../data
uv run colcheck run --skip-pricecatcher    # core artifacts only, much faster
```

## Repository layout

```
pipeline/   Python data pipeline: fetch -> validate -> transform -> artifacts
web/        Static site (Astro) — reads only the committed artifacts   [Phase 3]
worker/     Cloudflare Worker — /api/ask agent loop                    [Phase 4]
evals/      Agent evaluation harness                                   [Phase 4]
docs/       DATA.md, ASSUMPTIONS.md, KNOWN-GAPS.md, METHODOLOGY.md, ADRs
data/       Versioned static data artifacts
```

## How this is kept honest

- **Contract tests fail the build** if upstream renames a column, changes a state name, or
  adds a new CPI division.
- **A base-year test asserts the CPI is still 2010=100.** A DOSM rebase would silently
  invalidate every real-terms figure on the site; instead it fails CI.
- **Our deflation is validated against DOSM's own published real-wage series** — currently
  within 0.77 RM across all 16 states. That is a test, not a claim.
- **Nothing is published until every check passes.** A failed run writes no artifacts.
- **No interpolation, anywhere.** Survey-year gaps stay gaps; partial years are labelled.
- Findings that contradict the expected narrative are reported as found — see finding 3.

## Licence

Data © Department of Statistics Malaysia (DOSM), CC BY 4.0. Code: see `LICENSE`.
