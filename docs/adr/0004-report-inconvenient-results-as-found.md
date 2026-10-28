# ADR 0004 — Report inconvenient results as found, and never interpolate

- **Status:** Accepted
- **Date:** 2026-10-28
- **Phase:** 0 (discovery) onward

## Context

This is a portfolio project. The natural gravity of a portfolio project is towards findings
that sound impressive, and towards smoothing over anything that makes a chart awkward.

Two specific temptations arose during discovery, and a third during the analysis phase:

1. **The low-income result.** The expected story is "inflation hurts the poor more". The data
   says low-income households' CPI tracked the headline rate almost exactly over 2010–2025
   (+35.0% vs +34.6%, a gap of **+0.4 pp**). Prolonged searching would probably have found a
   sub-period with a bigger gap.

2. **The HIES gaps.** Household income exists for 22 irregular survey years between 1970 and
   2024, with gaps of up to five years. A smooth line is much prettier than a stepped one.

3. **Forecast failures.** Divisions 03 (Clothing & Footwear) and 05 (Furnishings) genuinely
   fail to beat the persistence baseline. An ETS line drawn anyway would look authoritative
   and be worth nothing.

## Decision

**Findings that contradict the expected narrative are reported as found, and no series is ever
interpolated.**

Concretely:

- The +0.4 pp low-income result is a headline finding, stated plainly. It is presented
  alongside the *composition* difference, which is real and interesting (low-income inflation
  ran hotter on restaurants and tobacco, cooler on education and food) — but the aggregate
  result is not massaged into something more dramatic.
- HIES is plotted at survey resolution with visible gaps. No line is drawn between survey
  points that a reader could mistake for data.
- A forecast that does not beat the baseline carries `beats_baseline: false`, a verdict saying
  so, and a caveat telling the reader not to trust it. It is still shown — hiding it would be
  its own dishonesty — but it is labelled.
- Partial years (2026 currently has 9 of 12 months) are flagged `is_partial` rather than being
  averaged as if complete.
- Nulls are carried through as gaps and surfaced as warnings. `cpi_headline` has 1,464 of them.

## Consequences

**Good**

- The project's claims are defensible under scrutiny, which is the actual goal.
- A failed forecast is evidence about how forecastable CPI inflation is — which is a more
  interesting finding than a fabricated success.
- The data-confidence layer has something real to display, because gaps actually reach it.

**Bad**

- Some charts look less impressive than they could. This is the point.
- More work: every consumer has to handle gaps and partial years rather than assuming a
  complete rectangle of numbers.
- The site says "I don't know" in places where a confident-sounding answer was available.

## How it is enforced

- `test_forecast_series_says_so_when_nothing_beats_the_baseline` — the honest state is a tested
  code path, not a comment.
- `test_annual_means_flags_partial_years` — a short year cannot be silently averaged.
- `test_null_values_warn_and_are_carried_through` — upstream gaps reach the artifact as gaps.
- `test_hies_still_has_gaps_and_we_still_report_them` — asserts the gaps still exist, so a
  future "simplification" that fills them in is caught.

## Alternatives rejected

- **Pick the most dramatic period.** Finding 3 was originally computed over 2010→2025 because
  that is where the CPI and wage series overlap, not because it maximises the gap. Both
  available periods are reported for findings 1 and 2 precisely so the choice cannot be
  gamed.
- **Interpolate survey years.** Fabricates data. No.
- **Hide forecasts that lose to the baseline.** That is the opposite of the brief's explicit
  instruction.
