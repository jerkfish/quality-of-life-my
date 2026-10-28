"""PriceCatcher reduction.

PriceCatcher is ~1.66 MB per monthly edition, 58 editions, well over a million price
records a month. Committing that raw would be ~96 MB of churn in a portfolio repo, so we
reduce it at ingest to an item x state x month aggregate and ship only that.

**DOSM's own caveat, which we respect:** "This data is collected for the purpose of price
surveillance... Inflation analysis should be conducted using DOSM's CPI data. Therefore,
PriceCatcher data should be used as a complement rather than a replacement to CPI data."

So nothing here feeds an inflation figure. This powers an item-level panel -- "what does a
kilo of chicken actually cost near you, and how has that moved" -- and the UI says so.
"""

from __future__ import annotations

import pandas as pd

# PriceCatcher uses -1 as a sentinel code for unresolved rows. It has no item name and no
# unit, so it cannot be aggregated meaningfully.
SENTINEL_CODES = {-1}


def clean_items(items: pd.DataFrame) -> pd.DataFrame:
    """Item lookup, minus sentinel rows and with stable dtypes."""
    out = items.copy()
    out["item_code"] = pd.to_numeric(out["item_code"], errors="coerce")
    out = out.dropna(subset=["item_code", "item"])
    out = out[~out["item_code"].isin(SENTINEL_CODES)].copy()
    out["item_code"] = out["item_code"].astype("int64")
    return out.drop_duplicates(subset=["item_code"]).reset_index(drop=True)


def clean_premises(premises: pd.DataFrame) -> pd.DataFrame:
    """Premise lookup: we only need the code -> state mapping, and it must not be null."""
    out = premises.copy()
    out["premise_code"] = pd.to_numeric(out["premise_code"], errors="coerce")
    out = out.dropna(subset=["premise_code", "state"])
    out["premise_code"] = out["premise_code"].astype("int64")
    return out.drop_duplicates(subset=["premise_code"]).reset_index(drop=True)


def aggregate_edition(
    prices: pd.DataFrame,
    items: pd.DataFrame,
    premises: pd.DataFrame,
) -> pd.DataFrame:
    """Reduce one monthly edition to item x state x month statistics.

    The unit of the result is (item_code, state, month). Prices within a month and a state
    are summarised by median, because PriceCatcher's raw rows are individual premise
    observations and the distribution has long tails (a hypermarket and a wet market are
    not the same shop).
    """
    px = prices.copy()
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    px = px.dropna(subset=["date", "price", "item_code", "premise_code"])
    px["item_code"] = pd.to_numeric(px["item_code"], errors="coerce")
    px["premise_code"] = pd.to_numeric(px["premise_code"], errors="coerce")
    px = px.dropna(subset=["item_code", "premise_code"])
    px["item_code"] = px["item_code"].astype("int64")
    px["premise_code"] = px["premise_code"].astype("int64")
    px = px[~px["item_code"].isin(SENTINEL_CODES)]

    px = px.merge(premises[["premise_code", "state"]], on="premise_code", how="left")
    px = px.merge(
        items[["item_code", "item", "unit", "item_group", "item_category"]],
        on="item_code",
        how="left",
    )
    px["month"] = px["date"].dt.to_period("M").astype(str)

    return (
        px.groupby(
            ["item_code", "item", "unit", "item_group", "item_category", "state", "month"],
            dropna=False,
        )  # fmt: skip
        .agg(
            median_price=("price", "median"),
            mean_price=("price", "mean"),
            min_price=("price", "min"),
            max_price=("price", "max"),
            n_obs=("price", "size"),
        )
        .reset_index()
    )


def national_item_series(aggregate: pd.DataFrame) -> pd.DataFrame:
    """Collapse the state dimension to give one national monthly series per item.

    Weighted by observation count, so a state with three price points does not count as much
    as one with three hundred.
    """
    df = aggregate.copy()
    df["_w"] = df["median_price"] * df["n_obs"]
    grouped = df.groupby(["item_code", "item", "unit", "item_group", "item_category", "month"],
                         dropna=False)  # fmt: skip
    out = grouped.agg(
        weighted_sum=("_w", "sum"),
        n_obs=("n_obs", "sum"),
        n_states=("state", "nunique"),
        min_price=("min_price", "min"),
        max_price=("max_price", "max"),
    ).reset_index()
    out["median_price"] = out["weighted_sum"] / out["n_obs"].replace(0, pd.NA)
    out = out.drop(columns=["weighted_sum"])
    # A national figure built from a single state is not national. Flag the thin ones.
    out["thin_coverage"] = out["n_states"] < 5
    return out


def item_change_summary(
    aggregate: pd.DataFrame, *, start_month: str, end_month: str
) -> pd.DataFrame:
    """Per-item cumulative price change between two months, nationally weighted."""
    nat = national_item_series(aggregate)
    a = nat[nat["month"] == start_month].set_index("item_code")
    b = nat[nat["month"] == end_month].set_index("item_code")
    common = a.index.intersection(b.index)
    if len(common) == 0:
        raise ValueError(f"No items present in both {start_month} and {end_month}.")

    span_months = (pd.Period(end_month, "M") - pd.Period(start_month, "M")).n
    out = pd.DataFrame(
        {
            "item": b.loc[common, "item"],
            "unit": b.loc[common, "unit"],
            "item_group": b.loc[common, "item_group"],
            "start_price": a.loc[common, "median_price"].astype(float),
            "end_price": b.loc[common, "median_price"].astype(float),
        }
    )
    out["change_pct"] = (out["end_price"] / out["start_price"] - 1.0) * 100.0
    # Annualised, so items observed over different spans are comparable. Guard against a
    # zero-length span, which would make this a division by zero rather than a statistic.
    out["change_pct_annualised"] = (
        ((out["end_price"] / out["start_price"]) ** (12.0 / span_months) - 1.0) * 100.0
        if span_months > 0
        else float("nan")
    )
    return out.sort_values("change_pct", ascending=False).reset_index()
