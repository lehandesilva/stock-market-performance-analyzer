"""Shared test data: the worked example from docs/03-what-if-i-sold.md."""

import pandas as pd
import pytest

from smpa.actions import empty_actions

TX_COLS = ["trade_date", "side", "symbol", "quantity", "price", "gross_amount", "fees", "net_amount", "contract_no",
           "sources", "company"]


def make_tx(rows, fee=0.0112):
    """(date, side, symbol, quantity, price) rows -> transactions, each paying `fee` of its gross."""
    out = []
    for i, (d, side, sym, q, p) in enumerate(rows, 1):
        gross = q * p
        f = round(gross * fee, 2)
        out.append((pd.Timestamp(d), side, sym, q, p, gross, f, gross + f if side == "BUY" else gross - f, str(i),
                    "contract_note", None))
    return pd.DataFrame(out, columns=TX_COLS).sort_values(["trade_date", "side", "contract_no"]).reset_index(drop=True)


def make_actions(rows):
    """(date, symbol, action, field=value...) dicts -> a corporate actions frame like load_actions gives."""
    df = empty_actions()
    if rows:
        df = pd.DataFrame([{**{c: None for c in df.columns}, "note": "", "tax": 0.0, **r} for r in rows],
                          columns=df.columns)
        df["date"] = pd.to_datetime(df["date"])
        df["recorded_on"] = pd.to_datetime(df["recorded_on"])
        for c in ("ratio_new", "ratio_old", "quantity", "price", "amount", "per_share", "tax"):
            df[c] = df[c].astype(float)
    return df


@pytest.fixture
def abc():
    """ABC.N0000: bought twice, partly sold, two dividends; fees 1.12% on every trade (docs 02 and 03)."""
    tx = make_tx([("2024-02-01", "BUY", "ABC.N0000", 1000, 40.0), ("2024-08-01", "BUY", "ABC.N0000", 500, 60.0),
                  ("2025-06-02", "SELL", "ABC.N0000", 600, 68.0)])
    actions = make_actions([{"date": "2024-11-01", "symbol": "ABC.N0000", "action": "DIVIDEND", "amount": 3000.0},
                            {"date": "2025-11-03", "symbol": "ABC.N0000", "action": "DIVIDEND", "amount": 1350.0}])
    closes = pd.DataFrame({"symbol": "ABC.N0000", "date": pd.to_datetime(["2025-03-03", "2026-11-05"]),
                           "price": [75.0, 55.0]})
    return tx, actions, closes
