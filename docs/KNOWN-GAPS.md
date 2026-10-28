# Known gaps and limitations

Things this project does not do, cannot do with the available data, or has not verified yet.
Kept deliberately blunt: a hiring manager reading this should be able to tell what is solid
from what is provisional.

## Data gaps (cannot be fixed, only disclosed)

**G1 — CPI expenditure weights are not open data.**
The personal inflation calculator needs a spending split to weight divisions by. DOSM
publishes those weights only in the CPI Technical Notes PDF, not as a dataset — we searched
the full catalogue and the only "weight" hits are child-nutrition datasets. Consequence: the
calculator requires the *user's* split as input and ships no default basket pretending to be
the official one. If we later extract the official weights from the technical notes, they
must be transcribed and cited, not estimated.

**G2 — Wage data covers formal employees only.**
`wages_state` and `formal_wages_state` are employer surveys of employees. The self-employed,
gig workers and the informal sector — a large share of Malaysian workers — are absent. Every
wage-based real-income figure on the site inherits this.

**G3 — Medians are composition-sensitive.**
The *median of the employee population* moves when the mix of employees changes, with no
change in anyone's pay. This is a real caveat on findings 1 and 2, and the UI states it. It
cannot be corrected without microdata we do not have.

**G4 — HIES is a survey with irregular gaps.**
Household income exists for 1970, 1974, 1976, 1979, 1984, 1987, 1989, 1992, 1995, 1997,
1999, 2002, 2004, 2007, 2009, 2012, 2014, 2016, 2019, 2020, 2022, 2024 — and no other years.
We do not interpolate. `hies_state` covers only 2022 and 2024.

**G5 — `wages_state` and `hh_income_state` measure different things.**
Individual wage from an employer survey vs household income from a household survey. They
agree on the *shape* of the answer, which is why we show both, but they are not
interchangeable and the site never plots them on a shared axis.

**G6 — State CPI starts in 2010.**
Pre-2010 regional analysis is impossible; `cpi_headline` goes back to 1980 nationally only.

**G7 — `cpi_core` starts in 2018.**
Only ~105 monthly observations, so it cannot be used for a 2010-base comparison.

## Method gaps

**G8 — PriceCatcher is not an inflation measure.**
DOSM says so explicitly and we repeat it in the UI. It powers item-level illustration only.

**G9 — The `food away from home` / `food at home` split is national only.**
`cpi_3d` has no state dimension. The finding holds nationally; a state-level version of it
would need a different source.

**G10 — Five of fourteen series cannot be forecast better than the baseline.**
Divisions 03 (Clothing & Footwear), 05 (Furnishings), 07 (Transport), 11 (Restaurants &
Accommodation) and 13 (Personal Care) fail to beat inflation persistence over the backtest
window. The pipeline reports this rather than presenting a forecast anyway. It is a real
result about forecastability, not a bug — but it does mean the forecast view is honest rather
than uniformly useful.

**G10a — Forecasts are national only.**
No state-level or district-level forecasts. `cpi_state` could support them, but 16 states ×
14 divisions × ~12 s per series is hours of CI. The national series and the 13 divisions are
what ship.

**G10b — SARIMA parameters are fixed, not searched.**
`SARIMAX(1,1,1)(1,1,1,12)` for every series. Reproducible and statable in the methodology,
but not optimal per division. A division where auto-ARIMA would pick a different order may be
under-served.

**G10c — Anomaly flags are unvalidated against an external event list.**
The flags land on known episodes (2020 deflation, 2022 surge) without any hard-coded dates,
which is corroboration. But we have not systematically checked them against a published
timeline of Malaysian price events, so "the detector finds the right things" is supported by
inspection rather than by a test that would catch a change.

**G11 — Real-wage validation tolerance is 2 RM.**
We reproduce DOSM's published real series to within 0.77 RM today. The tolerance is
deliberately loose enough to absorb rounding in the published figures; it is not zero.

## Engineering gaps

**G12 — `fetch.py` retry/backoff paths are untested.**
Coverage there is the lowest in the package. The happy path is exercised by the network
tests; `429` and `5xx` handling is not.

**G13 — `uv.lock` must be committed** for CI's `--frozen` install to be reproducible.

**G14 — No Worker or eval harness yet.** Phase 4. The frontend shipped in Phase 3.

**G14a — i18n is a client-side toggle, not real localisation.**
The Bahasa Malaysia dictionary covers navigation and key chrome only. Page bodies, chart
annotations and every generated sentence from the pipeline are English. Proper localisation
means per-locale routes so each language is a real, indexable URL; that has not been done.
The `data-i18n` structure is in place so adding a language is a data change, not a markup
change.

**G14b — The calculator's formula exists twice.**
Once in Python (`transforms/personal_inflation.py`, unit-tested) and once in TypeScript in the
calculator island, because the inputs are the user's and cannot be precomputed. The TS copy
runs a known-answer self-check on load and refuses to show results if it fails, so drift would
be visible — but a cross-language test would be better than a runtime guard.

**G14c — Charts render client-side.**
They are drawn by Observable Plot in the browser, ~98 KB gzipped, shared and deferred. A
build-time SVG render would ship less JavaScript and would work with JS disabled. The charts
do have text alternatives that a screen reader gets, so the *information* is available without
JS even though the graphics are not.

**G14d — No automated check that charts actually draw.**
CI verifies every chart island received non-empty props, but "props present" is not "SVG
rendered". Two charts were silently failing during development and only a manual headless-Chrome
DOM inspection caught them. A browser-based smoke test belongs in CI.

**G14e — Not deployed.**
The Cloudflare Pages project does not exist yet, so there is no live URL. Everything is
verified against a local preview build.

**G15 — PriceCatcher raw is ~130 MB of downloads per full CI run.**
Reduced to a 4.5 MB aggregate, but the scheduled job is not fast.

## Things deliberately not done

**G16 — No imputation of any kind.** Not for missing months, not for survey gaps, not for
gaps in the wage series. A chart that cannot be drawn honestly at a given resolution drops
to a coarser resolution instead.

**G17 — No causal claims.** We report that state real-income growth diverged while state
inflation did not. We do not claim to know why. Any explanation would need data we do not
have.
