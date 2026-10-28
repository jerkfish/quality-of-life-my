"""Phase 0: test the candidate headline insights on the real data."""

import os

import pandas as pd

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 100)

# --- CPI by state, annual mean of the monthly index, division 'overall' -------
cpi = pd.read_parquet(f"{RAW}/cpi_state.parquet")
cpi["date"] = pd.to_datetime(cpi["date"])
cpi["year"] = cpi["date"].dt.year
cpi = cpi[cpi["division"] == "overall"]
cpi_ann = cpi.groupby(["state", "year"])["index"].mean().rename("cpi").reset_index()

# --- Nominal wages by state (annual, both sexes) ------------------------------
w = pd.read_parquet(f"{RAW}/wages_state.parquet")
w["date"] = pd.to_datetime(w["date"])
w["year"] = w["date"].dt.year
w = w[(w["sex"] == "both") & (w["state"] != "Malaysia")]
w = w[["state", "year", "wages_median", "wages_mean"]]

df = cpi_ann.merge(w, on=["state", "year"], how="inner").sort_values(["state", "year"])

base_cpi = df[df["year"] == 2010].set_index("state")["cpi"]
base_nom = df[df["year"] == 2010].set_index("state")["wages_median"]
last = df[df["year"] == 2025].set_index("state")

out = pd.DataFrame(
    {
        "cpi_2010": base_cpi,
        "cpi_2025": last["cpi"],
        "cum_inflation_pct": (last["cpi"] / base_cpi - 1) * 100,
        "nom_med_2010": base_nom,
        "nom_med_2025": last["wages_median"],
        "nom_growth_pct": (last["wages_median"] / base_nom - 1) * 100,
        "real_med_2025_rm2010": last["wages_median"] / (last["cpi"] / base_cpi),
        "real_growth_pct": (last["wages_median"] / (last["cpi"] / base_cpi)) / base_nom * 100 - 100,
    }
)
out["gap_pp"] = out["nom_growth_pct"] - out["cum_inflation_pct"]
out = out.round(1).sort_values("gap_pp")

print("=" * 120)
print("2010 -> 2025: cumulative CPI inflation vs nominal median wage growth, by state")
print("=" * 120)
print(out[["cum_inflation_pct", "nom_growth_pct", "real_growth_pct", "gap_pp"]].to_string())

print()
print(f"Malaysia total: median nominal wage growth {out['nom_growth_pct'].mean():.1f}% avg, "
      f"inflation {out['cum_inflation_pct'].mean():.1f}% avg")
print(f"States with NEGATIVE real wage growth: {(out['real_growth_pct'] < 0).sum()} / {len(out)}")

# --- Cross-check against DOSM's own real series -------------------------------
print()
print("=" * 120)
print("CROSS-CHECK: my computed real median vs DOSM published real median (2010 prices)")
print("=" * 120)
real = pd.read_parquet(f"{RAW}/wages_real_state.parquet")
real["date"] = pd.to_datetime(real["date"])
real["year"] = real["date"].dt.year
r25 = real[real["year"] == 2025].set_index("state")["wages_median_real"]
chk = pd.DataFrame({"mine": out["real_med_2025_rm2010"], "dosm": r25}).dropna()
chk["diff"] = (chk["mine"] - chk["dosm"]).round(2)
print(chk.round(1).to_string())
print(f"max abs diff = {chk['diff'].abs().max():.2f} RM")

# --- National long series -----------------------------------------------------
print()
print("=" * 120)
print("National CPI (base 2010=100) annual means, selected years")
print("=" * 120)
nat = pd.read_parquet(f"{RAW}/cpi_headline.parquet")
nat["date"] = pd.to_datetime(nat["date"])
nat["year"] = nat["date"].dt.year
nat = nat[nat["division"] == "overall"].groupby("year")["index"].mean()
print(nat.loc[[1980, 1990, 2000, 2010, 2020, 2024, 2025]].round(1).to_string())

# --- Division-level inflation (for personal inflation calculator) -------------
print()
print("=" * 120)
print("Latest yoy inflation by division (national), 2026-09")
print("=" * 120)
inf = pd.read_parquet(f"{RAW}/cpi_headline_inflation.parquet")
inf["date"] = pd.to_datetime(inf["date"])
latest = inf[inf["date"] == inf["date"].max()][["division", "inflation_yoy", "inflation_mom"]]
mc = pd.read_parquet(f"{RAW}/mcoicop.parquet")
names = mc[mc["digits"] == 2].set_index("division")["desc_en"]
latest = latest.assign(name=latest["division"].map(names)).sort_values("inflation_yoy", ascending=False)
print(latest.to_string(index=False))
