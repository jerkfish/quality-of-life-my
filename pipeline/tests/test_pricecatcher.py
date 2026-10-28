"""PriceCatcher reduction tests."""

from __future__ import annotations

import pandas as pd
import pytest

from colcheck.transforms import pricecatcher as pc


@pytest.fixture
def cleaned(pricecatcher_frames):
    prices, items, premises = pricecatcher_frames
    return prices, pc.clean_items(items), pc.clean_premises(premises)


def test_clean_items_drops_the_sentinel_row():
    """item_code -1 has no name or unit and cannot be priced meaningfully."""
    items = pd.DataFrame(
        {
            "item_code": [1, -1],
            "item": ["CHICKEN", None],
            "unit": ["1kg", None],
            "item_group": ["FRESH", None],
            "item_category": ["POULTRY", None],
        }
    )
    out = pc.clean_items(items)
    assert list(out["item_code"]) == [1]


def test_clean_items_drops_rows_without_a_name():
    items = pd.DataFrame(
        {
            "item_code": [1, 2],
            "item": ["CHICKEN", None],
            "unit": ["1kg", "kg"],
            "item_group": ["FRESH", "FRESH"],
            "item_category": ["POULTRY", "X"],
        }
    )
    assert list(pc.clean_items(items)["item_code"]) == [1]


def test_aggregate_edition_known_answer(cleaned):
    """Item 1 in Johor has two observations (10.0, 12.0): median 11.0, n_obs 2."""
    prices, items, premises = cleaned
    agg = pc.aggregate_edition(prices, items, premises)

    assert len(agg) == 2  # item 1 in Johor, item 2 in Kedah. Item -1 is dropped.
    johor = agg[(agg["item_code"] == 1) & (agg["state"] == "Johor")].iloc[0]
    assert johor["median_price"] == pytest.approx(11.0)
    assert johor["min_price"] == pytest.approx(10.0)
    assert johor["max_price"] == pytest.approx(12.0)
    assert johor["n_obs"] == 2
    assert johor["month"] == "2026-09"


def test_aggregate_edition_drops_the_sentinel_item(cleaned):
    prices, items, premises = cleaned
    agg = pc.aggregate_edition(prices, items, premises)
    assert -1 not in set(agg["item_code"])


def test_aggregate_edition_attaches_state_from_premises(cleaned):
    prices, items, premises = cleaned
    agg = pc.aggregate_edition(prices, items, premises)
    eggs = agg[agg["item_code"] == 2].iloc[0]
    assert eggs["state"] == "Kedah"  # premise 11
    assert eggs["unit"] == "10 eggs"


def test_national_series_weights_by_observation_count(cleaned):
    """A state with 100 observations must outweigh one with 1."""
    prices, items, premises = cleaned
    agg = pc.aggregate_edition(prices, items, premises)
    # Add a heavily-observed cheap Johor row for item 1 and a thin expensive Kedah one.
    extra = pd.DataFrame(
        [
            {
                "item_code": 1,
                "item": "CHICKEN",
                "unit": "1kg",
                "item_group": "FRESH",
                "item_category": "POULTRY",
                "state": "Johor",
                "month": "2026-09",
                "median_price": 10.0,
                "mean_price": 10.0,
                "min_price": 10.0,
                "max_price": 10.0,
                "n_obs": 1000,
            },
            {
                "item_code": 1,
                "item": "CHICKEN",
                "unit": "1kg",
                "item_group": "FRESH",
                "item_category": "POULTRY",
                "state": "Kedah",
                "month": "2026-09",
                "median_price": 20.0,
                "mean_price": 20.0,
                "min_price": 20.0,
                "max_price": 20.0,
                "n_obs": 1,
            },
        ]
    )
    nat = pc.national_item_series(pd.concat([agg[agg["item_code"] == 1], extra], ignore_index=True))
    row = nat[(nat["item_code"] == 1) & (nat["month"] == "2026-09")].iloc[0]
    # Weighted towards the 1000-observation Johor price, not the 1-observation Kedah one.
    assert row["median_price"] < 12.0
    assert row["n_states"] == 2


def test_national_series_flags_thin_coverage(cleaned):
    prices, items, premises = cleaned
    agg = pc.aggregate_edition(prices, items, premises)
    nat = pc.national_item_series(agg)
    # Only 1 state per item in this fixture, so everything is thin.
    assert nat["thin_coverage"].all()


def test_item_change_summary_known_answer(cleaned):
    """Chicken 10.0 -> 12.0 over 12 months is +20%, annualised +20%."""
    prices, items, premises = cleaned
    agg = pc.aggregate_edition(prices, items, premises)
    later = agg[agg["item_code"] == 1].copy()
    later["month"] = "2026-09"
    earlier = later.copy()
    earlier["month"] = "2025-09"
    earlier["median_price"] = 10.0

    out = pc.item_change_summary(
        pd.concat([earlier, later], ignore_index=True), start_month="2025-09", end_month="2026-09"
    )
    row = out[out["item_code"] == 1].iloc[0]
    assert row["change_pct"] == pytest.approx(10.0)  # median 11.0 over 10.0
    assert row["change_pct_annualised"] == pytest.approx(10.0)


def test_item_change_summary_raises_when_months_do_not_overlap(cleaned):
    prices, items, premises = cleaned
    agg = pc.aggregate_edition(prices, items, premises)
    with pytest.raises(ValueError, match="No items present"):
        pc.item_change_summary(agg, start_month="1999-01", end_month="2026-09")


def test_aggregate_missing_premise_leaves_state_null_not_guessed():
    """An unknown premise code must not be attributed to an arbitrary state."""
    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01"]),
            "premise_code": [999],
            "item_code": [1],
            "price": [10.0],
        }
    )
    items = pd.DataFrame(
        {
            "item_code": [1],
            "item": ["CHICKEN"],
            "unit": ["1kg"],
            "item_group": ["FRESH"],
            "item_category": ["POULTRY"],
        }
    )
    premises = pd.DataFrame(
        {"premise_code": [10], "premise": ["A"], "premise_type": ["X"], "state": ["Johor"],
         "district": ["JB"]}  # fmt: skip
    )
    agg = pc.aggregate_edition(prices, items, premises)
    assert pd.isna(agg.iloc[0]["state"])
