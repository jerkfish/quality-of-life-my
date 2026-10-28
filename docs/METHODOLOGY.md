# METHODOLOGY.md

Every formula, every assumption, and every place where a defensible person could reasonably
have chosen differently. Written so the numbers can be checked rather than taken on trust.

---

## 1. Real income

### The deflation identity

All real-terms figures in this project are CPI-deflated:

```
real(t) = nominal(t) / ( CPI(t) / CPI(base) )
```

Because DOSM bases the CPI at **2010 = 100** and the state wage series also begins in 2010,
`CPI(base) = 100` and no rebasing or splicing is needed anywhere. That is the single most
important reason the project is feasible, and it is asserted by a test
(`test_cpi_base_year_is_still_2010`) so that a DOSM rebase fails CI instead of silently
corrupting every real-terms number.

### Annual aggregation

CPI is monthly; wages and HIES are annual or survey-year. We deflate an annual figure by the
**mean of the 12 monthly index values** in that calendar year.

This is not an arbitrary choice — it reproduces DOSM's own published real-wage series to
within **0.77 RM** across all 16 states (max difference on the mean: 0.94 RM; the residual is
rounding, since DOSM publishes whole ringgit). The comparison is a test.

Years with fewer than 12 observations are flagged `is_partial` and labelled as such. 2026
currently has 9 months of CPI; its annual mean is real but is not comparable with a full year.

### Growth over a period

Growth between two years is the **ratio of growth factors**, never a subtraction:

```
real_growth = ( nominal(t1) / nominal(t0) ) / ( CPI(t1) / CPI(t0) ) - 1
```

Subtracting inflation from nominal growth is a common shortcut that drifts over long
horizons. Over 2010→2025 the two agree to within a few tenths of a percentage point; over a
longer window they diverge, and the ratio is the correct one either way.

### Two income sources, deliberately both reported

| Source | What it measures | Population | Frequency |
| --- | --- | --- | --- |
| `wages_state` | individual wage, employer survey | Malaysian **citizens** employed | annual 2010→2025 |
| `hh_income_state` | household income, household survey (HIES) | all Malaysian households | survey years only |

These are **not interchangeable** and are never plotted on a shared axis. They are shown side
by side because they agree on the *shape* of the answer — real incomes rose, and the spread
across states is wide — which is stronger evidence than either alone.

### The population trap

Three DOSM files have near-identical names and different populations:

| File | Population |
| --- | --- |
| `wages_state` | Malaysian **citizens** |
| `wages_real_state_citizen` | Malaysian **citizens** ← matches `wages_state` |
| `wages_real_state` | **all** employees, citizens and non-citizens |

Comparing `wages_state` against `wages_real_state` measures a citizenship-composition change
and reports it as inflation — in Sabah in 2025 the two differ by about 24%. We always compare
citizen with citizen, and `test_validation_catches_the_wrong_population_series` fails if
anyone changes that.

### Caveat that cannot be fixed

The wage median is the median of *employees*. It moves when the mix of employees changes,
independently of anyone's pay. This cannot be corrected without microdata we do not have. It
is stated on every wage chart.

---

## 2. Personal inflation

### Formula

Given a spending split `w_i` across CPI divisions and division-level inflation rates `r_i`:

```
personal_inflation = Σ ( w_i / Σw ) · r_i
```

Weights are normalised, so users can enter ringgit amounts rather than percentages.

The reported **gap** is `personal_inflation − headline_inflation`, in percentage points.

### Why the basket comes from the user

DOSM does not publish CPI expenditure weights as open data. We searched the full catalogue;
the only "weight" hits are child-nutrition datasets. The official weights live in the CPI
Technical Notes PDF, which the dataset metadata cites but which is not machine-readable.

So the calculator takes the user's own split as input and ships **no** default basket
pretending to be the official one. An illustrative preset, if ever added, would be labelled
as illustrative and would not be presented as a DOSM weight.

### Missing divisions are excluded, never zeroed

If the user's basket names a division we cannot price, it is reported in `missing_divisions`
and removed from the normalisation. Treating it as 0% inflation would silently understate the
result, which is the more dangerous error. Below 50% priced weight the result carries an
explicit "not reliable" caveat.

---

## 3. Regional divergence

Two spreads are computed on matched periods and shown together:

- **cumulative inflation per state** — `CPI(state, end) / CPI(state, start) − 1`
- **real income growth per state** — the ratio formula from §1

Headline result: inflation is far more uniform across states (17.6 pp spread over 2010→2025)
than real income growth (62.1 pp). Both periods are reported because the two sources start in
different years: 2010→2025 for wages, 2012→2024 for HIES. The result holds in both.

**No causal claim is made.** We report that the divergence is on the income side rather than
the price side. Explaining *why* would require data we do not have.

---

## 4. Forecasting

### What is modelled, and what is scored

We model the **index level** — it has clean trend and seasonal structure, which is what SARIMA
and ETS are built for — but we **score the implied year-on-year inflation rate** in percentage
points.

Scoring on the index would flatter every model, because the index only goes up: a model with
a 0.5-point MAE on a series sitting at 130 has demonstrated almost nothing. Percentage errors
on a level near 130 are similarly uninformative. The rate is the quantity the site actually
makes a claim about, so it is the quantity we measure.

### The baseline

```
level(t) = level(t-12) · ( 1 + yoy(t-12) )
```

i.e. "inflation stays what it was a year ago".

**Why not textbook seasonal-naive (`level(t) = level(t-12)`)?** Under year-on-year scoring
that model predicts **exactly 0.0% inflation at every horizon, by construction** — it is an
identity, not a forecast. Our first implementation used it, and the tell was a MAPE of exactly
100.00%: the predicted rate was always zero, so the denominator was always the floor. Scoring
a real model against a straw man that says "inflation is always zero" would prove nothing.
Persistence of the inflation rate is the benchmark that actually has to be beaten, and for
CPI it is a hard one.

### Models

| Model | Specification |
| --- | --- |
| `inflation_persistence` | the baseline above |
| `ets` | Holt-Winters, additive trend + additive seasonality, period 12, estimated init |
| `sarima` | SARIMAX(1,1,1)(1,1,1,12), `enforce_stationarity=False`, `enforce_invertibility=False` |

SARIMA parameters are fixed rather than auto-searched so the result is reproducible and can be
stated here. We are not trying to win a competition; we are trying to find out whether a
standard model beats a hard baseline, and to be able to say why either way.

### Backtesting

**Rolling origin, expanding window.** For each fold, the model is fitted only on data
available at that origin and forecasts the next 12 months. Nothing is shuffled; nothing sees
the future. `test_rolling_origin_never_trains_on_the_future` asserts this.

Twelve folds by default (eight in the full pipeline, for runtime). Metrics:

```
MAE  = mean( |predicted_rate − actual_rate| )                    percentage points
RMSE = sqrt( mean( (predicted_rate − actual_rate)² ) )           percentage points
MAPE = mean( |error| / max(|actual_rate|, 0.25) ) · 100          percent
```

The MAPE denominator is floored at 0.25 pp because the rate passes through zero (2020 saw
deflation) and an undefended MAPE would let one month dominate the score.

### Prediction intervals

80% intervals, reported **in both index and rate space**, because the site charts the rate
while the model works in levels. Handing the frontend only the level would force it to
re-derive the rate and get the base month wrong.

Interval **coverage** is reported alongside accuracy. A model with good MAE and 40% coverage
of its 80% interval is worse than one with slightly worse MAE and honest intervals.

### The honesty requirement

If the best model does not beat the baseline, the result carries
`beats_baseline: false`, a verdict saying so, and a caveat telling the reader to treat the
projection as a continuation of current trends rather than a forecast that has earned trust.
That state is in the data, not inferred from a missing field, so the UI cannot omit it by
accident. `test_forecast_series_says_so_when_nothing_beats_the_baseline` pins the behaviour.

### Why horizons stop at 12 months

At horizon h ≤ 12, the year-earlier index needed for a year-on-year rate is always inside the
training window, so the implied rate is exact. Beyond 12 months, the base would itself be a
forecast and the quoted interval would be dishonest. We return null rather than a number with
no valid interval.

---

## 5. Anomaly and regime detection

### Anomalies: STL residuals with a robust scale

Decompose the monthly index into trend, seasonal and remainder using STL with `robust=True`,
then score the remainder:

```
z(t) = ( resid(t) − median(resid) ) / σ_robust
```

`σ_robust = MAD(resid) · 1.4826`, flagging `|z| ≥ 3.5`.

**Why MAD and not standard deviation?** A single large shock inflates a standard deviation
enough to hide itself — exactly the failure mode we care about.

**MAD's own blind spot.** MAD is exactly zero whenever more than half the residuals are
identical. This is not hypothetical: STL fits a cleanly seasonal series almost exactly, and we
hit the case where most residuals sat at 1e-14 while one month carried a real spike. A
MAD-scaled detector announced "no anomalies" in the one case it most needed to catch. So when
MAD is degenerate we fall back to the mean absolute deviation about the median
(`· 1.2533`), and only if *both* are below the noise floor do we return zeros.

### Regimes: binary segmentation

A single weird month and a durable change of level are different questions. To find the
latter we run **binary segmentation** on the year-on-year rate: greedily find the split that
most reduces within-segment variance, repeat, with a 24-month minimum segment.

Deliberately simple. PELT or a Bayesian change-point model would find more nuanced breaks, but
this is explainable in one sentence, reproducible without a seed, and adequate for a series
with a handful of real regime shifts. It is a known limitation, not an oversight.

### Annotation policy

We flag what the data says and attach **no narrative**. Where a flag coincides with something
independently datable in another series we fetch (fuel prices, labour force), that coincidence
is reported as *corroboration*, never as a cause. The word "because" does not appear in any
generated annotation.

---

## 6. Data quality

- **No interpolation, anywhere.** Not for missing months, not for survey gaps, not for gaps
  in the wage series. Where a chart cannot be drawn honestly at monthly resolution it drops to
  a coarser resolution instead.
- **Gaps are data, not errors.** `cpi_headline` has 1,464 null index values — divisions that
  did not exist in 1980. `hh_income_state` has 11 null medians. These are carried through as
  gaps and surfaced, never filled.
- **Every artifact carries its own as-of date.** There is no single global "last updated":
  CPI refreshes monthly, formal wages monthly, state wages annually, HIES biennially. One
  badge covering all of them would be a lie.
- **Partial years are labelled.** A 9-month annual mean is marked `is_partial`.

---

## 7. What would change the conclusions

Written down so the assumptions are falsifiable rather than buried:

1. **A DOSM CPI rebase.** Would invalidate every real-terms figure. Caught by a test.
2. **A change to which population `wages_state` covers.** Caught by a test.
3. **If the employee/self-employed mix shifted sharply**, the wage medians move for reasons
   unrelated to pay. Not correctable, only caveated.
4. **If HIES stopped being a survey**, the gap-handling assumptions could be relaxed — a test
   asserts the gaps still exist so we notice rather than assume.
5. **If the inflation process changed regime structurally** (a new monetary policy framework,
   say), the forecast backtest would degrade. The verdict field reports this automatically
   rather than hiding it.
