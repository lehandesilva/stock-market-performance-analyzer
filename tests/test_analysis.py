import pandas as pd
import pytest

from smpa.actions import load_actions
from smpa.analysis import (benchmark, cash_flows, compute, held_between, invested_over_time, monthly, rights_as_trades,
                           value_history, xirr)
from smpa.errors import PipelineStop


def tx(rows):
    df = pd.DataFrame(rows, columns=["trade_date", "side", "symbol", "quantity", "price", "fees", "contract_no"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["gross_amount"] = df["quantity"] * df["price"]
    df["net_amount"] = df["gross_amount"] + df["fees"].where(df["side"] == "BUY", -df["fees"])
    return df


def test_average_cost_and_realized():
    df = tx([
        ("2024-01-01", "BUY", "AAA", 100, 10.0, 0, 1),
        ("2024-02-01", "BUY", "AAA", 100, 20.0, 0, 1),
        ("2024-03-01", "SELL", "AAA", 50, 30.0, 0, 1),
    ])
    pf = compute(df, {"AAA": 25.0})
    [h] = pf.holdings.to_dict("records")
    assert h["quantity"] == 150 and h["avg_cost"] == 15.0 and h["cost_basis"] == 2250.0
    assert h["market_value"] == 3750.0 and h["unrealized_pnl"] == 1500.0
    assert pf.realized_pnl == 750.0  # 50 * (30 - 15)
    assert pf.net_invested == 3000 - 1500
    assert invested_over_time(df)["net_invested"].tolist() == [1000, 3000, 1500]
    assert monthly(pf.realized, "pnl")["pnl"].tolist() == [750.0]


def test_fees_in_cost_and_proceeds():
    df = tx([("2024-01-01", "BUY", "AAA", 10, 10.0, 5, 1), ("2024-01-02", "SELL", "AAA", 10, 12.0, 5, 1)])
    pf = compute(df)
    assert pf.realized_pnl == pytest.approx(115 - 105)
    assert pf.holdings.empty and pf.fees == 10


def test_sell_without_history_warns_and_counts_zero_gain():
    pf = compute(tx([("2024-01-01", "SELL", "AAA", 10, 10.0, 0, 1)]))
    assert pf.warnings and pf.realized_pnl == 0


def test_holdings_columns_stay_numeric_without_prices():
    pf = compute(tx([("2024-01-01", "BUY", "AAA", 10, 10.0, 0, "1")]))
    assert pf.holdings["market_value"].dtype == float and pf.holdings["market_value"].isna().all()


def actions(tmp_path, text):
    path = tmp_path / "corporate_actions.csv"
    path.write_text(text)
    return load_actions(path)


def test_bonus_and_split_change_shares_not_cost(tmp_path):
    df = tx([("2024-01-01", "BUY", "AAA.N0000", 100, 10.0, 0, 1), ("2024-06-01", "SELL", "AAA.N0000", 55, 10.0, 0, 2)])
    a = actions(tmp_path, "date,symbol,action,ratio\n2024-03-01,AAA.N0000,BONUS,1:10\n2024-04-01,AAA.N0000,SPLIT,2:1\n")
    pf = compute(df, actions=a)
    [h] = pf.holdings.to_dict("records")
    # 100 -> +10 bonus -> 110 -> split 2:1 -> 220; cost stays 1000, so avg cost 1000/220
    assert h["quantity"] == 165 and h["cost_basis"] == pytest.approx(1000 * 165 / 220)
    assert pf.realized_pnl == pytest.approx(550 - 1000 * 55 / 220)
    assert pf.actions["shares"].tolist() == [10, 110] and not pf.warnings


def test_bonus_quantity_overrides_ratio_and_fractions_round_down(tmp_path):
    df = tx([("2024-01-01", "BUY", "AAA.N0000", 105, 10.0, 0, 1)])
    a = actions(tmp_path, "date,symbol,action,ratio,quantity\n2024-02-01,AAA.N0000,BONUS,1:10,\n"
                          "2024-03-01,AAA.N0000,BONUS,1:10,12\n2024-04-01,AAA.N0000,SPLIT,1:5,\n")
    pf = compute(df, actions=a)
    assert pf.actions["shares"].tolist() == [10, 12, 25 - 127]  # 105 + 10 + 12 = 127 -> 127/5 -> 25
    assert pf.holdings["quantity"].tolist() == [25] and pf.actions["detail"].iloc[2] == "consolidation 1:5"


def test_dividend_per_share_counts_shares_held_before_that_days_trades(tmp_path):
    df = tx([("2024-01-01", "BUY", "AAA.N0000", 100, 10.0, 0, 1), ("2024-02-01", "BUY", "AAA.N0000", 50, 10.0, 0, 2)])
    a = actions(tmp_path, "date,symbol,action,per_share,amount,note\n2024-02-01,AAA.N0000,DIVIDEND,1.5,,final\n"
                          "2024-03-01,AAA.N0000,DIVIDEND,,99.50,interim\n2024-03-01,BBB.N0000,DIVIDEND,2,,\n")
    pf = compute(df, actions=a)
    assert pf.dividends["amount"].tolist() == [150.0, 99.5, 0.0]
    assert pf.dividend_income == 249.5 and pf.holdings["dividends"].tolist() == [249.5]
    assert len(pf.warnings) == 1 and "BBB.N0000" in pf.warnings[0]  # per share, but none held


def test_rights_subscription_is_a_purchase(tmp_path):
    a = actions(tmp_path, "date,symbol,action,quantity,price\n2024-05-01,AAA.N0000,RIGHTS,20,8\n")
    df = pd.concat([tx([("2024-01-01", "BUY", "AAA.N0000", 100, 10.0, 0, 1)]), rights_as_trades(a)], ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    pf = compute(df, actions=a)
    [h] = pf.holdings.to_dict("records")
    assert h["quantity"] == 120 and h["cost_basis"] == 1160 and pf.net_invested == 1160
    assert pf.actions[["action", "shares", "amount"]].values.tolist() == [["RIGHTS", 20, -160]]


def test_xirr_matches_excel():
    d = pd.Timestamp
    # Microsoft's XIRR documentation example: 0.373362535
    flows = [(d("2008-01-01"), -10000), (d("2008-03-01"), 2750), (d("2008-10-30"), 4250),
             (d("2009-02-15"), 3250), (d("2009-04-01"), 2750)]
    assert xirr(flows) == pytest.approx(0.373362535, abs=1e-7)
    assert xirr([(d("2021-01-01"), -1000), (d("2022-01-01"), 1100)]) == pytest.approx(0.10)
    assert xirr([(d("2021-01-01"), -1000), (d("2022-01-01"), 500)]) == pytest.approx(-0.5)
    assert xirr([(d("2021-01-01"), -1000)]) is None  # nothing back yet: undefined


def test_cash_flows_include_dividends(tmp_path):
    df = tx([("2024-01-01", "BUY", "AAA.N0000", 10, 10.0, 1, 1), ("2024-02-01", "SELL", "AAA.N0000", 5, 12.0, 1, 2)])
    a = actions(tmp_path, "date,symbol,action,amount\n2024-03-01,AAA.N0000,DIVIDEND,4\n")
    pf = compute(df, actions=a)
    assert [amt for _, amt in cash_flows(df, pf.dividends)] == [-101, 59, 4]


@pytest.mark.parametrize("text, error", [
    ("date,symbol,action\n2024-01-01,AAA.N0000,MERGER\n", "unknown action"),
    ("date,symbol,action,ratio\n2024-01-01,AAA.N0000,BONUS,1/10\n", "must look like 1:10"),
    ("date,symbol,action,amount,per_share\n2024-01-01,AAA.N0000,DIVIDEND,10,1\n", "exactly one"),
    ("date,symbol,action,amount\n2024-01-01,AAA.N0000,SPLIT,10\n", "doesn't apply to SPLIT"),
    ("date,symbol,action,ammount\n2024-01-01,AAA.N0000,DIVIDEND,10\n", "unknown column"),
    ("date,symbol,action,quantity\n2024-01-01,AAA.N0000,RIGHTS,10\n", "quantity subscribed and the"),
    ("date,symbol,action,quantity,price\n2024-01-01,AAA.N0000,RIGHTS,1.5,8\n", "whole number"),
    ("date,symbol,action,amount\n01/02/2024,AAA.N0000,DIVIDEND,10\n", "YYYY-MM-DD"),
    ("date,symbol,action,amount\n2024-02-30,AAA.N0000,DIVIDEND,10\n", "not a real date"),
    ("date,symbol,action,amount\n2024-01-01,AAA,DIVIDEND,10\n", "should look like"),
])
def test_bad_action_rows_stop_with_line_number(tmp_path, text, error):
    with pytest.raises(PipelineStop, match=error) as exc:
        actions(tmp_path, text)
    assert "corporate_actions.csv" in str(exc.value)


def test_action_file_is_optional_and_tolerates_blank_lines_and_case(tmp_path):
    assert load_actions(tmp_path / "missing.csv").empty
    a = actions(tmp_path, "date,symbol,action,amount,note\n\n2024-01-01, aaa.n0000 ,dividend,\"1,000.50\",\n")
    assert a[["symbol", "action", "amount"]].values.tolist() == [["AAA.N0000", "DIVIDEND", 1000.5]]


def test_value_history_only_counts_days_with_every_price(tmp_path):
    df = tx([("2024-01-02", "BUY", "AAA.N0000", 100, 10.0, 0, 1), ("2024-01-05", "BUY", "BBB.N0000", 10, 50.0, 0, 2)])
    a = actions(tmp_path, "date,symbol,action,ratio\n2024-01-08,AAA.N0000,SPLIT,2:1\n")
    ph = pd.DataFrame({"symbol": ["AAA.N0000", "AAA.N0000", "AAA.N0000", "BBB.N0000"],
                       "date": ["2024-01-01", "2024-01-03", "2024-01-09", "2024-01-10"], "price": [9, 11, 6, 55]})
    ih = pd.DataFrame({"date": ["2024-01-03", "2024-01-10"], "value": [100.0, 104.0]})
    h = value_history(df, a, ph, ih, pd.Timestamp("2024-01-10"))
    assert h["date"].dt.strftime("%m-%d").tolist() == ["01-03", "01-09", "01-10"]  # 01-01 is before the first trade
    assert h["value"].tolist()[0] == 1100 and pd.isna(h["value"].iloc[1])  # no BBB price yet on 01-09
    assert h["value"].iloc[2] == 200 * 6 + 10 * 55  # split doubled AAA; its 01-09 price carries to 01-10
    assert h["invested"].tolist() == [1000, 1500, 1500] and h["index"].tolist()[0] == 100.0
    assert h["fresh"].tolist() == [False, False, False]  # AAA was bought on 01-02, before the first day shown


def test_held_between():
    df = tx([("2024-01-01", "BUY", "AAA.N0000", 1, 1.0, 0, 1), ("2024-02-01", "SELL", "AAA.N0000", 1, 1.0, 0, 2),
             ("2024-01-01", "BUY", "BBB.N0000", 1, 1.0, 0, 3), ("2024-06-01", "SELL", "BBB.N0000", 1, 1.0, 0, 4),
             ("2024-07-01", "BUY", "CCC.N0000", 1, 1.0, 0, 5)]).sort_values("trade_date")
    assert held_between(df, None, "2024-03-01") == ["BBB.N0000", "CCC.N0000"]


def test_benchmark_puts_the_same_money_in_the_index():
    d = pd.Timestamp
    hist = pd.DataFrame({"date": [d("2024-01-01"), d("2024-02-01"), d("2024-03-01")],
                         "value": [1000.0, 1600.0, 1700.0], "index": [100.0, 105.0, 110.0]})
    ih = pd.DataFrame({"date": ["2024-01-01", "2024-02-01", "2024-03-01"], "value": [100.0, 105.0, 110.0]})
    flows = [(d("2023-06-01"), -999.0), (d("2024-02-01"), -500.0)]  # before the window: already in the start value
    equiv, b = benchmark(hist, flows, ih)
    units = 1000 / 100 + 500 / 105
    assert equiv.tolist() == pytest.approx([1000, units * 105, units * 110])
    assert b["start"] == d("2024-01-01") and b["days"] == 60
    assert b["period_return"] > b["index_period_return"] > 0  # beat a 10% rise in the index
    assert benchmark(hist.assign(index=float("nan")), flows, ih)[1] is None


def test_benchmark_from_the_first_trade_uses_prices_paid():
    d = pd.Timestamp
    hist = pd.DataFrame({"date": [d("2024-01-01"), d("2025-01-01")], "value": [1200.0, 1320.0],
                         "index": [100.0, 100.0], "fresh": [True, False]})
    ih = pd.DataFrame({"date": ["2024-01-01"], "value": [100.0]})
    equiv, b = benchmark(hist, [(d("2024-01-01"), -1000.0)], ih)  # paid 1000; closed that day at 1200
    assert equiv.tolist() == [1000.0, 1000.0]  # a flat index keeps what was paid
    assert b["xirr"] == pytest.approx(xirr([(d("2024-01-01"), -1000), (d("2025-01-01"), 1320)]))
    assert b["index_xirr"] == pytest.approx(0, abs=1e-9)
