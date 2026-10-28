"""Test fixtures.

The default fixtures are synthetic and hand-computable, so the transforms are tested against
arithmetic we can verify by eye rather than against whatever the live data happens to say
today. Live-data tests are marked `network` and kept separate.
"""

from __future__ import annotations

import pandas as pd
import pytest

from colcheck import registry


@pytest.fixture
def two_states() -> tuple[str, str]:
    return registry.STATES[0], registry.STATES[1]  # Johor, Kedah


@pytest.fixture
def mcoicop_frame() -> pd.DataFrame:
    """Minimal MCOICOP lookup: two 2-digit divisions and two 3-digit groups."""
    rows = [
        (1, "overall", "-", "-", "-", "All items", "Semua item"),
        (2, "01", "-", "-", "-", "Food & Beverages", "Makanan & Minuman"),
        (2, "07", "-", "-", "-", "Transport", "Pengangkutan"),
        (3, "01", "001", "-", "-", "Food away from home", "Makanan di luar rumah"),
        (3, "01", "011", "-", "-", "Food at home", "Makanan di rumah"),
    ]
    return pd.DataFrame(
        rows,
        columns=["digits", "division", "group", "class", "subclass", "desc_en", "desc_bm"],
    )


@pytest.fixture
def cpi_national_frame() -> pd.DataFrame:
    """24 months of national CPI: division 'overall' flat at 100 -> 110, division 01 doubled.

    Deliberately simple so year-on-year and cumulative figures are checkable by hand.
    """
    months = pd.date_range("2024-01-01", periods=24, freq="MS")
    rows = []
    for i, d in enumerate(months):
        rows.append({"date": d, "division": "overall", "index": 100.0 + i * 0.4})
        rows.append({"date": d, "division": "01", "index": 50.0 + i * 0.5})
    return pd.DataFrame(rows)


@pytest.fixture
def cpi_state_frame() -> pd.DataFrame:
    """Two states, two years, division 'overall', with exactly known inflation.

    Johor: 100 -> 125 (+25%). Kedah: 100 -> 150 (+50%).
    """
    rows = []
    for year, johor, kedah in ((2010, 100.0, 100.0), (2025, 125.0, 150.0)):
        for month in range(1, 13):
            d = pd.Timestamp(year=year, month=month, day=1)
            rows.append({"state": "Johor", "date": d, "division": "overall", "index": johor})
            rows.append({"state": "Kedah", "date": d, "division": "overall", "index": kedah})
    return pd.DataFrame(rows)


@pytest.fixture
def wages_state_frame() -> pd.DataFrame:
    """Nominal wages matching `cpi_state_frame`'s states, 'both' sex, plus a Malaysia row.

    Johor: 1000 -> 1500 nominal, against +25% inflation => real +20%.
    Kedah: 1000 -> 1200 nominal, against +50% inflation => real -20%.
    """
    rows = []
    for state, y2010, y2025 in (
        ("Johor", 1000.0, 1500.0),
        ("Kedah", 1000.0, 1200.0),
        ("Malaysia", 1100.0, 1600.0),
    ):
        for year, value in ((2010, y2010), (2025, y2025)):
            for sex in ("both", "male", "female"):
                rows.append(
                    {
                        "state": state,
                        "date": pd.Timestamp(year=year, month=1, day=1),
                        "sex": sex,
                        "recipients": 1000,
                        "wages_median": value,
                        "wages_mean": value * 1.3,
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture
def published_real_frame() -> pd.DataFrame:
    """DOSM's published CITIZEN real series matching `wages_state_frame` exactly."""
    rows = []
    for state, real_2025 in (("Johor", 1200.0), ("Kedah", 800.0)):
        for year, value in ((2010, 1000.0), (2025, real_2025)):
            rows.append(
                {
                    "state": state,
                    "date": pd.Timestamp(year=year, month=1, day=1),
                    "wages_median_real": value,
                    "wages_mean_real": value * 1.3,
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture
def pricecatcher_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """One tiny PriceCatcher edition: 4 prices across 2 items, 2 premises, 2 states."""
    items = pd.DataFrame(
        {
            "item_code": [1, 2, -1],
            "item": ["CHICKEN", "EGGS", None],
            "unit": ["1kg", "10 eggs", None],
            "item_group": ["FRESH", "FRESH", None],
            "item_category": ["POULTRY", "EGGS", None],
        }
    )
    premises = pd.DataFrame(
        {
            "premise_code": [10, 11],
            "premise": ["Shop A", "Shop B"],
            "premise_type": ["Pasar Mini", "Hypermarket"],
            "state": ["Johor", "Kedah"],
            "district": ["JB", "Alor Setar"],
        }
    )
    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01"] * 4),
            "premise_code": [10, 10, 11, 10],
            "item_code": [1, 1, 2, -1],
            "price": [10.0, 12.0, 5.0, 99.0],
        }
    )
    return prices, items, premises
