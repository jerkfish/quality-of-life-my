"""Build the compact data bundle the Worker carries.

**Why bundle rather than put the artifacts in R2?** Two reasons, in order of weight:

1.  The R2 credentials supplied for this project are scoped to a different bucket
    (``exampapers``) and cannot create ``colcheck-data``. So R2 is not available today
    regardless of whether it would be a good idea.
2.  At this size it genuinely would not be. The bundle below is a few hundred kilobytes
    before compression. Shipping it inside the Worker means no network hop on every tool
    call, no second system to keep in sync, and no cold-start fetch. R2 earns its place when
    the data outgrows the Worker bundle limit — which for the full PriceCatcher item history
    it already has, which is exactly why that part is trimmed here rather than uploaded whole.

The trim matters more than the transport. ``data/`` holds ~1.7 MB of JSON, most of it
per-state CPI divisions across 200 months. The agent does not need that matrix: it needs
national series over time, state-level aggregates over time, and state-by-division as a
snapshot. This module produces that, and records what it dropped so the difference is
inspectable rather than silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import registry

# Divisions the agent can be asked about, in the order the site presents them.
DIVISION_CODES: tuple[str, ...] = (*registry.CPI_DIVISIONS, "overall")


def _read(data_dir: Path, name: str) -> dict[str, Any] | None:
    path = data_dir / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _round(values: list[Any], digits: int = 3) -> list[Any]:
    out = []
    for v in values:
        if v is None:
            out.append(None)
        elif isinstance(v, (int, float)):
            out.append(round(float(v), digits))
        else:
            out.append(v)
    return out


def _annual_mean(periods: list[str], values: list[Any]) -> dict[str, float]:
    """Calendar-year mean, ignoring nulls. Used for static snapshots."""
    buckets: dict[str, list[float]] = {}
    for period, value in zip(periods, values, strict=False):
        if value is None or not isinstance(value, (int, float)):
            continue
        buckets.setdefault(period[:4], []).append(float(value))
    return {year: round(sum(v) / len(v), 3) for year, v in buckets.items() if v}


def build_bundle(data_dir: Path) -> dict[str, Any]:
    """Assemble the agent's data bundle from the committed artifacts."""
    manifest = _read(data_dir, "manifest.json") or {}
    national = _read(data_dir, "cpi_national.json")
    state = _read(data_dir, "cpi_state.json")
    groups = _read(data_dir, "cpi_groups.json")
    wages = _read(data_dir, "real_wages.json")
    income = _read(data_dir, "household_income.json")
    regional = _read(data_dir, "regional.json")
    forecast = _read(data_dir, "forecast.json")
    anomalies = _read(data_dir, "anomalies.json")
    pricecatcher = _read(data_dir, "pricecatcher/items.json")

    if national is None or state is None:
        raise FileNotFoundError(
            "cpi_national.json and cpi_state.json are required. "
            "Run `uv run colcheck run` before bundling."
        )

    dropped: list[str] = []

    # --- CPI, national, every division, full monthly history ------------------
    nat_periods = national["periods"]
    nat_series = {code: _round(national["series"].get(code, [])) for code in DIVISION_CODES}

    # --- CPI, state x division -------------------------------------------------
    # Full history for every state x division is ~45k numbers and 300 KB. The agent needs:
    #   (a) each state's overall index over time, for compare_states
    #   (b) each state's division index as a recent snapshot, for "what is housing inflation
    #       in Sabah"
    # So (a) is kept in full and (b) is reduced to annual means.
    state_overall: dict[str, list[Any]] = {}
    state_by_division: dict[str, dict[str, dict[str, float]]] = {}
    for state_name in state["meta"]["states"]:
        overall = state["series"].get(f"{state_name}|overall")
        if overall is None:
            continue
        state_overall[state_name] = _round(overall)

        per_div: dict[str, dict[str, float]] = {}
        for code in DIVISION_CODES:
            values = state["series"].get(f"{state_name}|{code}")
            if values is None:
                continue
            per_div[code] = _annual_mean(state["periods"], values)
        state_by_division[state_name] = per_div

    dropped.append(
        "cpi_state: monthly state x division arrays reduced to per-state overall history "
        "plus annual-mean snapshots per division."
    )

    # --- 3-digit groups (food away vs food at home and friends) ---------------
    group_names = (groups or {}).get("meta", {}).get("group_names", {})
    group_series = {
        code: _round(values) for code, values in (groups or {}).get("series", {}).items()
    }

    # --- Wages and income ------------------------------------------------------
    bundle: dict[str, Any] = {
        "generated_at": manifest.get("generated_at"),
        "pipeline_version": manifest.get("pipeline_version"),
        "source": "Department of Statistics Malaysia (DOSM), CC BY 4.0",
        "caveats": {
            "wages": (wages or {}).get("caveats", []),
            "income": (income or {}).get("caveats", []),
            "pricecatcher": (pricecatcher or {}).get("meta", {}).get("caveat", ""),
        },
        "as_of": {
            ds_id: {
                "as_of": entry.get("as_of"),
                "last_updated": entry.get("last_updated"),
                "tier": entry.get("tier"),
            }
            for ds_id, entry in (manifest.get("datasets") or {}).items()
        },
        "periods": {"national": nat_periods, "state": state["periods"]},
        "states": state["meta"]["states"],
        "base_year": national["meta"].get("base_year", 2010),
        "divisions": {
            k: v
            for k, v in national["meta"].get("division_names", {}).items()
            if k in DIVISION_CODES
        },
        "cpi": {
            "national": nat_series,
            "state_overall": state_overall,
            "state_by_division": state_by_division,
        },
        "groups": {"periods": (groups or {}).get("periods", []), "series": group_series, "names": group_names},
        "wages": {
            "national": (wages or {}).get("national", []),
            "by_state": (wages or {}).get("series", []),
            "summary": (wages or {}).get("summary", []),
            "meta": {
                "period": (wages or {}).get("meta", {}).get("period"),
                "population": (wages or {}).get("meta", {}).get("population"),
                "as_of": (wages or {}).get("meta", {}).get("as_of"),
            },
        },
        "income": {
            "national": (income or {}).get("national", []),
            "by_state": (income or {}).get("series", []),
            "summary": (income or {}).get("summary", []),
            "survey_years": (income or {}).get("meta", {}).get("survey_years", []),
            "meta": {
                "period": (income or {}).get("meta", {}).get("period"),
                "as_of": (income or {}).get("meta", {}).get("as_of"),
            },
        },
        "regional": {
            "wages_ranking": (regional or {}).get("wages_ranking", []),
            "income_ranking": (regional or {}).get("income_ranking", []),
            "spreads": (regional or {}).get("spreads", {}),
            "periods": {
                "wages": (regional or {}).get("meta", {}).get("wages_period"),
                "income": (regional or {}).get("meta", {}).get("income_period"),
            },
        },
    }

    # --- Forecast --------------------------------------------------------------
    if forecast:
        bundle["forecast"] = {
            "meta": {
                "baseline": forecast["meta"].get("baseline"),
                "scored_on": forecast["meta"].get("scored_on"),
                "as_of": forecast["meta"].get("as_of"),
            },
            "series": [
                {
                    "series_id": s["series_id"],
                    "label": s["label"],
                    "method": s["method"],
                    "horizon": s["horizon"],
                    "last_observed_period": s["last_observed_period"],
                    "last_observed_index": s["last_observed_index"],
                    "beats_baseline": s["beats_baseline"],
                    "verdict": s["verdict"],
                    "scores": s["scores"],
                    "points": s["points"],
                }
                for s in forecast["series"]
            ],
        }
    else:
        dropped.append("forecast.json absent; forecasting questions will be refused.")

    # --- Anomalies: headline only ---------------------------------------------
    if anomalies:
        headline = next(
            (s for s in anomalies["series"] if s["series_id"] == "cpi_headline:overall"), None
        )
        bundle["anomalies"] = {
            "threshold_z": (headline or {}).get("threshold_z"),
            "anomalies": (headline or {}).get("anomalies", []),
            "regimes": (headline or {}).get("regimes", []),
            "context": (headline or {}).get("context", {}),
        }
        if len(anomalies["series"]) > 1:
            dropped.append(
                f"anomalies: {len(anomalies['series']) - 1} division-level series omitted; "
                "headline kept."
            )

    # --- PriceCatcher: metadata and latest month only -------------------------
    if pricecatcher:
        latest = pricecatcher.get("latest_by_state", {})
        items = [
            {
                "code": i["item_code"],
                "item": i["item"],
                "unit": i["unit"],
                "group": i["item_group"],
                "category": i["item_category"],
                "prices": latest.get("prices", {}).get(str(i["item_code"]), {}),
            }
            for i in pricecatcher.get("items", [])
            if str(i["item_code"]) in latest.get("prices", {})
        ]
        bundle["pricecatcher"] = {
            "month": latest.get("month"),
            "n_items": len(items),
            "items": items,
        }
        dropped.append(
            f"pricecatcher: monthly item history omitted ({len(pricecatcher.get('series', {}))} "
            "series); latest-month state prices kept."
        )
    else:
        dropped.append("pricecatcher absent; item-price questions will be refused.")

    bundle["dropped_for_size"] = dropped
    return bundle


def write_bundle(data_dir: Path, out_path: Path) -> tuple[int, int]:
    """Write the bundle. Returns (uncompressed bytes, dropped-note count)."""
    bundle = build_bundle(data_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Compact separators: this file is machine-read, and whitespace is pure bundle weight.
    text = json.dumps(bundle, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    out_path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8")), len(bundle.get("dropped_for_size", []))
