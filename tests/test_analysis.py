import pandas as pd
import pytest

from smpa.analysis import compute, dedupe, invested_over_time, monthly


def tx(rows):
    df = pd.DataFrame(rows, columns=["trade_date", "side", "symbol", "quantity", "price", "fees", "document_id"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["gross_amount"] = df["quantity"] * df["price"]
    df["net_amount"] = df["gross_amount"] + df["fees"].where(df["side"] == "BUY", -df["fees"])
    df["document"] = "x.pdf"
    df["raw_line"] = ""
    return dedupe(df)


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


def test_dedupe_across_documents_keeps_within_document_repeats():
    same = ("2024-01-01", "BUY", "AAA", 10, 10.0, 0)
    df = tx([(*same, 1), (*same, 1), (*same, 2)])  # 2 fills in a contract note, repeated in a statement
    assert len(df) == 2


def test_sell_without_history_warns_and_counts_zero_gain():
    pf = compute(tx([("2024-01-01", "SELL", "AAA", 10, 10.0, 0, 1)]))
    assert pf.warnings and pf.realized_pnl == 0
