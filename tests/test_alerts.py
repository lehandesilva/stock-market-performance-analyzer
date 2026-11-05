"""Alerts: worth a look today, never acted on for you."""

import pandas as pd

from conftest import make_actions
from smpa.alerts import alerts, company_key


def holdings(**over):
    row = {"symbol": "AAA.N0000", "price": 10.0, "break_even": 12.0, "day_change": -0.06, "priced": True}
    return pd.DataFrame([{**row, **over}])


def test_company_names_match_however_written():
    assert company_key("Alpha PLC.") == company_key("ALPHA PLC") == company_key("alpha limited") == "ALPHA"


def test_each_kind_of_alert():
    ph = pd.DataFrame({"symbol": "AAA.N0000", "date": pd.to_datetime(["2026-02-01", "2026-10-01"]), "price": [11.0, 10.5]})
    news = pd.DataFrame({"date": ["2026-11-03", "2026-11-03", "2026-08-01"], "category": ["CASH DIVIDEND", "CORPORATE DISCLOSURE", "CASH DIVIDEND"],
                         "company": ["ALPHA PLC", "NOT HELD PLC", "ALPHA PLC"]})
    a = alerts(holdings(), ph, {"AAA.N0000": 10.0}, {"AAA.N0000": {"Alpha PLC"}}, news, make_actions([]), "2026-11-05")
    kinds = [x["kind"] for x in a]
    assert kinds == ["break-even", "52-week low", "big move", "announcement"]  # the old one and NOT HELD left out
    assert "the CSE says 10.00" in a[1]["message"]  # stored closes span under 300 days: the CSE's low is used
    assert a[3]["suggest"] == "DIVIDEND" and "<ex-date>,AAA.N0000,DIVIDEND with per_share=<amount a share>" in a[3]["message"]


def test_nothing_to_say_and_announcement_already_recorded():
    news = pd.DataFrame({"date": ["2026-11-03"], "category": ["CASH DIVIDEND"], "company": ["ALPHA PLC"]})
    done = make_actions([{"date": "2026-11-20", "symbol": "AAA.N0000", "action": "DIVIDEND", "per_share": 1.0}])
    calm = holdings(price=13.0, day_change=0.01)
    assert alerts(calm, pd.DataFrame(columns=["symbol", "date", "price"]), {"AAA.N0000": 9.0},
                  {"AAA.N0000": {"ALPHA PLC"}}, news, done, "2026-11-05") == []
