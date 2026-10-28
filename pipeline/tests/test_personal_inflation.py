"""Personal inflation calculator tests.

This is the feature a user actually touches, so the tests are heavier here: the arithmetic
must be exact, and the failure modes (missing data, partial baskets) must be visible rather
than silently absorbed.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from colcheck.transforms.personal_inflation import (
    Basket,
    compute_personal_inflation,
    explain_gap,
    inflation_by_division,
)

NAMES = {"01": ("Food & Beverages", "Makanan"), "07": ("Transport", "Pengangkutan")}


def test_single_division_reproduces_that_division_rate():
    """A basket entirely in one division must return exactly that division's rate."""
    basket = Basket(name="all food", weights={"01": 1.0})
    out = compute_personal_inflation(basket, {"01": 5.0}, headline_rate=2.0)
    assert out.personal_pct == pytest.approx(5.0)
    assert out.gap_pp == pytest.approx(3.0)


def test_weighted_average_known_answer():
    """70/30 split across 4% and 10% is 5.8%, and the gap is +3.8pp against 2% headline."""
    basket = Basket(name="mixed", weights={"01": 70.0, "07": 30.0})
    out = compute_personal_inflation(basket, {"01": 4.0, "07": 10.0}, headline_rate=2.0)
    assert out.personal_pct == pytest.approx(5.8)
    assert out.gap_pp == pytest.approx(3.8)


def test_weights_are_normalised_so_ringgit_amounts_work():
    """Users enter spending in ringgit, not percentages; the result must not depend on scale."""
    pesos = Basket(name="ringgit", weights={"01": 700.0, "07": 300.0})
    percents = Basket(name="percent", weights={"01": 70.0, "07": 30.0})
    rates = {"01": 4.0, "07": 10.0}
    a = compute_personal_inflation(pesos, rates, headline_rate=2.0)
    b = compute_personal_inflation(percents, rates, headline_rate=2.0)
    assert a.personal_pct == pytest.approx(b.personal_pct)


def test_contributions_sum_to_the_personal_rate():
    basket = Basket(name="mixed", weights={"01": 70.0, "07": 30.0})
    out = compute_personal_inflation(basket, {"01": 4.0, "07": 10.0}, headline_rate=2.0, names=NAMES)
    assert sum(c.contribution_pp for c in out.contributions) == pytest.approx(out.personal_pct)
    assert sum(c.weight for c in out.contributions) == pytest.approx(1.0)
    # Sorted by weight, biggest first.
    assert out.contributions[0].division == "01"
    assert out.contributions[0].name_en == "Food & Beverages"


def test_missing_division_is_reported_not_treated_as_zero():
    """The critical behaviour: an unpriceable division must not drag the rate down."""
    basket = Basket(name="partial", weights={"01": 50.0, "07": 50.0})
    out = compute_personal_inflation(basket, {"01": 4.0}, headline_rate=2.0)
    assert out.missing_divisions == ["07"]
    # Rate is 4%, NOT 2% (which treating the missing division as 0% would have produced).
    assert out.personal_pct == pytest.approx(4.0)
    assert out.priced_weight_share == pytest.approx(0.5)
    assert any("excluded" in c for c in out.caveats)


def test_low_coverage_adds_a_reliability_caveat():
    basket = Basket(name="mostly missing", weights={"01": 20.0, "07": 80.0})
    out = compute_personal_inflation(basket, {"01": 4.0}, headline_rate=2.0)
    assert any("not reliable" in c for c in out.caveats)


def test_entirely_unpriceable_basket_returns_nan_not_a_number():
    basket = Basket(name="nothing", weights={"99": 1.0})
    out = compute_personal_inflation(basket, {"01": 4.0}, headline_rate=2.0)
    assert math.isnan(out.personal_pct)
    assert math.isnan(out.gap_pp)
    assert out.missing_divisions == ["99"]


def test_zero_weight_basket_raises():
    with pytest.raises(ValueError, match="no positive weights"):
        Basket(name="empty", weights={"01": 0.0}).normalised()


def test_negative_weights_are_ignored_not_subtracted():
    basket = Basket(name="bad input", weights={"01": 100.0, "07": -50.0})
    assert basket.normalised() == {"01": 1.0}


def test_explain_gap_names_the_drivers():
    basket = Basket(name="mixed", weights={"01": 20.0, "07": 80.0})
    out = compute_personal_inflation(basket, {"01": 3.0, "07": 9.0}, headline_rate=2.0, names=NAMES)
    text = explain_gap(out)
    assert "above" in text
    assert "Transport" in text or "transport" in text
    assert "percentage points" in text


def test_explain_gap_says_so_when_there_is_no_gap():
    basket = Basket(name="headline-like", weights={"01": 1.0})
    out = compute_personal_inflation(basket, {"01": 2.0}, headline_rate=2.0, names=NAMES)
    assert "essentially the same" in explain_gap(out)


def test_explain_gap_handles_nan():
    basket = Basket(name="nothing", weights={"99": 1.0})
    out = compute_personal_inflation(basket, {}, headline_rate=2.0)
    assert "not enough price data" in explain_gap(out)


def test_inflation_by_division_yoy():
    """Needs 13+ months: yoy is a 12-period shift, so a short series has no answer at all."""
    dates = pd.date_range("2024-01-01", periods=14, freq="MS")
    # 100.0 for the first 12 months, then 110.0 -- so the 13th month is exactly +10% yoy.
    index = [100.0] * 12 + [110.0, 110.0]
    df = pd.DataFrame({"date": dates, "division": "01", "index": index})

    rates = inflation_by_division(df, yoy=True)
    assert rates["01"] == pytest.approx(10.0)


def test_inflation_by_division_yoy_without_a_full_year_of_history():
    """A series shorter than 13 months must yield nothing, not a wrong number."""
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-02-01"]),
            "division": ["01", "01"],
            "index": [100.0, 101.0],
        }
    )
    assert inflation_by_division(df, yoy=True) == {}


def test_inflation_by_division_cumulative():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2010-01-01", "2025-01-01"]),
            "division": ["01", "01"],
            "index": [100.0, 150.0],
        }
    )
    rates = inflation_by_division(df, base_year=2010)
    assert rates["01"] == pytest.approx(50.0)


def test_inflation_by_division_requires_a_base_year():
    df = pd.DataFrame({"date": pd.to_datetime(["2010-01-01"]), "division": ["01"], "index": [100.0]})
    with pytest.raises(ValueError, match="base_year is required"):
        inflation_by_division(df)
