"""JSON payloads for the web dashboard, built from the database with the analysis module."""

from __future__ import annotations

import json
import sqlite3

import pandas as pd

from ..actions import load_actions
from ..analysis import (NON_MARKET, benchmark, cash_flows, compute, invested_over_time, load_prices,
                        load_transactions, monthly, value_history, xirr)
from ..prices import ASPI
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
    actions_all = load_actions(cfg.actions_csv)
    tx_all = load_transactions(conn, cfg.manual_csv, actions_all)
    prices = load_prices(conn, cfg.prices_csv)

    dates = pd.concat([tx_all["trade_date"], actions_all["date"]])
    first = dates.min() if len(tx_all) else None
    last = dates.max() if len(tx_all) else None  # a dividend can come after the last trade
    when = last
    if as_of and last is not None:
        when = min(max(pd.Timestamp(as_of), first), last)
    tx = tx_all[tx_all["trade_date"] <= when] if when is not None else tx_all
    actions = actions_all[actions_all["date"] <= when] if when is not None else actions_all

    pf = compute(tx, dict(zip(prices["symbol"], prices["price"])), actions)
    h = pf.holdings.merge(prices[["symbol", "change_pct", "traded_at", "source"]], on="symbol", how="left")
    h["day_change"] = h["change_pct"] / 100  # the CSE reports percent units
    h["value"] = h["market_value"].fillna(h["cost_basis"])  # unpriced holdings count at cost
    h["weight"] = h["value"] / h["value"].sum() if len(h) else h["value"]
    h["priced"] = h["market_value"].notna()
    h = h.drop(columns=["change_pct"]).sort_values("value", ascending=False)

    priced_cost = h.loc[h["priced"], "cost_basis"].sum()
    unreal = h["unrealized_pnl"].sum(min_count=1)
    traded = tx[~tx["sources"].isin(NON_MARKET)]  # rights subscriptions and buyouts aren't market turnover
    turnover = traded["gross_amount"].sum() if len(traded) else 0.0

    capital = invested_over_time(tx)
    if len(capital) and when is not None and capital["trade_date"].iloc[-1] < when:
        capital = pd.concat([capital, pd.DataFrame([{"trade_date": when,
                                                     "net_invested": capital["net_invested"].iloc[-1]}])])
    vol = monthly(traded, "gross_amount", by="side")
    if len(vol):
        vol = vol.pivot_table(index="month", columns="side", values="gross_amount", aggfunc="sum", fill_value=0)
        vol = vol.reindex(columns=["BUY", "SELL"], fill_value=0).reset_index()
    else:
        vol = pd.DataFrame(columns=["month", "BUY", "SELL"])

    fetched = prices["fetched_at"].dropna().max() if len(prices) else None
    fetched = fetched if isinstance(fetched, str) else None
    valued = max(when, pd.Timestamp(fetched[:10])) if when is not None and fetched else when
    returns = _returns(pf, h, when, valued, unreal)
    history, bench = _history(conn, pf, actions, when, latest=not (as_of and when is not None and when < last))
    unpriced = [f"{sym}: no price from the CSE. If it left the exchange (delisted or bought out), add a DELIST row "
                "with the buyout price to corporate_actions.csv; if it's only suspended, put a price in prices.csv."
                for sym in h.loc[~h["priced"], "symbol"]]
    flags = [{"id": f"POS-{i:02d}", "kind": "position", "message": w}
             for i, w in enumerate(pf.warnings + unpriced, 1)]
    flags += [{"id": f"DOC-{i:02d}", "kind": "document", "message": m} for i, m in enumerate(issues["message"], 1)]
    account = next(iter(docs["account"].dropna()), "")

    return {
        "meta": {
            "account": _mask(account), "currency": cfg.currency,
            "first_trade": first.strftime("%Y-%m-%d") if first is not None else None,
            "last_trade": tx_all["trade_date"].max().strftime("%Y-%m-%d") if len(tx_all) else None,
            "last_date": last.strftime("%Y-%m-%d") if last is not None else None,
            "as_of": when.strftime("%Y-%m-%d") if when is not None else None,
            "prices_fetched": fetched,
            "documents": len(docs),
        },
        "readouts": {
            "net_invested": pf.net_invested, "cost_basis": float(h["cost_basis"].sum()),
            "market_value": _num(h["market_value"].sum(min_count=1)), "unrealized": _num(unreal),
            "unrealized_pct": _num(unreal / priced_cost) if pd.notna(unreal) and priced_cost else None,
            "realized": pf.realized_pnl, "fees": pf.fees, "turnover": float(turnover),
            "positions": len(h), "priced": int(h["priced"].sum()), "sells": int((tx["side"] == "SELL").sum()),
            "dividends": pf.dividend_income, "dividend_count": len(pf.dividends), **returns,
        },
        "holdings": _records(h),
        "capital": _records(capital.rename(columns={"trade_date": "date", "net_invested": "value"})),
        "history": history, "benchmark": bench,
        "realized_monthly": _records(monthly(pf.realized, "pnl")),
        "turnover_monthly": _records(vol.rename(columns={"BUY": "buy", "SELL": "sell"})),
        "flags": flags,
        "transactions": _records(tx.sort_values(["trade_date", "contract_no"], ascending=False)),
        "realized": _records(pf.realized.sort_values("trade_date", ascending=False)),
        "cash": _records(cash.sort_values("date", ascending=False)),
        "actions": _records(pf.actions.sort_values("date", ascending=False)),
        "documents": _records(docs.drop(columns=["account"]).sort_values(["email_date", "id"], ascending=False)),
    }


def _returns(pf, h: pd.DataFrame, when, valued, unreal) -> dict:
    """Total gain and money-weighted return (XIRR), valuing holdings on the date prices were fetched.

    Unpriced holdings count at cost. Under a year, the annual rate would exaggerate, so the plain
    return over the period is given too.
    """
    gain = pf.realized_pnl + pf.dividend_income + (0.0 if pd.isna(unreal) else float(unreal))
    out = {"total_gain": gain, "xirr": None, "period_return": None, "return_since": None, "return_days": None}
    if when is None or pf.transactions.empty:
        return out
    flows = cash_flows(pf.transactions, pf.dividends)
    flows.append((valued, float(h["value"].sum()) if len(h) else 0.0))
    rate = xirr(flows)
    start = min(d for d, _ in flows)
    days = (valued - start).days
    out.update(return_since=start.strftime("%Y-%m-%d"), return_days=days, xirr=rate,
               period_return=(1 + rate) ** (days / 365) - 1 if rate is not None else None)
    return out


def _history(conn: sqlite3.Connection, pf, actions: pd.DataFrame, end, latest: bool) -> tuple[list[dict], dict | None]:
    """Daily portfolio value from price history, and the same money in the ASPI.

    Looking at the latest date, the chart runs on to the newest price; looking back (as of a past
    date), it stops there.
    """
    if end is None:
        return [], None
    ph = pd.read_sql_query("SELECT symbol, date, price FROM price_history", conn)
    ih = pd.read_sql_query("SELECT date, value FROM index_history WHERE name = ?", conn, params=(ASPI,))
    if latest and len(ph):
        end = max(end, pd.Timestamp(ph["date"].max()))
    hist = value_history(pf.transactions, actions, ph, ih, end)
    hist["benchmark"], bench = benchmark(hist, cash_flows(pf.transactions, pf.dividends), ih)
    hist = hist[hist["value"].notna()][["date", "value", "benchmark"]]
    if bench:
        bench = {**bench, "start": bench["start"].strftime("%Y-%m-%d"), "end": bench["end"].strftime("%Y-%m-%d")}
    return _records(hist), bench


def document_text(cfg: Config, conn: sqlite3.Connection, doc_id: int) -> str | None:
    row = conn.execute("SELECT text_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row or not row["text_path"]:
        return None
    return (cfg.data_dir / row["text_path"]).read_text()
