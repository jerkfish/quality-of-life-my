# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Phase 1: data pipeline (`pipeline/`) — fetches DOSM parquet, validates it against declared
  contracts, and reduces it to the static artifacts under `data/`.
- Dataset registry covering 32 upstream datasets with tier, frequency, join keys and
  per-dataset notes (`pipeline/src/colcheck/registry.py`).
- Data contracts with error/warning severity: missing columns, unparsable dates, unexpected
  or missing states, CPI division domains, duplicate keys, null keys, non-numeric values.
- Transforms: CPI annual/ cumulative/YoY, real wages with base-year deflation, HIES
  household income, personal inflation calculator, PriceCatcher item-level reduction.
- Test suite: 95 offline tests plus 18 live upstream contract tests. Known-answer cases
  throughout; the transforms carry 92-100% coverage.
- Live contract test that asserts the CPI is still based at 2010 and that our deflation
  still reproduces DOSM's own published real-wage series.
- Scheduled GitHub Actions workflow: lint, format check, tests, upstream contract tests,
  artifact rebuild, commit-on-drift.
- `docs/DATA.md`, `docs/ASSUMPTIONS.md`, `docs/KNOWN-GAPS.md`.

### Notes
- The pipeline reads bulk parquet from `storage.dosm.gov.my`, not the REST API. The API is
  rate limited to 4 requests/minute and is missing five datasets we need entirely.
- Nothing is published until every validation passes; a failed run writes no artifacts.

### Added — Phase 0 (discovery)
- Confirmed API mechanics, response shapes and rate limits against the live service rather
  than the documentation.
- Established that CPI is based at 2010=100 and that the state wage series starts in the same
  year, so no rebasing is needed anywhere in the project.
- Validated the deflation method against DOSM's published real-wage series (max 0.77 RM).
- Documented three findings the data supports, with caveats.

[Unreleased]: https://github.com/jerkfish/quality-of-life-my/commits/HEAD
