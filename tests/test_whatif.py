"""What if I'd sold on a date? The worked example from docs/03-what-if-i-sold.md."""

import pandas as pd
import pytest

from conftest import make_tx
from smpa.analysis import value_history
from smpa.whatif import holding_vs_selling, if_sold_on, sales_review


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


def test_holding_was_worse_than_selling_on_d(abc):
    tx, actions, closes = abc
    index = pd.DataFrame({"date": pd.to_datetime(["2025-03-03", "2025-06-02", "2025-11-03", "2026-11-05"]),
                          "value": [100.0, 100.0, 100.0, 100.0]})
    r = holding_vs_selling(tx, actions, closes, index, "ABC.N0000", "2025-03-03", "2026-11-05", 0.0112, 0.0112)
    assert (r["shares"], r["shares_left"]) == (1500, 900)
    assert (r["sales"], r["dividends"], r["value_now"]) == pytest.approx((40_343.04, 1_350.00, 48_945.60))
    assert r["holding"] == pytest.approx(90_638.64) and r["selling"] == pytest.approx(111_240.00)
    assert r["difference"] == pytest.approx(-20_601.36)
    assert r["difference_fair"] == pytest.approx(-20_601.36)  # a flat index: time changes nothing
    assert holding_vs_selling(tx, actions, closes, index, "ABC.N0000", "2024-01-01", "2026-11-05", 0.01, 0.01) is None


def test_after_the_sale_and_exit_efficiency(abc):
    tx, actions, closes = abc
    later = pd.concat([closes, pd.DataFrame({"symbol": "ABC.N0000", "date": pd.to_datetime(["2024-12-02", "2026-06-02"]),
                                             "price": [78.5, 58.0]})])
    index = pd.DataFrame({"date": pd.to_datetime(["2025-06-02", "2026-06-02"]), "value": [100.0, 112.0]})
    [s] = sales_review(tx, actions, later, index, "2026-11-05").to_dict("records")
    assert s["after_12"] == pytest.approx(58 / 68 - 1, abs=1e-9) and round(s["after_12"], 3) == -0.147
    assert round(s["vs_12"], 3) == -0.238 and s["left_12"] == pytest.approx(-6_000)  # a loss avoided: a good sale
    assert s["after_1"] is None  # no close within a month of 2025-07-02
    assert round(s["efficiency"], 2) == 0.66  # (68 − 47.19) ÷ (78.50 − 47.19)


def test_sales_summary_averages_every_sale():
    from smpa.whatif import sales_summary

    tx = make_tx([("2024-01-02", "BUY", "AAA.N0000", 100, 10.0), ("2024-01-02", "BUY", "BBB.N0000", 100, 10.0),
                  ("2024-03-01", "SELL", "AAA.N0000", 100, 12.0), ("2024-03-01", "SELL", "BBB.N0000", 100, 8.0)])
    closes = pd.DataFrame({"symbol": ["AAA.N0000"] * 3 + ["BBB.N0000"] * 3,
                           "date": pd.to_datetime(["2024-02-01", "2024-03-01", "2024-04-01"] * 2),
                           "price": [14.0, 12.0, 13.2, 10.5, 8.0, 6.0]})
    index = pd.DataFrame({"date": pd.to_datetime(["2024-03-01", "2024-04-01"]), "value": [100.0, 110.0]})
    sales = sales_review(tx, None, closes, index, "2024-12-31")
    s = sales_summary(sales)
    w1 = s["windows"][0]
    assert (s["sales"], w1["n"], w1["vs_n"]) == (2, 2, 2) and s["windows"][1]["n"] == 0  # no close 3 months on
    assert w1["after"] == pytest.approx((0.1 - 0.25) / 2) and w1["vs"] == pytest.approx((1.1 / 1.1 - 1 + 0.75 / 1.1 - 1) / 2)
    assert w1["beat"] == 0 and w1["left"] == pytest.approx(100 * 1.2 - 100 * 2)
    # exit efficiency only for the sale at a gain: (12 − 10.112) ÷ (14 − 10.112)
    eff = sales.set_index("symbol")["efficiency"]
    assert eff["AAA.N0000"] == pytest.approx((12 - 10.112) / (14 - 10.112)) and pd.isna(eff["BBB.N0000"])
    assert s["efficiency"] == pytest.approx(eff["AAA.N0000"]) and s["efficiency_n"] == 1
