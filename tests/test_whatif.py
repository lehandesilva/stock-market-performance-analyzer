"""What if I'd sold on a date? The worked example from docs/03-what-if-i-sold.md."""

import pandas as pd
import pytest

from smpa.analysis import value_history
from smpa.whatif import if_sold_on


def test_sold_everything_on_d(abc):
    tx, actions, closes = abc
    r = if_sold_on(tx, actions, closes, "2025-03-03", 0.0112)
    [h] = r["holdings"].to_dict("records")
    assert (h["quantity"], h["price"], h["proceeds"], h["fees"]) == (1500, 75.0, pytest.approx(111_240), pytest.approx(1260))
    assert r["gain"] == pytest.approx(40_456) and r["gain"] / r["cost"] == pytest.approx(0.572, abs=5e-4)
    assert (r["dividends"], r["realized"]) == (3000, 0) and r["lifetime"] == pytest.approx(43_456)
    assert r["xirr"] == pytest.approx(0.725, abs=5e-4) and r["complete"]
    # what actually happened, valued today after the fees to sell the 900 left
    now = if_sold_on(tx, actions, closes, "2026-11-05", 0.0112)
    assert now["lifetime"] == pytest.approx(22_854.64) and now["xirr"] == pytest.approx(0.166, abs=5e-4)
    assert r["lifetime"] - now["lifetime"] == pytest.approx(20_601.36)  # selling on D would have been better by


def test_no_close_means_no_price_not_a_guess(abc):
    tx, actions, closes = abc
    r = if_sold_on(tx, actions, closes, "2024-09-01", 0.0112)  # no close within 31 days
    assert not r["complete"] and r["holdings"]["price"].isna().all() and r["gain"] == 0
    assert if_sold_on(tx, actions, closes, "2025-03-03", 0.0112, symbol="XYZ.N0000")["holdings"].empty


def test_value_history_carries_realized_and_dividends_to_date(abc):
    tx, actions, closes = abc
    days = pd.DataFrame({"date": ["2024-12-01", "2025-07-01"], "value": [1.0, 1.0]})
    h = value_history(tx, actions, closes.assign(date=closes["date"].dt.strftime("%Y-%m-%d")), days,
                      pd.Timestamp("2026-11-05"))
    by_day = h.set_index(h["date"].dt.strftime("%Y-%m-%d"))
    assert by_day.loc["2024-12-01", ["realized", "dividends"]].tolist() == [0, 3000]
    assert by_day.loc["2025-07-01", "realized"] == pytest.approx(12_029.44)
    assert by_day.loc["2026-11-05", "dividends"] == 4350
