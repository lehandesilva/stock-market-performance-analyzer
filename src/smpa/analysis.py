"""Portfolio maths on parsed transactions (average-cost method)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

TX_COLUMNS = [
    "trade_date", "side", "symbol", "quantity", "price", "gross_amount", "fees",
    "net_amount", "document_id", "document", "raw_line",
]
DEDUPE_KEY = ["trade_date", "side", "symbol", "quantity", "price_key"]


@dataclass
class Portfolio:
    transactions: pd.DataFrame
    holdings: pd.DataFrame
    realized: pd.DataFrame
    warnings: list[str] = field(default_factory=list)

    @property
    def net_invested(self) -> float:
        t = self.transactions
        return float(t.loc[t.side == "BUY", "net_amount"].sum() - t.loc[t.side == "SELL", "net_amount"].sum())

    @property
    def realized_pnl(self) -> float:
        return float(self.realized["pnl"].sum()) if len(self.realized) else 0.0

    @property
    def fees(self) -> float:
        return float(self.transactions["fees"].sum())


def load_transactions(conn: sqlite3.Connection, manual_csv: Path | None = None) -> pd.DataFrame:
    df = pd.read_sql_query(
        """SELECT t.trade_date, t.side, t.symbol, t.quantity, t.price, t.gross_amount, t.fees,
                  t.net_amount, t.document_id, d.attachment_name AS document, t.raw_line
           FROM transactions t JOIN documents d ON d.id = t.document_id""",
        conn,
    )
    if manual_csv and manual_csv.exists():
        m = load_manual(manual_csv)
        df = m if df.empty else pd.concat([df, m], ignore_index=True)
    if df.empty:
        return pd.DataFrame(columns=TX_COLUMNS)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["side"] = df["side"].str.upper()
    df["symbol"] = df["symbol"].str.upper().str.strip()
    return dedupe(df)


def load_manual(path: Path) -> pd.DataFrame:
    """Extra trades the parser missed: trade_date,side,symbol,quantity,price[,fees]."""
    m = pd.read_csv(path)
    m["fees"] = m["fees"] if "fees" in m else 0.0
    m["fees"] = m["fees"].fillna(0.0)
    m["side"] = m["side"].str.upper()
    m["gross_amount"] = m["quantity"] * m["price"]
    m["net_amount"] = m["gross_amount"] + m["fees"].where(m["side"] == "BUY", -m["fees"])
    m["document_id"] = -1
    m["document"] = "manual_transactions.csv"
    m["raw_line"] = ""
    return m[TX_COLUMNS]


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """The same trade can appear in a contract note *and* a monthly statement.

    Keep, per (date, side, symbol, qty, price), the highest count seen in any
    single document instead of summing across documents.
    """
    df = df.copy()
    df["price_key"] = df["price"].round(2)
    df["_n"] = df.groupby(DEDUPE_KEY + ["document_id"]).cumcount()
    df = df.sort_values("document_id").drop_duplicates(DEDUPE_KEY + ["_n"], keep="first")
    df = df.drop(columns=["_n", "price_key"])
    # BUY sorts before SELL on the same day.
    return df.sort_values(["trade_date", "side"], kind="stable").reset_index(drop=True)[TX_COLUMNS]


def load_prices(path: Path) -> dict[str, float]:
    """Optional data/prices.csv with columns symbol,price (latest market prices)."""
    if not path.exists():
        return {}
    p = pd.read_csv(path)
    return {str(s).upper().strip(): float(v) for s, v in zip(p["symbol"], p["price"])}


def compute(tx: pd.DataFrame, prices: dict[str, float] | None = None) -> Portfolio:
    prices = prices or {}
    pos: dict[str, tuple[float, float]] = {}  # symbol -> (quantity, cost basis)
    realized, warnings = [], []
    for r in tx.itertuples(index=False):
        qty, cost = pos.get(r.symbol, (0.0, 0.0))
        if r.side == "BUY":
            qty, cost = qty + r.quantity, cost + r.net_amount
        else:
            known = min(r.quantity, qty)
            if r.quantity > qty + 1e-9:
                warnings.append(
                    f"{r.trade_date:%Y-%m-%d}: sold {r.quantity:g} {r.symbol} but only {qty:g} "
                    "bought in the data - missing earlier statements? Unknown lots counted at zero gain."
                )
            avg = cost / qty if qty else 0.0
            cost_known = avg * known
            cost_unknown = r.net_amount * (r.quantity - known) / r.quantity
            cost_sold = cost_known + cost_unknown
            realized.append({
                "trade_date": r.trade_date, "symbol": r.symbol, "quantity": r.quantity,
                "proceeds": r.net_amount, "cost": cost_sold, "pnl": r.net_amount - cost_sold,
            })
            qty, cost = qty - known, cost - cost_known
            if qty <= 1e-9:
                qty, cost = 0.0, 0.0
        pos[r.symbol] = (qty, cost)

    rows = []
    for sym, (qty, cost) in sorted(pos.items()):
        if qty <= 0:
            continue
        price = prices.get(sym)
        mv = qty * price if price is not None else None
        rows.append({
            "symbol": sym, "quantity": qty, "avg_cost": cost / qty, "cost_basis": cost,
            "price": price, "market_value": mv,
            "unrealized_pnl": mv - cost if mv is not None else None,
            "unrealized_pct": (mv - cost) / cost if mv is not None and cost else None,
        })
    holdings = pd.DataFrame(rows, columns=[
        "symbol", "quantity", "avg_cost", "cost_basis", "price", "market_value",
        "unrealized_pnl", "unrealized_pct",
    ])
    realized_df = pd.DataFrame(realized, columns=["trade_date", "symbol", "quantity", "proceeds", "cost", "pnl"])
    return Portfolio(tx, holdings, realized_df, warnings)


def invested_over_time(tx: pd.DataFrame) -> pd.DataFrame:
    """Cumulative net cash put in (buys minus sell proceeds), per trade date."""
    if tx.empty:
        return pd.DataFrame(columns=["trade_date", "net_invested"])
    flow = tx["net_amount"].where(tx["side"] == "BUY", -tx["net_amount"])
    daily = flow.groupby(tx["trade_date"]).sum().cumsum()
    return daily.rename("net_invested").reset_index()


def monthly(df: pd.DataFrame, value: str, by: str | None = None) -> pd.DataFrame:
    """Sum `value` per calendar month (optionally split by column `by`)."""
    if df.empty:
        return pd.DataFrame(columns=["month", *([by] if by else []), value])
    keys = [df["trade_date"].dt.to_period("M").dt.to_timestamp().rename("month")]
    if by:
        keys.append(df[by])
    return df.groupby(keys)[value].sum().reset_index()
