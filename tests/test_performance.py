"""Return and risk maths, against the examples in docs/04-performance-and-risk.md."""

import numpy as np
import pandas as pd
import pytest

from conftest import make_actions
from smpa.adjust import actual_closes, adjusted_closes, multiplier, unrecorded_splits
from smpa.performance import (direct_alpha, dimson_beta, ks_pme, level_series, max_drawdown, regression, returns,
                              trading_days_per_year, volatility)

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
