"""The per-stock page."""

import pandas as pd
import pytest

from smpa.ticker import _yields, ticker_detail


def test_worked_example(abc):
    tx, actions, closes = abc
    index = pd.DataFrame({"date": pd.to_datetime(["2024-02-01", "2026-11-05"]), "value": [100.0, 100.0]})
    t = ticker_detail(tx, actions, 55.0, None, closes, index, "ABC.N0000", "2026-11-05", 0.0112, 49_500.0,
                      sold_on="2025-03-03")
    life = t["lifetime"]
    assert (life["realized"], life["unrealized"], life["dividends"]) == pytest.approx((12_029.44, 7_029.60, 4_350.00))
    assert life["total"] == pytest.approx(23_409.04) and life["fees"] == pytest.approx(1_240.96)
    assert round(life["xirr"], 3) == 0.170 and round(life["xirr_if_sold"], 3) == 0.166
    p = t["position"]
    assert (p["quantity"], round(p["avg_cost"], 2), round(p["break_even"], 2), p["weight"]) == (900, 47.19, 47.72, 1.0)
    assert round(t["holding"]["average_age"]) == 907 and t["lots"]["quantity"].tolist() == [400, 500]
    s = t["if_sold"]
    assert s["lifetime"] == pytest.approx(43_456) and s["better_by"] == pytest.approx(20_601.36)
    assert s["cohort"]["difference"] == pytest.approx(-20_601.36)
    assert t["vs_index"]["ks_pme"] > 1
    assert t["yields"]["income_12m"] == 0  # the last dividend, on 2025-11-03, is just over a year ago
    y = _yields(t["actions"], {"cost_basis": 42_470.40}, 55.0, pd.Timestamp("2026-06-01"))
    assert y["income_12m"] == 1_350 and y["on_cost"] == pytest.approx(1_350 / 42_470.40)
    assert y["trailing"] == pytest.approx(1.5 / 55)  # 1,350 on 900 shares, at today's price
