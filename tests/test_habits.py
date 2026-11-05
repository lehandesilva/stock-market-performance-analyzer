"""Trading habits (docs/05-your-trading-habits.md), on small examples worked out by hand."""

import pandas as pd
import pytest

from conftest import make_actions, make_tx
from smpa.habits import (buys_review, buys_summary, by_reason, disposition, fees_by_stock, fees_by_year,
                         journal_review, never_sold, round_trips, switch_summary, switches, trading_alpha, trip_stats,
                         wilson)

D = pd.Timestamp


def closes(rows):
    return pd.DataFrame(rows, columns=["symbol", "date", "price"]).assign(date=lambda x: pd.to_datetime(x["date"]))


def test_wilson_range_for_seven_wins_in_twelve():
    lo, hi = wilson(7, 12)
    assert (round(lo, 2), round(hi, 2)) == (0.32, 0.81)  # docs/05 §1: consistent with skill and with luck
    assert wilson(0, 5)[0] == 0 and wilson(5, 5)[1] == 1 and wilson(0, 0) is None


def test_disposition_on_the_docs_example():
    """Two days of selling: A sold, B kept (up), C kept (down); then D sold, B kept, C and E kept (down)."""
    tx = make_tx([("2024-01-02", "BUY", s, 100, 10.0) for s in ("A", "B", "C")]
                 + [("2024-03-15", "BUY", s, 100, 10.0) for s in ("D", "E")]
                 + [("2024-03-01", "SELL", "A", 100, 12.0), ("2024-04-01", "SELL", "D", 100, 12.0)])
    px = closes([("B", "2024-03-01", 11.0), ("C", "2024-03-01", 9.0), ("B", "2024-04-01", 11.0),
                 ("C", "2024-04-01", 9.0), ("E", "2024-04-01", 9.0)])
    r = disposition(tx, None, px)
    assert r["counts"] == {"realized_gains": 2, "paper_gains": 2, "realized_losses": 0, "paper_losses": 3}
    assert (r["pgr"]["share"], r["plr"]["share"], r["difference"], r["days"]) == (0.5, 0.0, 0.5, 2)
    assert disposition(tx, None, px.iloc[:0])["counts"]["paper_gains"] == 0  # no closes: kept stocks are left out


def test_round_trip_statistics():
    tx = make_tx([("2024-01-01", "BUY", "A", 100, 10.0), ("2024-01-31", "SELL", "A", 100, 11.0),
                  ("2024-01-01", "BUY", "B", 100, 10.0), ("2024-03-01", "SELL", "B", 100, 10.5),
                  ("2024-01-01", "BUY", "C", 100, 10.0), ("2024-07-19", "SELL", "C", 100, 9.7)], fee=0)
    t = round_trips(tx, None)
    assert t["symbol"].tolist() == ["C", "B", "A"] and t["pnl"].tolist() == pytest.approx([-30, 50, 100])
    s = trip_stats(t)
    assert s["win_rate"]["share"] == pytest.approx(2 / 3) and s["win_rate"]["n"] == 3
    assert (s["average_win"], s["average_loss"]) == pytest.approx((75, -30))
    assert (s["payoff"], s["profit_factor"], s["expectancy"]) == pytest.approx((2.5, 5, 40))
    assert (s["median_days_win"], s["median_days_loss"]) == (45, 200)  # losers held longer
    assert s["average_win_return"] == pytest.approx(0.075)
    assert trip_stats(t.iloc[:0])["payoff"] is None


def test_fee_drag_turnover_and_fee_types():
    tx = make_tx([("2024-01-02", "BUY", "A", 1000, 10.0), ("2024-06-03", "SELL", "A", 400, 12.0),
                  ("2025-02-03", "BUY", "B", 100, 20.0)])
    parts = pd.DataFrame({"contract_no": ["1", "2"], "brokerage": [6.4, 3.07], "cds": [0.0, 0.0], "cse": [0.84, 0.4],
                          "sec": [0.72, 0.35], "stl": [3.0, 1.44], "clearing": [0.24, 0.12],
                          "foreign_brokerage": [0.0, 0.0]})  # contract 3 (2025) has no breakdown
    hist = pd.DataFrame({"date": pd.to_datetime(["2024-03-01", "2024-09-02"]), "value": [9000.0, 11000.0]})
    growth = pd.Series([1.0, 1.1], index=pd.to_datetime(["2024-03-01", "2024-09-02"]))
    y = fees_by_year(tx, parts, hist, growth).set_index("year")
    assert y.at[2024, "fees"] == pytest.approx(112 + 53.76) and y.at[2024, "brokerage"] == pytest.approx(9.47)
    assert y.at[2024, "unknown"] == pytest.approx(112 + 53.76 - 11.2 - 5.38)  # what the parts don't explain
    assert y.at[2024, "fee_drag"] == pytest.approx(165.76 / 10_000)
    assert y.at[2024, "turnover"] == pytest.approx(4800 / 10_000)  # the smaller of buys and sells
    assert y.at[2024, "twr"] == pytest.approx(0.1)
    assert y.at[2025, "unknown"] == pytest.approx(22.4) and pd.isna(y.at[2025, "average_value"])
    by = fees_by_stock(tx, None, {"A": 12.0, "B": None}).set_index("symbol")
    assert by.at["A", "lifetime"] == pytest.approx(4800 - 53.76 - 4044.8 + 600 * 12 - 6067.2)
    assert by.at["A", "share"] == pytest.approx(165.76 / by.at["A", "lifetime"]) and pd.isna(by.at["B", "share"])


def test_frozen_portfolio_and_trading_alpha():
    tx = make_tx([("2024-06-03", "BUY", "A", 100, 10.0), ("2025-03-03", "SELL", "A", 100, 11.0),
                  ("2025-03-03", "BUY", "B", 100, 11.0)], fee=0)
    actions = make_actions([{"date": "2025-06-02", "symbol": "A", "action": "DIVIDEND", "per_share": 1.0}])
    px = closes([("A", "2024-12-31", 10.0), ("A", "2025-12-31", 12.0), ("B", "2025-12-31", 15.4)])
    growth = pd.Series([1.0, 1.54], index=pd.to_datetime(["2024-12-31", "2025-12-31"]))
    [y] = trading_alpha(tx, actions, px, growth, "2025-12-31").to_dict("records")
    # A held all year: 1,000 -> 1,200, plus a 1.00 dividend on its 100 shares; what you did: +54%
    assert (y["year"], y["holdings"], y["complete"]) == (2025, 1, True)
    assert (y["frozen"], y["twr"], y["alpha"]) == pytest.approx((0.30, 0.54, 0.24))
    split = make_actions([{"date": "2025-09-01", "symbol": "A", "action": "SPLIT", "ratio_new": 2, "ratio_old": 1}])
    px2 = closes([("A", "2024-12-31", 10.0), ("A", "2025-08-29", 12.0), ("A", "2025-09-01", 6.0), ("A", "2025-12-31", 6.0)])
    [y] = trading_alpha(tx, split, px2, growth, "2025-12-31").to_dict("records")
    assert y["frozen"] == pytest.approx(0.20)  # 200 shares at 6.00 after the split


def test_switch_gain_after_both_fees():
    tx = make_tx([("2023-12-01", "BUY", "X", 100, 9.0), ("2024-01-02", "SELL", "X", 100, 10.0),
                  ("2024-01-05", "BUY", "Y", 100, 20.0), ("2024-03-01", "BUY", "Z", 10, 5.0)])
    px = closes([("X", "2024-04-05", 11.0), ("Y", "2024-04-05", 24.0)])
    [sw] = switches(tx, None, px, "2024-11-05").to_dict("records")  # Z: no sale before it, not a switch
    assert (sw["sold"], sw["bought"], sw["fees"]) == ("X", "Y", pytest.approx(0.0224))
    assert sw["gain_3"] == pytest.approx(1.2 / 1.1 - 1 - 0.0224) and sw["gain_6"] is None
    s = switch_summary(switches(tx, None, px, "2024-11-05"))
    assert (s[0]["n"], s[0]["ahead"]["k"], s[1]["n"]) == (1, 1, 0)


def test_never_sold_and_the_same_money_in_the_aspi():
    tx = make_tx([("2023-12-01", "BUY", "X", 100, 9.0), ("2024-01-02", "SELL", "X", 100, 10.0)])
    px = closes([("X", "2026-11-04", 15.0)])
    aspi = pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2026-11-04"]), "value": [100.0, 110.0]})
    r = never_sold(tx, None, px, aspi, "2026-11-05")
    assert (r["sales"], r["priced"], r["proceeds"], r["value"]) == (1, 1, pytest.approx(988.8), 1500.0)
    assert r["in_aspi"] == pytest.approx(988.8 * 1.1) and r["difference"] == pytest.approx(1500 - 988.8)
    assert never_sold(tx, None, px.iloc[:0], aspi, "2026-11-05")["unpriced"] == ["X"]


def test_buys_tagged_and_judged_against_the_aspi():
    tx = make_tx([("2024-01-02", "BUY", "A", 100, 10.0), ("2024-02-01", "BUY", "A", 100, 8.0),
                  ("2024-03-01", "BUY", "A", 100, 12.0)])
    days = pd.bdate_range("2024-01-01", "2024-12-31")
    aspi = pd.DataFrame({"date": days, "value": 100.0})  # flat: the excess is the stock's own return
    t1 = days[days > D("2024-01-02")][19]
    px = closes([("A", t1.strftime("%Y-%m-%d"), 11.0), ("A", "2024-04-02", 9.0)])
    b = buys_review(tx, None, px, aspi, "2024-11-05")
    assert b["kind"].tolist() == ["new", "down", "winner"]  # 8 < 10.11 average; 12 > 9.10
    assert b.at[0, "excess_20"] == pytest.approx(0.1) and b.at[0, "after_3"] == pytest.approx(-0.1)
    assert b.at[2, "after_3"] is None or pd.isna(b.at[2, "after_3"])  # no close within 31 days of 2024-06-01
    s = {x["kind"]: x for x in buys_summary(b)}
    assert (s["all"]["n"], s["new"]["n_excess_20"], s["down"]["n"]) == (3, 1, 1)
    # one buy split into two contracts the same day is one decision, not a new position and then averaging down
    split = make_tx([("2024-01-02", "BUY", "A", 40, 10.0), ("2024-01-02", "BUY", "A", 60, 10.0)])
    [one] = buys_review(split, None, px, aspi, "2024-11-05").to_dict("records")
    assert (one["kind"], one["quantity"], one["price"]) == ("new", 100, 10.0)


def test_journal_outcomes_follow_each_buys_own_shares():
    tx = make_tx([("2024-01-02", "BUY", "A", 100, 10.0), ("2024-01-02", "BUY", "B", 100, 20.0),
                  ("2024-06-03", "SELL", "A", 100, 12.0), ("2024-07-01", "BUY", "A", 50, 11.0)])
    actions = make_actions([{"date": "2024-05-02", "symbol": "B", "action": "DIVIDEND", "amount": 50.0}])
    journal = pd.DataFrame({"contract_no": ["1", "2", "4"], "reason": ["results play", "Dividend ", "Results play"],
                            "target": [14.0, None, None], "stop": [8.0, None, None], "note": ["", "", ""]})
    j = journal_review(tx, actions, journal, {"A": 11.0, "B": 22.0}, 0.01).set_index("contract_no")
    assert j.at["1", "outcome"] == pytest.approx(1186.56 - 1011.2)  # sold: 1,200 less fees, against its cost
    assert j.at["2", "outcome"] == pytest.approx(50 + 100 * 22 * 0.99 - 2022.4)  # dividend + what's left, after fees
    assert j.at["4", "left"] == 50 and j.at["4", "dividends"] == 0
    r = {x["reason"]: x for x in by_reason(j.reset_index())}
    assert r["results play"]["n"] == 2 and r["Dividend"]["n"] == 1  # same reason, any case
    assert r["Dividend"]["made_money"]["k"] == 1
    assert pd.isna(journal_review(tx, actions, journal, {"B": 22.0}, 0.01).set_index("contract_no").at["4", "outcome"])
