# ADR 0003 — Reduce PriceCatcher at ingest rather than shipping raw rows

- **Status:** Accepted
- **Date:** 2026-10-28
- **Phase:** 1 (pipeline)

## Context

PriceCatcher is the item-level price dataset: 797 items, 3,931 premises, 16 states, published
as one monthly parquet per edition from 2022-01 to 2026-10 — 58 editions.

Measured characteristics:

- **~1.7 million raw rows in a single month** (2026-09).
- **1.7–3.5 MB per edition**, so ~163 MB for the full history.
- The user chose to include all 58 editions at the Phase 0 gate, for the "what does a chicken
  cost near you" panel.

It is tempting to ship the raw rows and query them in the browser. That would bury a portfolio
repository under a binary blob that changes every month.

## Decision

Reduce each edition to **item × state × month** statistics at ingest, keeping median, mean,
min, max and observation count. 1.74M raw rows for September 2026 collapse to **4,680 aggregate
rows**. The full 58-edition history becomes a **4.5 MB** parquet artifact.

Raw editions are cached at `.cache/raw/` for the duration of a run and **never committed**.

## Consequences

**Good**

- Repository stays small: 163 MB → 4.5 MB, a 97% reduction.
- The aggregate is the shape every consumer actually wants — "the median price of item X in
  state Y in month Z" — so neither the site nor the agent has to group a million rows.
- Median rather than mean is the right summary: PriceCatcher rows are individual premise
  observations and the distribution has long tails (a hypermarket and a wet market are not the
  same shop).

**Bad**

- **Premise-level detail is lost.** "Which specific shops are cheapest in Johor Bahru" is no
  longer answerable. If that becomes a requirement, the raw monthly file can be fetched on
  demand for the months in question — 1.7 MB is nothing for a single month. Not needed yet.
- Distribution shape is lost beyond min/max. We keep the observation count so thin cells stay
  visible, but we cannot reconstruct percentiles.
- Semi-sentinel rows (`item_code = -1`, one premise with a null state) are dropped, so a small
  number of raw observations are not represented. They cannot be attributed to a state, so
  they could not be used for a state-level panel anyway.

## Guardrails

- DOSM's own caveat is repeated in the artifact metadata and in the UI: PriceCatcher is a
  price-surveillance dataset and **inflation analysis should use CPI**. It never feeds an
  inflation number.
- The aggregate is our own construct rather than an upstream dataset, so it is not covered by
  a registry contract; the pipeline asserts its invariants directly (no null months, no
  zero-observation rows) instead.

## Alternatives rejected

- **Commit raw editions** — 163 MB of monthly-changing binaries in a portfolio repository.
- **Ship all raw rows to the browser as JSON** — an order of magnitude larger than parquet,
  for data the client would immediately aggregate anyway.
- **Introduce R2** — unnecessary once the aggregate is 4.5 MB. R2 would only earn its place if
  raw premise-level data were genuinely needed in the browser, and it is not.
- **Sample editions (e.g. one per year)** — throws away the ability to show an item's actual
  path through 2022's inflation spike, which is the interesting part.
