# ASSUMPTIONS.md

Assumptions made without asking, per the project rules ("Ask me at most one batch of
clarifying questions at the start of Phase 0; otherwise make reasonable assumptions and log
them"). Each entry says what we assumed and what we would do instead if it is wrong.

## Data

**A1 — Bulk parquet over the REST API.**
We read `storage.dosm.gov.my` / `storage.data.gov.my` parquet files rather than
`api.data.gov.my`. Rationale in `DATA.md` §3: the REST API is limited to 4 req/min, and five
of the datasets we need (`cpi_3d`, `pricecatcher`, `lookup_item`, `lookup_premise`, and the
4d/5d CPI tables) return 404 on both API endpoints while their parquet files download fine.
_If wrong:_ the pipeline's fetch layer is isolated behind one module; swapping to REST is a
local change.

**A2 — CPI base is 2010 = 100, and it is stable.**
Stated in the `cpi_headline` field metadata ("Index value, with base 2010 = 100") and
corroborated by our validation against DOSM's published real series (max 0.77 RM difference).
We therefore do **no** rebasing anywhere. If DOSM rebases in future, the pipeline's
known-answer test will fail loudly rather than silently mis-deflate.

**A3 — Annual CPI = mean of the 12 monthly index values.**
Used to deflate annual and biennial series. Validated: this method reproduces
`wages_real_state_citizen` to within RM 1 across all 16 states. DOSM presumably does the
same. If a future DOSM vintage diverges, the validation test catches it.

**A4 — "Citizens" and "all employees" are different populations** (`wages_state` vs
`wages_real_state`). We use the citizen series for the headline real-wage work and only ever
compare like with like. See `DATA.md` §6.

**A5 — State set is the 16 states/federal territories**, matched on exact string. Excludes
the `Malaysia` aggregate row, which we source separately from national datasets.

**A6 — PriceCatcher is IN SCOPE, all 58 monthly editions** (2022-01 → 2026-10). ~~Tier 3~~
→ promoted to Tier 1 by decision at the Phase 0 gate. See A13 for the storage consequence.

**A6a — PriceCatcher is never an input to inflation figures.** DOSM's own caveat says it is
collected for price surveillance and that "inflation analysis should be conducted using
DOSM's CPI data". It powers an item-level panel only, and the UI labels it as such.

## Method

**A7 — Periods are chosen by data availability, not to maximise drama.** Findings 1 and 2
use two periods (2010→2025 for wages, 2012→2024 for HIES) because the underlying sources
have different start years. Both periods are reported side by side.

**A8 — "Real" always means CPI-deflated, and the deflator is named.** Every real-terms number
in the UI carries its base year and its deflator inline. No bare "real" figures.

**A9 — No interpolation, ever.** Survey-year gaps (HIES) are shown as gaps. If a chart cannot
be drawn honestly at monthly resolution, it drops to survey resolution rather than smoothing.

**A10 — Findings that contradict the expected narrative are reported as found.** The
low-income CPI result (+0.4 pp vs headline) is reported plainly. We do not reframe, and we
do not search for a period that makes it look bigger.

## Engineering

**A11 — Cloudflare free tier only**, per the hard constraint. Workers AI, KV, D1, R2, Pages.
No paid LLM API. The agent must run on a Workers AI model that supports tool/function calling
or structured JSON output — to be confirmed in Phase 4 against the current catalogue, not assumed.

**A12 — The frontend never calls `api.data.gov.my`.** All upstream access happens in the
scheduled GitHub Action, which writes versioned artifacts. This is a hard constraint from the
brief and it also happens to be required by the 4 req/min limit.

**A13 — Artifacts are small enough to live in the repo.** The whole Tier 1 + Tier 2 set is
well under 2 MB as parquet and will be smaller as trimmed JSON.

**A13a — PriceCatcher raw is ~96 MB and MUST NOT be committed.** 58 editions × 1.66 MB.
The pipeline reduces it at ingest to compact aggregates (item × state × month medians and
counts) and ships only those. The ~96 MB raw layer is a CI cache, not an artifact. If a
future need genuinely requires raw rows in the browser, that is the point at which R2 gets
introduced — not before.

**A14 — Astro + Observable Plot.** Confirmed at the Phase 0 gate.
Static content site with a handful of interactive islands. Astro ships zero JS by default,
which is the cheapest route to the Lighthouse ≥ 90 bar. Observable Plot for charts
(small, declarative, good small-multiples and choropleth support).
_This supersedes "Astro or Vite/React" in the brief._

**A17 — Both income framings are shown side by side.** Confirmed at the Phase 0 gate.
HIES household income for the conventional cost-of-living reading, and the employer wage
survey for the continuous annual/monthly series. They are different populations measuring
different things; presenting both, and showing that they agree on the *shape* of the answer,
is stronger evidence than picking one.

**A18 — The user owns all git operations.** *(Superseded by explicit instruction on
2026-10-28; the earlier "commits stay local" version is void.)*

- **The agent does not commit, stage, push or amend anything.** The user commits and pushes
  manually.
- **No attribution lines of any kind**, and no co-authors other than `jerkfish`. Specifically
  no `Co-Authored-By: Claude` trailer.
- The agent does not rewrite history.

Practical consequence: `.gitignore` must be correct and kept current, because the user's
`git add -A` will otherwise sweep up build and cache output. `.cache/` (163 MB of downloaded
upstream parquet) and `.discovery/` are ignored for exactly this reason. Note that a
`.gitignore` entry does nothing for a file that is *already* tracked — those need
`git rm --cached`.

**A15 — English first, BM structure in place.** i18n keys from the start; BM translations for
key UI strings only if time allows in Phase 5.

**A16 — Conventional commits from the first commit**, since a clean history is an explicit
grading criterion.

**A19 — The site reads artifacts at build time, never at runtime.**
No fetch calls, no API, no client-side data loading. A missing artifact fails the build rather
than rendering an empty chart a reader would not notice.

**A20 — Charts are client-side islands; prose and tables are static HTML.**
Every chart has a text alternative, so the findings are reachable with JavaScript disabled,
but the graphics are not. Build-time SVG rendering would be better and is listed as a gap.

**A21 — No default basket in the calculator.**
Sharpened from A6. DOSM does not publish expenditure weights as open data, so inventing a
plausible default and presenting it as official would be the easiest and most misleading
option. The form starts empty, with a one-click even split and a clear explanation of why
there is no preset.

## Open questions deferred to the phase gate

See the Phase 0 report. The material ones: whether to include PriceCatcher, whether the
Cloudflare/GitHub accounts exist yet, and which of HIES household income vs employer wage
survey should be the site's primary "income" framing.
