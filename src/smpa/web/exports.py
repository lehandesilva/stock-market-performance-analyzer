"""CSV downloads (docs/06 §8): the overview's tables as they are on screen, one stock's whole record, and a
yearly summary of realized P&L, dividends and fees for tax time."""

from __future__ import annotations

import sqlite3

import pandas as pd

from ..analysis import compute
from ..config import Config
from ..db import FEE_COLUMNS
from ..habits import FEE_TYPES, fees_by_year
from .api import _records, load_data

TABLES = ("holdings", "transactions", "realized", "sales", "cash", "actions")  # the overview's tabs, by payload key


def table_csv(payload: dict, name: str) -> str:
    """One of the overview's tables, from the payload it was shown from (so "As of" applies)."""
    return pd.DataFrame(payload[name]).to_csv(index=False)


def yearly(tx: pd.DataFrame, realized: pd.DataFrame, log: pd.DataFrame, fee_parts: pd.DataFrame) -> pd.DataFrame:
    """Per calendar year: bought and sold (gross), realized P&L (average cost, after fees both ways), dividends
    received and the tax withheld from them (known for dividends entered per share), and the fees paid, by kind
    where a Contract Note or Trade Confirmation gave it."""
    cols = ["year", "bought", "sold", "realized", "dividends", "withheld", "fees", *FEE_TYPES, "unknown"]
    if tx.empty and log.empty:
        return pd.DataFrame(columns=cols)
    empty = pd.DataFrame(columns=["date", "value"])
    fees = fees_by_year(tx, fee_parts, empty, pd.Series(dtype=float, index=pd.DatetimeIndex([]))).set_index("year")
    divs = log[log["action"] == "DIVIDEND"]
    years = sorted({*tx["trade_date"].dt.year, *divs["date"].dt.year})
    out = []
    for y in range(years[0], years[-1] + 1):
        t = tx[tx["trade_date"].dt.year == y]
        dv = divs[divs["date"].dt.year == y]
        known = dv["withheld"].dropna()
        out.append({"year": y, "bought": float(t.loc[t["side"] == "BUY", "gross_amount"].sum()),
                    "sold": float(t.loc[t["side"] == "SELL", "gross_amount"].sum()),
                    "realized": float(realized.loc[realized["trade_date"].dt.year == y, "pnl"].sum()),
                    "dividends": float(dv["amount"].sum()),
                    "withheld": float(known.sum()) if len(known) else None,
                    **({k: float(fees.at[y, k]) for k in ["fees", *FEE_TYPES, "unknown"]} if y in fees.index else
                       {k: 0.0 for k in ["fees", *FEE_TYPES, "unknown"]})})
    return pd.DataFrame(out, columns=cols)


def yearly_csv(cfg: Config, conn: sqlite3.Connection) -> str:
    d = load_data(cfg, conn)
    pf = compute(d.tx, {}, d.actions)
    parts = pd.read_sql_query(f"SELECT contract_no, {FEE_COLUMNS} FROM trades", conn)
    return yearly(d.tx, pf.realized, pf.actions, parts).round(2).to_csv(index=False)


def stock_csv(cfg: Config, conn: sqlite3.Connection, symbol: str) -> str | None:
    """Everything that happened to one stock, in date order: each trade with its fees (and, for a sale, the
    realized P&L at average cost) and each corporate action. None for a symbol never traded."""
    d = load_data(cfg, conn)
    tx, actions = d.tx[d.tx["symbol"] == symbol], d.actions[d.actions["symbol"] == symbol]
    if tx.empty and actions.empty:
        return None
    pf = compute(tx, {}, actions)
    parts = pd.read_sql_query(f"SELECT contract_no, {FEE_COLUMNS} FROM trades WHERE symbol = ?", conn, params=(symbol,))
    t = tx.merge(parts, on="contract_no", how="left") if len(parts) else tx.assign(**dict.fromkeys(FEE_TYPES))
    t = t.assign(kind=t["side"], realized=float("nan"))
    sells = t.index[t["side"] == "SELL"]
    t.loc[sells, "realized"] = pf.realized["pnl"].to_numpy()  # one row per sale, in the same order
    a = pf.actions[pf.actions["action"].isin(["DIVIDEND", "BONUS", "SPLIT"])]  # rights and buyouts are trades above
    a = a.rename(columns={"action": "kind"}).assign(trade_date=lambda x: x["date"])
    rows = pd.concat([t, a], ignore_index=True).sort_values("trade_date", kind="stable")
    cols = ["trade_date", "kind", "quantity", "price", "gross_amount", *FEE_TYPES, "fees", "net_amount", "realized",
            "contract_no", "sources", "shares", "held", "amount", "withheld", "detail", "note"]
    return pd.DataFrame(_records(rows.reindex(columns=cols).rename(columns={"trade_date": "date"}))).to_csv(index=False)
