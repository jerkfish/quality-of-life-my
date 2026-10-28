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

**G10 — Forecast and anomaly detection are not built yet.**
Phases 2 and 3. The baseline-beats-model honesty requirement is planned but unproven.

**G11 — Real-wage validation tolerance is 2 RM.**
We reproduce DOSM's published real series to within 0.77 RM today. The tolerance is
deliberately loose enough to absorb rounding in the published figures; it is not zero.

## Engineering gaps

**G12 — `fetch.py` retry/backoff paths are untested.**
Coverage there is the lowest in the package. The happy path is exercised by the network
tests; `429` and `5xx` handling is not.

**G13 — `uv.lock` must be committed** for CI's `--frozen` install to be reproducible.

**G14 — No frontend, Worker, or evals yet.** Phases 3-4.

**G15 — PriceCatcher raw is ~130 MB of downloads per full CI run.**
Reduced to a 4.5 MB aggregate, but the scheduled job is not fast.

## Things deliberately not done

**G16 — No imputation of any kind.** Not for missing months, not for survey gaps, not for
gaps in the wage series. A chart that cannot be drawn honestly at a given resolution drops
to a coarser resolution instead.

**G17 — No causal claims.** We report that state real-income growth diverged while state
inflation did not. We do not claim to know why. Any explanation would need data we do not
have.
