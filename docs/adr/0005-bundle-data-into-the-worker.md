# ADR 0005 — Bundle the agent's data into the Worker instead of serving it from R2

- **Status:** Accepted
- **Date:** 2026-10-29
- **Phase:** 4 (Worker)

## Context

The Worker needs the prepared data to answer anything. Three options were on the table:

1. Bundle a trimmed copy of the artifacts into the Worker.
2. Publish the artifacts to R2 and have the Worker fetch them.
3. Put them in KV.

R2 credentials were supplied for this project, which makes option 2 the expected choice. Then
two things turned out to be true.

**The credentials cannot do it.** The R2 key pair is scoped to a different bucket
(`exampapers`, from another project) and returns `AccessDenied` for listing, creating, or
writing anything else. The Cloudflare API token can list R2 buckets but not create one. So
`colcheck-data` does not exist and neither credential can make it exist.

**And at this size it would not be the better engineering choice anyway.** The full `data/`
tree is about 1.7 MB of JSON, but the agent does not need most of it: the bulk is a
200-month × 16-state × 14-division CPI matrix. Reduced to what the tools actually query, the
bundle is **415 KB raw and 123 KB gzipped**, comfortably inside the Worker bundle limit.

## Decision

Bundle the data into the Worker. Add `uv run colcheck bundle` to the pipeline, emitting a
trimmed `worker/src/generated/data.json`.

The trim is explicit and self-documenting. The bundle carries a `dropped_for_size` array
naming every reduction — that state CPI divisions became annual snapshots, that 13 of 14
anomaly series were dropped, that PriceCatcher's monthly history was left out and only the
latest month per state kept. A silent trim would be indistinguishable from data that never
existed.

## Consequences

**Good**

- No network hop on any tool call. Every answer is computed in-process from an object that is
  already resident, which matters when a single question can make six tool calls.
- No second system to keep in sync, and no failure mode where the Worker is up but its data
  store is not.
- The agent and the charts are compiled from the *same* artifacts, so they cannot disagree.
  That is the property this project most needs to preserve.
- Deploying the Worker deploys its data, atomically.

**Bad**

- **A data refresh requires a Worker redeploy.** The pipeline commits artifacts monthly; CI
  would need to redeploy the Worker after a data change for the agent to see it. Today that is
  a manual step, and `docs/KNOWN-GAPS.md` records it.
- The Worker is 123 KB larger, and Worker startup parses the bundle. Measured startup is 6 ms,
  so this is not a practical cost at this size.
- **PriceCatcher's monthly history is unavailable to the agent**, only the latest month. A
  question like "how has the price of chicken changed since 2022" cannot be answered by the
  agent even though the site can draw it.

## When to revisit

R2 becomes the right answer when either of these stops being true:

- the trimmed bundle outgrows the Worker bundle limit, or
- the data needs to refresh more often than the Worker redeploys.

The second is the likelier trigger. The path back is small: `bundle.py` already produces
exactly the object that would be uploaded, so publishing it instead of writing it locally is a
short change — once a bucket and a scoped token exist.

## Alternatives rejected

- **Upload to the provided R2 bucket** — it exists, but it belongs to another project, and
  writing this project's data into it would be a surprising thing to do with someone else's
  storage.
- **KV** — the API token returns `401` for KV, and a per-request fetch adds latency to
  something that is currently free.
- **Give the agent the full untrimmed dataset** — 1.7 MB in the bundle, and the extra 1.3 MB
  is a matrix no tool queries. Bundling everything because it fits is how bundles stop fitting.
