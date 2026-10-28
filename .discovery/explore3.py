"""Phase 0: validate the deflation method and test the low-income / strata insights."""

import os

import pandas as pd

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
pd.set_option("display.width", 220)


def L(n):
    return pd.read_parquet(f"{RAW}/{n}.parquet")


# ---------- 1. Reproduce DOSM's published real wage series -------------------
cpi = L("cpi_state")
cpi["date"] = pd.to_datetime(cpi["date"])
cpi["year"] = cpi["date"].dt.year
cpi_ann = (
    cpi[cpi["division"] == "overall"].groupby(["state", "year"])["index"].mean().rename("cpi").reset_index()
)
w = L("wages_state")
w["date"] = pd.to_datetime(w["date"])
w["year"] = w["date"].dt.year
w = w[w["sex"] == "both"][["state", "year", "wages_median", "wages_mean"]]

df = cpi_ann.merge(w, on=["state", "year"], how="inner")
b = df[df["year"] == 2010].set_index("state")
df = df.assign(
    real_median=df["wages_median"] / (df["cpi"] / b["cpi"].reindex(df["state"]).values),
    real_mean=df["wages_mean"] / (df["cpi"] / b["cpi"].reindex(df["state"]).values),
)

pub = L("wages_real_state_citizen")
pub["date"] = pd.to_datetime(pub["date"])
pub["year"] = pub["date"].dt.year
mine = df[df["year"] == 2025].set_index("state")[["real_median", "real_mean"]]
ref = pub[pub["year"] == 2025].set_index("state")[["wages_median_real", "wages_mean_real"]]
chk = mine.join(ref).dropna()
chk["d_median"] = (chk["real_median"] - chk["wages_median_real"]).round(2)
chk["d_mean"] = (chk["real_mean"] - chk["wages_mean_real"]).round(2)

print("=" * 110)
print("VALIDATION: my deflation (nominal / CPI-div-2010-base) vs DOSM published citizen real series, 2025")
print("=" * 110)
print(chk.round(1).to_string())
print(f"max |d_median| = {chk['d_median'].abs().max():.2f} RM   max |d_mean| = {chk['d_mean'].abs().max():.2f} RM")

# ---------- 2. Low-income vs headline CPI ------------------------------------
print()
print("=" * 110)
print("INSIGHT TEST: does the low-income basket inflate faster than headline?")
print("=" * 110)
nat = L("cpi_headline")
nat["date"] = pd.to_datetime(nat["date"])
nat["year"] = nat["date"].dt.year
low = L("cpi_lowincome")
low["date"] = pd.to_datetime(low["date"])
low["year"] = low["date"].dt.year

n = nat[nat["division"] == "overall"].groupby("year")["index"].mean()
l = low[low["division"] == "overall"].groupby("year")["index"].mean()
cmp = pd.DataFrame({"headline": n, "lowincome": l}).dropna()
cmp["ratio"] = (cmp["lowincome"] / cmp["headline"]).round(3)
print(cmp.loc[[2010, 2015, 2019, 2020, 2022, 2024, 2025]].round(1).to_string())
print(f"cumulative 2010->2025: headline {n[2025] / n[2010] * 100 - 100:.1f}%  "
      f"lowincome {l[2025] / l[2010] * 100 - 100:.1f}%")

print()
print("By division, cumulative 2010->2025 (headline vs low-income), pp gap:")
g = (
    nat.groupby(["division", "year"])["index"].mean().unstack()
    .pipe(lambda d: (d[2025] / d[2010] - 1) * 100)
    .rename("headline")
)
gl = (
    low.groupby(["division", "year"])["index"].mean().unstack()
    .pipe(lambda d: (d[2025] / d[2010] - 1) * 100)
    .rename("lowincome")
)
mc = L("mcoicop")
names = mc[mc["digits"] == 2].set_index("division")["desc_en"]
gg = pd.concat([g, gl], axis=1).dropna()
gg["gap_pp"] = (gg["lowincome"] - gg["headline"]).round(1)
gg["name"] = gg.index.map(names)
print(gg.sort_values("gap_pp", ascending=False).round(1).to_string())

# ---------- 3. Urban vs rural --------------------------------------------------
print()
print("=" * 110)
print("Strata: urban vs rural cumulative CPI 2010->2025")
print("=" * 110)
st = L("cpi_strata")
st["date"] = pd.to_datetime(st["date"])
st["year"] = st["date"].dt.year
s = st[st["division"] == "overall"].groupby(["strata", "year"])["index"].mean().unstack()
print(((s[2025] / s[2010] - 1) * 100).round(1).to_string())
print(f"strata values: {sorted(st['strata'].unique())}")

# ---------- 4. Nominal wage sanity -------------------------------------------
print()
print("=" * 110)
print("Raw nominal median wage (citizens, RM/month), selected states")
print("=" * 110)
sel = df[df["state"].isin(["W.P. Labuan", "Sarawak", "Selangor", "W.P. Kuala Lumpur", "Sabah", "Malaysia"])]
piv = sel.pivot(index="year", columns="state", values="wages_median")
print(piv.loc[[2010, 2015, 2019, 2020, 2022, 2024, 2025]].to_string())
