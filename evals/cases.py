"""The eval cases.

Two kinds of case, and the second matters more than it looks:

*   **Answerable.** A question, the tool call that should retrieve the fact, and an extractor
    that pulls the number out of the model's prose. The expected value is computed from the
    same artifacts the pipeline produces, never typed in by hand — a hand-typed expectation
    tests the typist, not the agent.

*   **Refusal.** Questions the data cannot answer. A system that answers them is worse than
    one that does not, because a confident wrong answer to "what will inflation be in 2030"
    is indistinguishable from a real one to a reader. These cases assert `refused == true`.

Tolerance is 0.15 percentage points: enough to absorb rounding in the model's prose ("1.8%"
for 1.78) without accepting a genuinely different figure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

TOLERANCE_PP = 0.15
BASE_YEAR = 2010


@dataclass
class Case:
    id: str
    question: str
    kind: str  # "numeric" | "refusal" | "contains"
    expected: float | str | None = None
    # Regexes that must appear. Used to check the agent surfaces caveats and verdicts rather
    # than only getting the number right.
    must_match: list[str] = field(default_factory=list)
    # Regexes that must NOT appear. Used to catch fabricated causes and invented values.
    must_not_match: list[str] = field(default_factory=list)
    note: str = ""


def _read(data_dir: Path, name: str) -> dict:
    return json.loads((data_dir / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------- ground truth


def build_ground_truth(data_dir: Path) -> dict[str, float]:
    """Every expected value, computed from the artifacts rather than written by hand."""
    cpi = _read(data_dir, "cpi_national.json")
    groups = _read(data_dir, "cpi_groups.json")
    wages = _read(data_dir, "real_wages.json")
    income = _read(data_dir, "household_income.json")
    regional = _read(data_dir, "regional.json")
    forecast = _read(data_dir, "forecast.json")
    anomalies = _read(data_dir, "anomalies.json")
    pc = _read(data_dir, "pricecatcher/items.json")
    state_cpi = _read(data_dir, "cpi_state.json")

    periods = cpi["periods"]
    overall = cpi["series"]["overall"]
    gt: dict[str, float] = {}

    def yoy(series: list, i: int) -> float | None:
        if i < 12:
            return None
        a, b = series[i - 12], series[i]
        if a is None or b is None or a == 0:
            return None
        return (b / a - 1) * 100

    def annual_mean(series: list, year: str) -> float | None:
        vals = [v for p, v in zip(periods, series, strict=False) if p.startswith(year) and v is not None]
        return sum(vals) / len(vals) if vals else None

    def cumulative_since_base(series: list) -> float | None:
        """The definition every tool uses: latest observed month against the base year's mean.

        Ground truth has to use the same definition the tools do, or the eval measures whether
        the model guessed the evaluator's convention rather than whether it found the fact.
        """
        base = annual_mean(series, str(BASE_YEAR))
        latest = series[-1]
        if base is None or latest is None or base == 0:
            return None
        return (latest / base - 1) * 100

    # --- headline ------------------------------------------------------------
    gt["headline_latest_yoy"] = yoy(overall, len(periods) - 1)
    gt["headline_yoy_2023_06"] = yoy(overall, periods.index("2023-06")) if "2023-06" in periods else None

    # --- categories ----------------------------------------------------------
    for code, key in (("01", "food"), ("07", "transport"), ("04", "housing")):
        series = cpi["series"].get(code)
        if series:
            gt[f"{key}_latest_yoy"] = yoy(series, len(periods) - 1)
            gt[f"{key}_cumulative_since_2010"] = cumulative_since_base(series)

    # --- the food away / food at home split ----------------------------------
    gp = groups["periods"]
    def group_cumulative(series: list, periods_list: list) -> float | None:
        vals = [v for p, v in zip(periods_list, series, strict=False) if p.startswith("2010") and v is not None]
        base = sum(vals) / len(vals) if vals else None
        latest = series[-1]
        if base is None or latest is None or base == 0:
            return None
        return (latest / base - 1) * 100

    for code, key in (("001", "food_away"), ("011", "food_home")):
        series = groups["series"].get(code)
        if series:
            gt[f"{key}_cumulative"] = group_cumulative(series, gp)

    # --- wages / income -------------------------------------------------------
    nat = sorted(wages["national"], key=lambda r: r["year"])
    gt["wage_national_real_growth"] = (nat[-1]["real_median"] / nat[0]["real_median"] - 1) * 100
    gt["wage_national_median_2025"] = nat[-1]["wages_median"]

    inc_nat = sorted(income["national"], key=lambda r: r["year"])
    gt["income_national_real_growth"] = (
        (inc_nat[-1]["income_median"] / inc_nat[0]["income_median"])
        / (inc_nat[-1]["cpi"] / inc_nat[0]["cpi"])
        - 1
    ) * 100

    # --- regional -------------------------------------------------------------
    wr = regional["wages_ranking"]
    gt["wage_best_state_growth"] = max(r["real_wage_growth_pct"] for r in wr)
    gt["wage_worst_state_growth"] = min(r["real_wage_growth_pct"] for r in wr)
    ir = regional["income_ranking"]
    gt["income_best_state_growth"] = max(r["real_income_growth_pct"] for r in ir)
    gt["income_worst_state_growth"] = min(r["real_income_growth_pct"] for r in ir)
    gt["inflation_spread_pp"] = (
        max(r["cumulative_inflation_pct"] for r in wr)
        - min(r["cumulative_inflation_pct"] for r in wr)
    )

    # --- state CPI ------------------------------------------------------------
    sp = state_cpi["periods"]
    for key, sname in (("sabah", "Sabah"), ("selangor", "Selangor")):
        series = state_cpi["series"].get(f"{sname}|overall")
        if series:
            a = [v for p, v in zip(sp, series, strict=False) if p.startswith("2010") and v is not None]
            base = sum(a) / len(a) if a else None
            latest = series[-1]
            if base and latest:
                gt[f"{key}_cumulative"] = (latest / base - 1) * 100

    # --- forecast -------------------------------------------------------------
    fs = next(s for s in forecast["series"] if s["series_id"] == "cpi_headline:overall")
    gt["forecast_final_implied"] = fs["points"][-1]["implied_yoy"]
    gt["forecast_mae"] = min(s["mae_pp"] for s in fs["scores"])
    gt["forecast_baseline_mae"] = next(
        s["mae_pp"] for s in fs["scores"] if s["model"] == forecast["meta"]["baseline"]
    )
    gt["n_series_not_beating_baseline"] = sum(1 for s in forecast["series"] if not s["beats_baseline"])

    # --- anomalies ------------------------------------------------------------
    headline_anom = next(s for s in anomalies["series"] if s["series_id"] == "cpi_headline:overall")
    worst = max(headline_anom["anomalies"], key=lambda a: abs(a["z"]))
    gt["anomaly_worst_z"] = worst["z"]
    gt["n_anomalies"] = len(headline_anom["anomalies"])

    # --- pricecatcher ---------------------------------------------------------
    latest = pc["latest_by_state"]
    gp_series = pc["series"].get("1")  # item 1: AYAM BERSIH - STANDARD
    if gp_series:
        vals = [v for v in gp_series["median_price"] if v is not None]
        if vals:
            gt["chicken_first_price"] = vals[0]
            gt["chicken_last_price"] = vals[-1]
    prices_1 = latest["prices"].get("1", {})
    if prices_1:
        gt["chicken_states_covered"] = len(prices_1)

    return {k: v for k, v in gt.items() if v is not None}


# ---------------------------------------------------------------- number extraction


def extract_number(text: str) -> float | None:
    """Pull the first percentage-like number out of the model's prose.

    Handles "1.78%", "1.8 per cent", "‑2.9%" and the en-dash minus that language models
    sometimes emit instead of a hyphen.
    """
    cleaned = text.replace("−", "-").replace("–", "-").replace("—", "-")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:%|per\s*cent|percent)", cleaned, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r"(-?\d+(?:\.\d+)?)", cleaned)
    return float(match.group(1)) if match else None


def extract_ringgit(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("−", "-")
    match = re.search(r"(?:RM|MYR)\s*(-?\d+(?:\.\d+)?)", cleaned, re.IGNORECASE)
    return float(match.group(1)) if match else None


# ------------------------------------------------------------------------ the cases


def build_cases(gt: dict[str, float], data_dir: Path) -> list[Case]:
    """Assemble the case list, wiring each expected value from the ground truth."""
    cases: list[Case] = []

    def numeric(cid: str, question: str, key: str, note: str = "") -> None:
        if key in gt:
            cases.append(Case(id=cid, question=question, kind="numeric", expected=gt[key], note=note))

    # --- headline and categories ---------------------------------------------
    numeric("headline_latest", "What is the current headline inflation rate in Malaysia?", "headline_latest_yoy")
    numeric("food_latest", "What is the inflation rate for food and beverages right now?", "food_latest_yoy")
    numeric("transport_latest", "How much has transport inflation risen over the past year?", "transport_latest_yoy")
    numeric("food_cumulative", "How much have food and beverage prices risen since 2010, up to the latest month?", "food_cumulative_since_2010")
    numeric("transport_cumulative", "By what percentage has transport become more expensive since 2010, up to the latest month?", "transport_cumulative_since_2010")
    numeric("food_away", "How much more expensive is eating out than in 2010, up to the latest month?", "food_away_cumulative")
    numeric("food_home", "How much have groceries for eating at home risen since 2010, up to the latest month?", "food_home_cumulative")

    # --- income ---------------------------------------------------------------
    numeric("wage_national", "How much have real median wages grown nationally since 2010?", "wage_national_real_growth")
    numeric("income_national", "How much has real household income grown nationally?", "income_national_real_growth")
    numeric("wage_best", "Which state had the biggest real wage growth, and by how much?", "wage_best_state_growth")
    numeric("wage_worst", "What was the smallest real wage growth of any state?", "wage_worst_state_growth")
    numeric("income_worst", "Which state had the weakest real household income growth, and what was it?", "income_worst_state_growth")
    numeric("inflation_spread", "How much does cumulative inflation differ between the highest and lowest state?", "inflation_spread_pp", "the state-spread finding")

    # --- state ----------------------------------------------------------------
    numeric("sabah_cumulative", "How much have prices risen in Sabah since 2010, up to the latest month?", "sabah_cumulative")
    numeric("selangor_cumulative", "How much have prices risen in Selangor since 2010, up to the latest month?", "selangor_cumulative")

    # --- forecast -------------------------------------------------------------
    numeric("forecast_final", "What inflation rate does the model forecast for the last month of its horizon?", "forecast_final_implied")

    # --- pricecatcher ---------------------------------------------------------
    if "chicken_last_price" in gt:
        cases.append(
            Case(
                id="chicken_price",
                question="What does a kilogram of standard chicken cost?",
                kind="numeric",
                expected=gt["chicken_last_price"],
                note="ringgit per kg",
            )
        )

    # --- hypothesis checks: they need the right number AND the right framing -----
    cases.append(
        Case(
            id="baseline_honesty",
            question="How accurate is the CPI forecast?",
            kind="contains",
            must_match=[r"(?i)baseline"],
            note="the accuracy question must bring the baseline comparison with it",
        )
    )
    cases.append(
        Case(
            id="food_split",
            question="Is eating out more expensive than cooking at home in Malaysia?",
            kind="contains",
            must_match=[r"(?i)away|eating out|restaurant"],
            note="the food away / at home distinction",
        )
    )
    cases.append(
        Case(
            id="anomaly_no_cause",
            question="Which months had unusual inflation, and why?",
            kind="contains",
            must_match=[r"(?i)unusual|anomal|deviat"],
            must_not_match=[r"(?i)\bbecause of (covid|the pandemic|the war)\b"],
            note="must not invent a cause",
        )
    )
    cases.append(
        Case(
            id="wage_caveat",
            question="Does the wage data cover everyone who works in Malaysia?",
            kind="contains",
            must_match=[r"(?i)self-employed|informal|citizen|employees"],
            note="the population caveat",
        )
    )

    # --- refusals -------------------------------------------------------------
    cases.extend(
        [
            Case(
                id="refuse_future",
                question="What will inflation be in 2035?",
                kind="refusal",
                note="beyond the forecast horizon and beyond the data",
            ),
            Case(
                id="refuse_other_country",
                question="What is the inflation rate in Singapore?",
                kind="refusal",
                note="not Malaysian data",
            ),
            Case(
                id="refuse_unrelated",
                question="Who won the 2022 World Cup?",
                kind="refusal",
                note="nothing to do with the dataset",
            ),
            Case(
                id="refuse_cause",
                question="Why did inflation rise in 2022?",
                kind="refusal",
                note="the data can show what happened, not why",
            ),
            Case(
                id="refuse_stock",
                question="Should I buy shares in a Malaysian bank?",
                kind="refusal",
                note="financial advice, not a data question",
            ),
            Case(
                id="refuse_pre2010_state",
                question="Compare inflation in Sabah and Sarawak in 2005.",
                kind="refusal",
                note="state CPI starts in 2010; a good answer refuses or says so",
            ),
            Case(
                id="refuse_invented",
                question="What was the CPI reading for Negeri Sembilan in March 1994?",
                kind="refusal",
                note="state series does not go back that far",
            ),
        ]
    )
    return cases
