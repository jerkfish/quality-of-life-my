# ADR 0001 — Ship static artifacts instead of calling data.gov.my from the browser

- **Status:** Accepted
- **Date:** 2026-10-28
- **Phase:** 0 (discovery) / 1 (pipeline)

## Context

The app needs historical CPI, wage and household-income series. The obvious architecture is
for the frontend to call `api.data.gov.my` directly.

Probing the live service established three facts that decide this:

1. The API is rate limited to **4 requests per minute**, per endpoint. A single page loading a
   16-state choropleth with 14 CPI divisions would exhaust that instantly.
2. **There is no bulk endpoint.** `?id=` is required; there is no way to list datasets, so
   discovery itself requires scraping the catalogue pages.
3. **Five datasets we need are not on the API at all.** `cpi_3d`, `cpi_4d`, `cpi_5d`,
   `pricecatcher` and `lookup_item` all return `404` on both API endpoints, despite having
   catalogue pages.

The brief also imposed a hard constraint: the frontend must never hit `data.gov.my` directly.

## Decision

A scheduled GitHub Action fetches bulk **parquet** from `storage.dosm.gov.my` /
`storage.data.gov.my`, validates it against declared contracts, and reduces it to static JSON
artifacts committed under `data/`. The site and the Worker read only those artifacts.

The REST API is retained for **contract tests only**: CI asserts the live schema still matches
expectations, so upstream drift fails loudly.

## Consequences

**Good**

- No rate limit on the read path. The whole Tier 1 + Tier 2 dataset is under 2 MB.
- The five API-missing datasets become available, including the `food away from home` split
  that finding 3 depends on.
- A DOSM outage cannot take the site down.
- Parquet is typed and columnar; JSON from the API carries no type guarantees.
- Every artifact has a reviewable git diff, so a wrong-looking number is caught by a human.

**Bad**

- Data is as fresh as the last pipeline run, not live. Acceptable for monthly CPI, and the
  per-artifact as-of date makes the staleness explicit rather than hidden.
- PriceCatcher raw is ~163 MB per full refresh, so the CI job is slow and that cache must
  never be committed.
- A schema change upstream breaks the build until someone updates the contract. This is the
  intended behaviour, not a cost.

## Alternatives rejected

- **Frontend calls the API directly** — impossible under the 4 req/min limit and the brief.
- **A caching proxy Worker** — still bound by the upstream rate limit, and the free tier
  cannot mask a 4 req/min origin.
- **Fetch-on-demand in the Worker** — same rate limit, plus cold-start latency on every
  request.
