"""Trading habits (docs/05): the docs' worked examples and small hand-checked cases."""

import pandas as pd
import pytest

from conftest import make_actions, make_tx
from smpa.habits import (_Closes, _history, disposition, fees_by_year, frozen_years, lot_outcomes, round_trips,
                         switches, trailing_stop, wilson)
from smpa.lots import replay, trips_frame


def closes(rows):
    """(symbol, date, price) rows -> price history."""
    return pd.DataFrame(rows, columns=["symbol", "date", "price"]).assign(date=lambda d: pd.to_datetime(d["date"]))


def test_wilson_range_from_the_docs():
    lo, hi = wilson(7, 12)
    assert (round(lo, 2), round(hi, 2)) == (0.32, 0.81)
    assert wilson(0, 0) is None


def test_disposition_on_the_docs_two_selling_days():
    # day 1: A sold (up), B kept (up), C kept (down); day 2: D sold (up), B kept (up), C and E kept (down)
    tx = make_tx([("2024-01-02", "BUY", s, 100, 10.0) for s in ("A.N0000", "B.N0000", "C.N0000")]
                 + [("2024-03-15", "BUY", s, 100, 10.0) for s in ("D.N0000", "E.N0000")]
                 + [("2024-03-01", "SELL", "A.N0000", 100, 12.0), ("2024-04-01", "SELL", "D.N0000", 100, 11.0)], fee=0)
    ph = closes([("B.N0000", "2024-02-28", 12.0), ("C.N0000", "2024-02-28", 8.0), ("B.N0000", "2024-03-29", 12.0),
                 ("C.N0000", "2024-03-29", 8.0), ("E.N0000", "2024-03-29", 9.0)])
    d = disposition(tx, None, ph)
    assert d["pgr"]["value"] == 0.5 and d["plr"]["value"] == 0.0 and d["difference"] == 0.5
    assert (d["pgr"]["n"], d["plr"]["n"], d["counts"]["days"]) == (4, 3, 2)


def test_round_trip_statistics():
    tx = make_tx([("2024-01-02", "BUY", "A.N0000", 100, 10.0), ("2024-02-01", "SELL", "A.N0000", 100, 13.0),
                  ("2024-01-02", "BUY", "B.N0000", 100, 10.0), ("2024-04-01", "SELL", "B.N0000", 100, 9.0),
                  ("2024-01-02", "BUY", "C.N0000", 100, 10.0), ("2024-07-01", "SELL", "C.N0000", 100, 8.0)], fee=0)
    _, lots = replay(tx, None)
    r = round_trips(trips_frame(lots.trips))
    assert (r["n"], r["wins"], r["win_rate"]) == (3, 1, pytest.approx(1 / 3))
    assert r["average_win"] == 300 and r["average_loss"] == -150 and r["payoff"] == 2.0
    assert r["profit_factor"] == pytest.approx(1.0) and r["expectancy"] == 0
    assert (r["days_winners"], r["days_losers"]) == (30, pytest.approx((90 + 181) / 2))


def test_trailing_stop_against_holding():
    tx = make_tx([("2024-01-02", "BUY", "A.N0000", 10, 100.0)], fee=0)
    ph = closes([("A.N0000", d, p) for d, p in (("2024-01-02", 100.0), ("2024-02-01", 150.0), ("2024-03-01", 120.0),
                                                 ("2024-04-01", 90.0))])
    walked = _history(tx, None)
    c = _Closes(ph, None)
    lots = lot_outcomes(tx, None, ph, pd.DataFrame(columns=["date", "value"]), "2024-04-01", 0.0, c, walked)
    assert lots.iloc[0]["outcome"] == 900  # held: 10 × 90
    t = trailing_stop(lots, walked[1], walked[2], c, "2024-04-01", lambda days: pd.Series(0.0, index=days),
                      stops=(0.1, 0.2, 0.3)).set_index("stop")
    assert t.loc[0.2, "rule"] == 1200 and t.loc[0.2, "difference"] == 300  # 120 is 20% under the 150 high
    assert t.loc[0.3, "rule"] == 900 and t.loc[0.3, "fired"] == 1  # fired at 90, no better than holding
    assert t.loc[0.1, "rule"] == 1200


def test_frozen_portfolio_and_trading_alpha():
    tx = make_tx([("2024-06-03", "BUY", "A.N0000", 100, 10.0), ("2025-03-03", "SELL", "A.N0000", 50, 11.0)], fee=0)
    actions = make_actions([{"date": "2025-06-02", "symbol": "A.N0000", "action": "DIVIDEND", "per_share": 1.0}])
    ph = closes([("A.N0000", "2024-12-31", 10.0), ("A.N0000", "2025-12-31", 12.0)])
    growth = pd.Series([1.0, 1.1], index=pd.to_datetime(["2024-12-31", "2025-12-31"]))
    _, _, dps, _ = _history(tx, actions)
    f = frozen_years(tx, actions, ph, growth, dps, "2026-01-15").set_index("year")
    # 1 January 2025: 100 shares at 10, left alone: 1,200 at the year's end plus a 100 dividend
    assert f.loc[2025, "frozen"] == pytest.approx(0.30) and f.loc[2025, "alpha"] == pytest.approx(0.10 - 0.30)
    assert 2024 not in f.index  # the time-weighted series doesn't reach back to 1 January 2024


def test_switch_compares_the_new_idea_with_the_old():
    tx = make_tx([("2023-06-01", "BUY", "A.N0000", 100, 8.0), ("2024-01-02", "SELL", "A.N0000", 100, 10.0),
                  ("2024-01-04", "BUY", "B.N0000", 50, 20.0)], fee=0)
    ph = closes([("A.N0000", "2024-04-04", 12.0), ("B.N0000", "2024-04-04", 30.0)])
    [s] = switches(tx, _Closes(ph, None), "2024-05-01").to_dict("records")
    assert (s["from"], s["to"]) == ("A.N0000", "B.N0000")
    assert s["gain_3"] == pytest.approx(1.5 / 1.2 - 1) and s["gain_6"] is None


def test_fees_by_year_split_by_type():
    tx = make_tx([("2024-01-02", "BUY", "A.N0000", 100, 10.0), ("2024-06-03", "SELL", "A.N0000", 100, 12.0)])
    cols = pd.DataFrame([{"contract_no": "1", "brokerage": 6.4, "cds": 0.2, "cse": 0.84, "sec": 0.72, "stl": 3.0,
                          "clearing": 0.04, "foreign_brokerage": 0.0}])
    hist = pd.DataFrame({"date": pd.bdate_range("2024-01-02", periods=30), "value": 1000.0})
    f = fees_by_year(tx, cols, hist, pd.Series(dtype=float, index=pd.DatetimeIndex([]))).iloc[0]
    assert f["brokerage"] == pytest.approx(6.4) and f["unknown"] == pytest.approx(tx["fees"].iloc[1])
    assert f["drag"] == pytest.approx(tx["fees"].sum() / 1000) and f["turnover"] == pytest.approx(1.0)
