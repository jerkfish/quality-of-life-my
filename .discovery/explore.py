"""Phase 0 exploration: verify coverage and test whether the candidate insights hold."""

import glob
import os

import pandas as pd

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)


def load(name):
    return pd.read_parquet(os.path.join(RAW, f"{name}.parquet"))


def coverage(df, name):
    d = pd.to_datetime(df["date"])
    print(f"  {name:26} rows={len(df):>7,}  {d.min().date()} -> {d.max().date()}")


print("=" * 78)
print("COVERAGE")
print("=" * 78)
for n in [
    "cpi_headline", "cpi_state", "cpi_headline_inflation", "cpi_state_inflation",
    "cpi_core", "cpi_strata", "cpi_lowincome", "cpi_3d",
    "wages_state", "wages_real_state", "formal_wages_state", "formal_wages",
    "hies_state", "hies_malaysia_percentile", "hh_income_state",
    "lfs_month", "lfs_qtr_state", "population_state", "fuelprice",
]:
    coverage(load(n), n)

print()
print("=" * 78)
print("DIMENSIONS")
print("=" * 78)
cpi_state = load("cpi_state")
print("cpi_state states:", sorted(cpi_state["state"].unique()))
print("cpi_state divisions:", sorted(cpi_state["division"].unique()))
print("cpi_headline divisions:", sorted(load("cpi_headline")["division"].unique()))
print("wages_state states:", sorted(load("wages_state")["state"].unique()))
print("wages_state sexes:", sorted(load("wages_state")["sex"].dropna().unique()))
print("wages_state years:", sorted(pd.to_datetime(load("wages_state")["date"]).dt.year.unique()))
print("hies_state years:", sorted(pd.to_datetime(load("hies_state")["date"]).dt.year.unique()))
fw = load("formal_wages_state")
print("formal_wages_state states:", sorted(fw["state"].unique()))
print()

print("=" * 78)
print("MCOICOP divisions (2-digit)")
print("=" * 78)
mc = load("mcoicop")
print(mc[mc["digits"] == 2][["division", "desc_en", "desc_bm"]].to_string(index=False))
