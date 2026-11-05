"""Cash at the broker, and the account's return with it (docs/05 §7)."""

import pandas as pd
import pytest

from smpa.analysis import xirr
from smpa.cash import account_return, cash_history, flows_at_broker

D = pd.Timestamp


def balances(rows):
    return pd.DataFrame(rows, columns=["period_start", "period_end", "opening", "closing"])


def flows(rows):
    return pd.Series([a for _, a in rows], index=pd.DatetimeIndex([D(d) for d, _ in rows]))


def test_statements_anchor_the_balance_and_a_gap_that_doesnt_add_up_is_a_break():
    """The test fixtures: March 2021 and October 2026 statements, with trades in between but no deposit."""
    b = balances([("2021-03-01", "2021-03-31", 0.0, 1050.87), ("2026-10-01", "2026-10-30", 25.0, 170.66)])
    f = flows([("2021-03-10", 10_000.0), ("2021-03-15", -8_949.13), ("2026-09-02", -9_910.94), ("2026-09-02", 1_502.80),
               ("2026-10-02", 5_000.0), ("2026-10-06", -4_854.34), ("2026-11-04", 20_000.0)])
    c = cash_history(b, f, "2026-11-05")
    s = c["series"]
    assert s[D("2021-03-31")] == pytest.approx(1050.87) and s[D("2026-09-30")] == pytest.approx(-7_357.27)
    [br] = c["breaks"]  # five years of statements missing: deposits nobody has a document for
    assert (br["date"], br["at"], br["statement"]) == (D("2026-10-01"), "opening", 25.0)
    assert br["worked_out"] == pytest.approx(-7_357.27)
    assert (c["anchor"], c["anchor_cash"]) == (D("2026-09-30"), 25.0)  # the account return starts there
    k = c["known"]  # wrong somewhere between the two statements; right within them, and after the last
    assert k[D("2021-03-31")] and not k[D("2021-04-01")] and not k[D("2026-09-30")] and k[D("2026-10-01")]
    assert br["since"] == D("2021-03-31") and k[D("2026-11-05")]
    assert s[D("2026-10-30")] == pytest.approx(170.66) and c["confirmed"] == 170.66
    assert c["now"] == pytest.approx(20_170.66)  # the receipt after the last statement
    assert cash_history(balances([]), f, "2026-11-05") is None


def test_broker_flows_leave_out_what_goes_through_your_bank():
    tx = pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-03", "2024-02-01", "2024-03-01"]),
                       "side": ["BUY", "SELL", "BUY"], "net_amount": [9_000.0, 500.0, 100.0],
                       "sources": ["contract_note", "manual", "rights issue"]})
    mv = pd.DataFrame({"date": ["2024-01-02", "2024-06-01"], "kind": ["DEPOSIT", "WITHDRAWAL"], "amount": [10_000.0, 300.0]})
    f = flows_at_broker(tx, mv)
    assert f.to_dict() == {D("2024-01-02"): 10_000.0, D("2024-01-03"): -9_000.0, D("2024-02-01"): 500.0,
                           D("2024-06-01"): -300.0}  # the rights subscription was paid to the company


def test_cash_drag_is_the_shares_return_less_the_accounts():
    b = balances([("2024-01-01", "2024-01-31", 0.0, 1_000.0)])
    f = flows([("2024-01-02", 10_000.0), ("2024-01-03", -9_000.0)])
    c = cash_history(b, f, "2025-01-02")
    assert (c["anchor"], c["anchor_cash"], c["breaks"]) == (D("2023-12-31"), 0.0, [])
    tbill = pd.Series([0.10], index=[D("2023-12-01")])
    r = account_return(c, 0.0, [(D("2024-01-02"), -10_000.0)], [(D("2024-01-03"), -9_000.0)], 9_900.0, "2025-01-02",
                       tbill=tbill)
    assert r["account"] == pytest.approx(xirr([(D("2024-01-02"), -10_000), (D("2025-01-02"), 10_900)]))
    assert r["shares"] == pytest.approx(0.10, abs=1e-6)  # 9,000 -> 9,900 in a year
    assert r["drag"] == pytest.approx(r["shares"] - r["account"]) and 0.009 < r["drag"] < 0.012
    assert r["tbill_forgone"] == pytest.approx((10_000 + 1_000 * 366) * 0.10 / 365)  # 366 days from 3 Jan 2024 on
    assert account_return(c, None, [], [], 9_900.0, "2025-01-02")["account"] is None  # shares then unknown
