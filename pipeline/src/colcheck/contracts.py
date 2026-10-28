"""Data contracts: assert that what we downloaded is what we think we downloaded.

The point of this module is to turn silent wrongness into loud failure. Upstream can
revise a series, rename a column, change a base year, add a state, or start publishing a
different population -- and every one of those would corrupt the site without raising an
error anywhere. These checks are the tripwire.

`validate_frame` returns a `DatasetReport` rather than just raising, because the site needs
to *display* data confidence per chart, not merely crash. Errors raise; warnings are
carried through to the artifact so the UI can render them.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from .registry import CPI_DIVISIONS, CPI_OVERALL, MALAYSIA, STATES, Dataset

# Columns whose names are fixed across the DOSM datasets we use.
KNOWN_KEY_COLUMNS = frozenset(
    {"date", "state", "district", "parlimen", "dun", "division", "group", "class", "subclass",
     "sex", "strata", "sector", "occupation", "education", "ethnicity", "age", "series",
     "premise_code", "item_code", "band", "percentile", "variable", "digits", "series_type"}
)  # fmt: skip


class ContractError(Exception):
    """Upstream data no longer matches our declared contract."""


class Issue(BaseModel):
    level: Literal["error", "warning"]
    code: str
    message: str


class DatasetReport(BaseModel):
    """What we observed about a dataset, for the site's data-confidence layer."""

    dataset_id: str
    rows: int = 0
    date_min: str | None = None
    date_max: str | None = None
    entities: dict[str, int] = Field(default_factory=dict)
    duplicate_keys: int = 0
    null_counts: dict[str, int] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _allowed_states(ds: Dataset) -> set[str]:
    return {*STATES, MALAYSIA} if ds.allow_national else set(STATES)


def validate_frame(ds: Dataset, df: pd.DataFrame) -> DatasetReport:
    """Check `df` against the registry declaration for `ds`.

    Raises ContractError if any error-level issue is found. Returns the full report
    (including warnings) otherwise.
    """
    rep = DatasetReport(dataset_id=ds.id, rows=len(df))

    def err(code: str, msg: str) -> None:
        rep.issues.append(Issue(level="error", code=code, message=msg))

    def warn(code: str, msg: str) -> None:
        rep.issues.append(Issue(level="warning", code=code, message=msg))

    if df.empty:
        err("empty", "Dataset has zero rows.")
        raise ContractError(f"{ds.id}: {rep.errors[0].message}")

    # --- columns -------------------------------------------------------------
    required = {c for c in (ds.date_col, *ds.entity_cols, *ds.value_cols) if c}
    missing = sorted(required - set(df.columns))
    if missing:
        err(
            "missing_columns",
            f"Missing required columns {missing}. Upstream columns are {sorted(df.columns)}.",
        )

    # --- date ----------------------------------------------------------------
    if ds.date_col and ds.date_col in df.columns:
        raw = df[ds.date_col]
        parsed = pd.to_datetime(raw, errors="coerce")
        bad = int(parsed.isna().sum())
        if bad:
            err("unparsable_dates", f"{bad} row(s) have an unparsable {ds.date_col}.")
        else:
            rep.date_min = str(parsed.min().date())
            rep.date_max = str(parsed.max().date())

    # --- entity domains ------------------------------------------------------
    if "state" in df.columns:
        found = set(df["state"].dropna().unique())
        unexpected = found - _allowed_states(ds)
        if unexpected:
            err(
                "unexpected_states",
                f"Unexpected state value(s) {sorted(unexpected)}. "
                f"allow_national={ds.allow_national}.",
            )
        # A state silently vanishing would change every ranking, so we want to see it -- but
        # a legitimate upstream change (a new federal territory, say) should not hard-fail
        # the pipeline. Warning, surfaced in the manifest and on the site.
        missing_states = set(STATES) - found
        if missing_states:
            warn(
                "missing_states",
                f"{len(missing_states)} expected state(s) absent: {sorted(missing_states)}",
            )
        rep.entities["state"] = len(found)

    if ds.id.startswith("cpi_") and "division" in df.columns:
        found = set(df["division"].dropna().unique())
        allowed = {*CPI_DIVISIONS, CPI_OVERALL}
        unexpected = found - allowed
        if unexpected:
            err("unexpected_divisions", f"Unexpected CPI division code(s) {sorted(unexpected)}.")
        rep.entities["division"] = len(found)

    for col in ds.entity_cols:
        if col in df.columns and col != "state":
            rep.entities[col] = int(df[col].nunique(dropna=True))

    # --- duplicates ----------------------------------------------------------
    key_cols = [c for c in ds.resolved_key_cols if c in df.columns]
    if key_cols:
        dupes = int(df.duplicated(subset=key_cols).sum())
        rep.duplicate_keys = dupes
        if dupes:
            err(
                "duplicate_keys",
                f"{dupes} duplicate row(s) on key {key_cols}. A join on these would fan out.",
            )

    # --- nulls ---------------------------------------------------------------
    # Nulls in a key column are corruption: the row cannot be joined or attributed.
    for col in key_cols:
        n = int(df[col].isna().sum())
        if n:
            rep.null_counts[col] = n
            err("null_key", f"{n} null value(s) in key column {col!r}.")

    # Nulls elsewhere are upstream reality and are surfaced, not failed on. The only such
    # case today is the sentinel row in lookup_premise, which has no state -- it is
    # legitimately unattributable and must not be silently counted towards any state.
    for col in (c for c in ds.entity_cols if c and c in df.columns and c not in key_cols):
        n = int(df[col].isna().sum())
        if n:
            rep.null_counts[col] = n
            warn(
                "null_entity",
                f"{col!r} has {n} null value(s); these rows cannot be attributed to a group.",
            )

    # --- numeric values ------------------------------------------------------
    for col in ds.value_cols:
        if col not in df.columns:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            coerced = pd.to_numeric(df[col], errors="coerce")
            n_bad = int(coerced.isna().sum() - df[col].isna().sum())
            if n_bad:
                err("non_numeric", f"{col!r} has {n_bad} non-numeric value(s).")
        else:
            coerced = df[col]
        n_null = int(coerced.isna().sum())
        if n_null:
            rep.null_counts[col] = n_null
            # Genuine gaps exist upstream (inflation_yoy is null in the first year of the
            # series, for instance). Warn rather than fail -- but never interpolate.
            warn("null_values", f"{col!r} has {n_null} null value(s); carried through as gaps.")

    if not rep.ok:
        first = rep.errors[0]
        raise ContractError(
            f"{ds.id}: {len(rep.errors)} contract error(s); first is [{first.code}] {first.message}"
        )
    return rep


def empty_report(dataset_id: str) -> DatasetReport:
    return DatasetReport(dataset_id=dataset_id, rows=0)
