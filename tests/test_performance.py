"""Return and risk maths, against the examples in docs/04-performance-and-risk.md."""

import numpy as np
import pandas as pd
import pytest

from conftest import make_actions
from smpa.adjust import actual_closes, adjusted_closes, multiplier, total_return_closes, unrecorded_splits
from smpa.performance import (attribution, concentration, converted_xirr, direct_alpha, dimson_beta, ks_pme,
                              level_series, max_drawdown, modified_dietz, regression, returns, risk_contributions,
                              tbill_index, trading_days_per_year, twr_index, volatility)

D = pd.Timestamp


def test_max_drawdown_and_recovery():
    g = pd.Series([100, 120, 90, 110, 80, 130], index=pd.date_range("2024-01-01", periods=6))
    dd = max_drawdown(g)
    assert dd["depth"] == pytest.approx(-1 / 3) and (dd["peak"], dd["trough"], dd["recovered"]) == (
        D("2024-01-02"), D("2024-01-05"), D("2024-01-06"))
    assert max_drawdown(g[:5])["recovered"] is None  # still under water


def test_cse_calendar_not_252_days():
    weekdays = pd.bdate_range("2025-11-05", "2026-11-04")
    open_days = weekdays.delete(range(5, 5 + (len(weekdays) - 240) * 10, 10))  # Poya days and holidays: closed
    assert len(open_days) == 240
    assert trading_days_per_year(pd.DataFrame({"date": open_days, "value": 1.0})) == 240
    assert trading_days_per_year(None) == 240
    r = pd.Series([0.01, -0.01] * 50)
    assert volatility(r, 240) == pytest.approx(r.std() * 240 ** 0.5)


def test_dimson_beta_recovers_a_thinly_traded_stocks_beta():
    rng = np.random.default_rng(1)
    m = pd.Series(rng.normal(0, 0.01, 2000))
    s = 0.6 * m + 0.6 * m.shift(1).fillna(0) + pd.Series(rng.normal(0, 0.004, 2000))  # half the reaction a day late
    assert regression(s, m, 240)["beta"] == pytest.approx(0.6, abs=0.05)  # biased towards zero
    assert dimson_beta(s, m) == pytest.approx(1.2, abs=0.05)


def test_ks_pme_and_direct_alpha():
    flat = level_series(pd.DataFrame({"date": ["2024-01-01", "2026-01-01"], "value": [100.0, 100.0]}))
    flows = [(D("2024-01-01"), -1000.0), (D("2025-01-01"), 3000.0)]
    assert ks_pme(flows, 0.0, D("2026-01-01"), flat) == pytest.approx(3.0)  # the old method went to -2,000
    days = pd.date_range("2020-01-01", "2026-01-01")
    index = pd.Series(100 * 1.1 ** ((days - days[0]).days / 365), index=days)
    same = [(D("2020-01-01"), -1000.0), (D("2022-06-01"), -500.0), (D("2024-03-01"), 400.0)]
    end_value = 1000 * index.iloc[-1] / 100 + 500 * index.iloc[-1] / index[D("2022-06-01")] - 400 * index.iloc[-1] / index[D("2024-03-01")]
    assert direct_alpha(same, end_value, days[-1], index) == pytest.approx(0, abs=1e-6)  # tracks the index exactly
    ahead = 1000 * (1.1 * 1.05) ** ((days[-1] - days[0]).days / 365)
    assert direct_alpha([(D("2020-01-01"), -1000.0)], ahead, days[-1], index) == pytest.approx(0.05, abs=1e-6)
    assert returns(pd.Series([100.0, 110.0, 99.0])).tolist() == pytest.approx([0.1, -0.1])


def hist(prices, symbol="HHL.N0000", start="2025-05-01"):
    return pd.DataFrame({"symbol": symbol, "date": pd.bdate_range(start, periods=len(prices)), "price": prices})


SPLIT = make_actions([{"date": "2025-05-07", "symbol": "HHL.N0000", "action": "SPLIT", "ratio_new": 5, "ratio_old": 1}])


def test_split_found_in_stored_history_either_way():
    raw = hist([150.0, 151, 149, 150, 30.2, 30.5, 31])  # unadjusted: drops to 1/5 on 2025-05-07
    adj = hist([30.0, 30.2, 29.8, 30, 30.2, 30.5, 31])  # the CSE divided earlier closes by 5
    for h in (raw, adj):
        actual, [found] = actual_closes(h, SPLIT, "HHL.N0000")
        assert actual.iloc[0] == pytest.approx(150, rel=0.01) and actual.iloc[-1] == 31  # what was really paid
        assert adjusted_closes(h, SPLIT, "HHL.N0000").iloc[0] == pytest.approx(30, rel=0.01)  # on today's basis
    assert actual_closes(raw, SPLIT, "HHL.N0000")[1][0]["history"] == "unadjusted"
    assert actual_closes(adj, SPLIT, "HHL.N0000")[1][0]["history"] == "adjusted"
    assert multiplier(SPLIT, "HHL.N0000", "2025-01-01", "2026-01-01") == 5
    assert unrecorded_splits(raw, None, "HHL.N0000") and not unrecorded_splits(raw, SPLIT, "HHL.N0000")


def test_twr_ignores_when_money_went_in():
    # 1,000 doubles to 2,000; 10,000 more goes in; then everything halves: the picks ended where they started
    days = pd.to_datetime(["2023-01-02", "2024-01-01", "2024-01-02", "2025-01-02"])
    hist = pd.DataFrame({"date": days, "value": [1000.0, 2000.0, 12000.0, 6000.0], "fresh": [True, False, False, False]})
    flows = [(days[0], -1000.0), (days[2], -10000.0)]
    g = twr_index(hist, flows)
    assert g.tolist() == pytest.approx([1.0, 2.0, 2.0, 1.0])  # TWR 0%
    from smpa.analysis import xirr
    assert xirr(flows + [(days[3], 6000.0)]) == pytest.approx(-0.431, abs=1e-3)  # XIRR −43.1% a year


def test_twr_starts_at_one_when_something_was_already_held_and_flows_join_the_next_valued_day():
    days = pd.to_datetime(["2024-01-01", "2024-01-03"])
    hist = pd.DataFrame({"date": days, "value": [100.0, 165.0], "fresh": [False, False]})
    g = twr_index(hist, [(D("2023-06-01"), -999.0), (D("2024-01-02"), -50.0)])  # 01-02 had no valuation
    assert g.tolist() == pytest.approx([1.0, 165 / 150])


def test_modified_dietz_example():
    r = modified_dietz(100_000, 160_000, [(D("2024-06-20"), 50_000)], D("2024-05-31"), D("2024-06-30"))
    assert round(r, 4) == 0.0857


def test_concentration_and_risk_contributions_add_up():
    c = concentration(pd.Series({"A": 40.0, "B": 30.0, "C": 20.0, "D": 10.0}))
    assert c["hhi"] == pytest.approx(0.30) and round(c["effective"], 1) == 3.3 and c["top3"] == pytest.approx(0.9)
    rng = np.random.default_rng(3)
    m = rng.normal(0, 0.01, 300)
    rets = pd.DataFrame({"A": m + rng.normal(0, 0.01, 300), "B": m + rng.normal(0, 0.02, 300), "C": -m + rng.normal(0, 0.01, 300)})
    rc = risk_contributions(rets, pd.Series({"A": 0.5, "B": 0.3, "C": 0.2}), 240)
    assert sum(x["contribution"] for x in rc["holdings"]) == pytest.approx(rc["volatility"])
    assert next(x for x in rc["holdings"] if x["symbol"] == "C")["contribution"] < 0  # moves against the rest
    assert rc["diversification_ratio"] > 1


def test_attribution_adds_up_to_the_portfolio_return():
    a = attribution({"A": 1000.0, "B": 500.0}, {"A": 1300.0, "B": 700.0, "C": 210.0},
                    {"B": [(D("2024-07-01"), 100.0)], "C": [(D("2024-04-01"), 200.0)]}, D("2024-01-01"), D("2025-01-01"))
    total = (2210 - 1500 - 300) / (1500 + 100 * 184 / 366 + 200 * 275 / 366)
    assert a["contribution"].sum() == pytest.approx(total)
    assert a.set_index("symbol").loc["A", "gain"] == 300


def test_tbill_account_and_converted_returns():
    y = pd.Series([0.10], index=[D("2024-01-01")])
    level = tbill_index(y, pd.date_range("2024-01-01", "2025-01-01"))
    assert level.iloc[-1] / level.iloc[0] == pytest.approx((1 + 0.1 / 365) ** 366, rel=1e-9)
    flows = [(D("2024-01-01"), -1000.0)]
    assert converted_xirr(flows, 1100.0, D("2025-01-01"), lambda d: 1.0) == pytest.approx(xirr_(flows, 1100.0))
    assert converted_xirr(flows, 1100.0, D("2025-01-01"), lambda d: None) is None


def xirr_(flows, final):
    from smpa.analysis import xirr
    return xirr(flows + [(D("2025-01-01"), final)])


def test_data_files_are_checked_strictly(tmp_path):
    from smpa.errors import PipelineStop
    from smpa.extdata import load_ccpi, load_fx, load_indices, load_sectors, load_tbill

    (tmp_path / "tbill.csv").write_text("date,yield\n2024-01-03,13.95\n2024-01-10,13.80\n")
    assert load_tbill(tmp_path / "tbill.csv").tolist() == pytest.approx([0.1395, 0.138])
    (tmp_path / "ccpi.csv").write_text("month,index\n2022-09,240.5\n")
    assert load_ccpi(tmp_path / "ccpi.csv").index[0] == D("2022-09-01")
    assert load_fx(tmp_path / "missing.csv").empty
    for name, text, error in [("fx.csv", "date,rate\n2024-01-01,300\n", "missing column"),
                              ("tbill.csv", "date,yield\n2024-01-03,0.1395\n2024-01-03,0.14\n", "line 3: 2024-01-03 appears twice"),
                              ("tbill.csv", "date,yield\n2024-01-03,139\n", "below 100"),
                              ("indices.csv", "name,date,value\nASPI,2024-01-01,10000\n", "use your own name"),
                              ("sectors.csv", "symbol,sector\nJKH,Capital Goods\n", "should look like JKH.N0000")]:
        (tmp_path / name).write_text(text)
        loader = {"fx.csv": load_fx, "tbill.csv": load_tbill, "indices.csv": load_indices, "sectors.csv": load_sectors}[name]
        with pytest.raises(PipelineStop, match=error):
            loader(tmp_path / name)


def test_total_return_closes_put_dividends_back():
    """A 5.00 dividend on a 100.00 share: the ex-date drop to 95 is no loss once the dividend is counted."""
    h = hist([100.0, 100.0, 95.0, 96.0], symbol="ABC.N0000", start="2025-06-02")
    per_share = pd.DataFrame({"symbol": ["ABC.N0000"], "date": [D("2025-06-04")], "per_share": [5.0]})
    tr = total_return_closes(h, None, "ABC.N0000", per_share)
    assert tr.tolist() == pytest.approx([95.0, 95.0, 95.0, 96.0]) and returns(tr).iloc[1] == 0
    # with a later 2:1 split recorded: the dividend is set against the close as traded then, so the factor holds
    split = make_actions([{"date": "2025-06-06", "symbol": "ABC.N0000", "action": "SPLIT", "ratio_new": 2, "ratio_old": 1}])
    h2 = hist([100.0, 100.0, 95.0, 96.0, 48.5], symbol="ABC.N0000", start="2025-06-02")
    tr2 = total_return_closes(h2, split, "ABC.N0000", per_share)
    assert tr2.tolist() == pytest.approx([47.5, 47.5, 47.5, 48.0, 48.5])
    assert total_return_closes(h, None, "ABC.N0000", None).tolist() == h["price"].tolist()  # no dividends known
