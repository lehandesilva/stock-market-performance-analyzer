"""FIFO lots, built alongside the average-cost ledger."""

import pandas as pd
import pytest

from conftest import make_actions, make_tx
from smpa.analysis import compute
from smpa.lots import average_age, open_lots, replay, trips_frame


def test_worked_example_lots_and_round_trip(abc):
    tx, actions, _ = abc
    book, lots = replay(tx, actions)
    held = open_lots(lots, "ABC.N0000", "2026-11-05")
    assert held[["quantity", "age_days"]].values.tolist() == [[400, 1008], [500, 826]]
    assert held["cost_per_share"].round(2).tolist() == [40.45, 60.67]
    assert held["cost"].sum() == pytest.approx(46_515.20)  # FIFO cost; average cost says 42,470.40
    assert book.pos["ABC.N0000"][1] == pytest.approx(42_470.40)
    assert round(average_age(held)) == 907
    [trip] = trips_frame(lots.trips).to_dict("records")
    assert (trip["quantity"], trip["days"], trip["matched"]) == (600, 487, True)
    assert trip["cost"] == pytest.approx(600 * 40.448) and trip["proceeds"] == pytest.approx(40_343.04)
    assert trip["buy_fees"] == pytest.approx(268.80) and trip["sell_fees"] == pytest.approx(456.96)


def test_cost_method_never_changes_the_total(abc):
    tx, actions, _ = abc
    _, lots = replay(tx, actions)
    pf = compute(tx, {"ABC.N0000": 55.0}, actions)
    fifo = trips_frame(lots.trips)["pnl"].sum() + 900 * 55 - open_lots(lots, None, "2026-11-05")["cost"].sum()
    assert fifo == pytest.approx(pf.realized_pnl + pf.holdings["unrealized_pnl"].sum())  # 19,059.04 both ways


def test_splits_and_bonuses_scale_lots_and_rounding_goes_to_the_newest():
    tx = make_tx([("2024-01-01", "BUY", "AAA.N0000", 105, 10.0), ("2024-02-01", "BUY", "AAA.N0000", 22, 12.0)], fee=0)
    a = make_actions([{"date": "2024-03-01", "symbol": "AAA.N0000", "action": "BONUS", "ratio_new": 1, "ratio_old": 10},
                      {"date": "2024-04-01", "symbol": "AAA.N0000", "action": "SPLIT", "ratio_new": 1, "ratio_old": 5}])
    book, lots = replay(tx, a)  # 127 + 12 bonus = 139 -> 1-into-5: 27
    q = [lot.quantity for lot in lots.open["AAA.N0000"]]
    assert sum(q) == book.pos["AAA.N0000"][0] == 27 and q == [22, 5]
    assert [lot.cost for lot in lots.open["AAA.N0000"]] == [1050, 264]  # cost stays with each lot


def test_bonus_when_none_held_is_a_lot_that_cost_nothing_and_oversold_shares_are_unmatched():
    # rights sold under their own symbol, entered as a BONUS of the quantity received
    tx = make_tx([("2024-02-01", "SELL", "HNB.R0000", 50, 4.0), ("2024-03-01", "SELL", "XXX.N0000", 10, 5.0)], fee=0)
    a = make_actions([{"date": "2024-01-15", "symbol": "HNB.R0000", "action": "BONUS", "quantity": 50}])
    _, lots = replay(tx, a)
    t = trips_frame(lots.trips)
    assert t[["symbol", "cost", "proceeds", "matched"]].values.tolist() == [
        ["HNB.R0000", 0.0, 200.0, True], ["XXX.N0000", 50.0, 50.0, False]]


def test_delisting_closes_every_lot_as_a_non_market_sale():
    tx = make_tx([("2024-01-01", "BUY", "EXPO.N0000", 100, 150.0)], fee=0)
    sale = tx.iloc[0].copy()
    sale[["trade_date", "side", "price", "gross_amount", "net_amount", "sources"]] = [
        pd.Timestamp("2024-06-10"), "SELL", 185.0, 18500.0, 18500.0, "delisting"]
    _, lots = replay(pd.concat([tx, sale.to_frame().T], ignore_index=True), None)
    assert not lots.open["EXPO.N0000"] and lots.trips[0].market is False and lots.trips[0].pnl == 3500
