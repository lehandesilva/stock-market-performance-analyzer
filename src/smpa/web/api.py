"""JSON payloads for the web dashboard, built from the database with the analysis module."""

from __future__ import annotations

import json
import sqlite3

import pandas as pd

from ..analysis import compute, invested_over_time, load_prices, load_transactions, monthly
from ..config import Config


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-safe records: NaN -> null, dates -> 'YYYY-MM-DD'."""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    return json.loads(df.to_json(orient="records"))


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _mask(account: str) -> str:
    """NBS-12345-AB/00 -> NBS-•••••-AB/00 (screen-sharing friendly)."""
    return "".join("•" if ch.isdigit() and 4 <= i < len(account) - 5 else ch for i, ch in enumerate(account))


def build_payload(cfg: Config, conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    docs = pd.read_sql_query(
        """SELECT id, email_date, attachment_name, doc_type, layout, account, parse_count,
                  COALESCE(parse_error, extract_error) AS error, text_path IS NOT NULL AS has_text
           FROM documents ORDER BY email_date, id""",
        conn,
    )
    issues = pd.read_sql_query("SELECT subject, message FROM issues ORDER BY subject", conn)
    cash = pd.read_sql_query(
        "SELECT date, kind, ref, amount, description, sources FROM cash_movements ORDER BY date", conn)
    tx_all = load_transactions(conn, cfg.manual_csv)
    prices = load_prices(conn, cfg.prices_csv)

    first = tx_all["trade_date"].min() if len(tx_all) else None
    last = tx_all["trade_date"].max() if len(tx_all) else None
    when = last
    if as_of and last is not None:
        when = min(max(pd.Timestamp(as_of), first), last)
    tx = tx_all[tx_all["trade_date"] <= when] if when is not None else tx_all

    pf = compute(tx, dict(zip(prices["symbol"], prices["price"])))
    h = pf.holdings.merge(prices[["symbol", "change_pct", "traded_at", "source"]], on="symbol", how="left")
    h["day_change"] = h["change_pct"] / 100  # the CSE reports percent units
    h["value"] = h["market_value"].fillna(h["cost_basis"])  # unpriced holdings count at cost
    h["weight"] = h["value"] / h["value"].sum() if len(h) else h["value"]
    h["priced"] = h["market_value"].notna()
    h = h.drop(columns=["change_pct"]).sort_values("value", ascending=False)

    priced_cost = h.loc[h["priced"], "cost_basis"].sum()
    unreal = h["unrealized_pnl"].sum(min_count=1)
    turnover = tx["gross_amount"].sum() if len(tx) else 0.0

    capital = invested_over_time(tx)
    if len(capital) and when is not None and capital["trade_date"].iloc[-1] < when:
        capital = pd.concat([capital, pd.DataFrame([{"trade_date": when,
                                                     "net_invested": capital["net_invested"].iloc[-1]}])])
    vol = monthly(tx, "gross_amount", by="side")
    if len(vol):
        vol = vol.pivot_table(index="month", columns="side", values="gross_amount", aggfunc="sum", fill_value=0)
        vol = vol.reindex(columns=["BUY", "SELL"], fill_value=0).reset_index()
    else:
        vol = pd.DataFrame(columns=["month", "BUY", "SELL"])

    fetched = prices["fetched_at"].dropna().max() if len(prices) else None
    flags = [{"id": f"POS-{i:02d}", "kind": "position", "message": w} for i, w in enumerate(pf.warnings, 1)]
    flags += [{"id": f"DOC-{i:02d}", "kind": "document", "message": m} for i, m in enumerate(issues["message"], 1)]
    account = next(iter(docs["account"].dropna()), "")

    return {
        "meta": {
            "account": _mask(account), "currency": cfg.currency,
            "first_trade": first.strftime("%Y-%m-%d") if first is not None else None,
            "last_trade": last.strftime("%Y-%m-%d") if last is not None else None,
            "as_of": when.strftime("%Y-%m-%d") if when is not None else None,
            "prices_fetched": fetched if isinstance(fetched, str) else None,
            "documents": len(docs),
        },
        "readouts": {
            "net_invested": pf.net_invested, "cost_basis": float(h["cost_basis"].sum()),
            "market_value": _num(h["market_value"].sum(min_count=1)), "unrealized": _num(unreal),
            "unrealized_pct": _num(unreal / priced_cost) if pd.notna(unreal) and priced_cost else None,
            "realized": pf.realized_pnl, "fees": pf.fees, "turnover": float(turnover),
            "positions": len(h), "priced": int(h["priced"].sum()), "sells": int((tx["side"] == "SELL").sum()),
        },
        "holdings": _records(h),
        "capital": _records(capital.rename(columns={"trade_date": "date", "net_invested": "value"})),
        "realized_monthly": _records(monthly(pf.realized, "pnl")),
        "turnover_monthly": _records(vol.rename(columns={"BUY": "buy", "SELL": "sell"})),
        "flags": flags,
        "transactions": _records(tx.sort_values(["trade_date", "contract_no"], ascending=False)),
        "realized": _records(pf.realized.sort_values("trade_date", ascending=False)),
        "cash": _records(cash.sort_values("date", ascending=False)),
        "documents": _records(docs.drop(columns=["account"]).sort_values(["email_date", "id"], ascending=False)),
    }


def document_text(cfg: Config, conn: sqlite3.Connection, doc_id: int) -> str | None:
    row = conn.execute("SELECT text_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row or not row["text_path"]:
        return None
    return (cfg.data_dir / row["text_path"]).read_text()
