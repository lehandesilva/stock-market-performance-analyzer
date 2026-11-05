"""JSON payloads for the web dashboard, built from the database with the analysis module."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import pandas as pd

from ..actions import load_actions
from ..analysis import (NON_MARKET, benchmark, cash_flows, compute, invested_over_time, load_prices,
                        load_transactions, monthly, prices_on, value_history, xirr)
from ..adjust import actual_history
from ..config import Config
from ..db import FEE_COLUMNS
from ..fees import break_even, fee_rate, fee_rates, liquidation
from ..prices import ASPI, trading_day
from ..ticker import ticker_detail


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


@dataclass
class Data:
    """Everything the dashboard is built from, read once per request."""

    docs: pd.DataFrame
    issues: pd.DataFrame
    cash: pd.DataFrame
    actions: pd.DataFrame
    tx: pd.DataFrame
    prices: pd.DataFrame  # latest, with data/prices.csv overrides
    price_hist: pd.DataFrame  # symbol, date (Timestamp), price
    index_hist: pd.DataFrame  # date (Timestamp), value: the ASPI
    fetched: str | None  # when the latest prices were fetched (ISO time)
    changes: pd.DataFrame  # stored closes the CSE later reported differently, per series and day noticed
    indices: pd.DataFrame  # name, date, value: every index stored (ASPI, SPSL20, sector:…)

    @property
    def fetched_day(self) -> pd.Timestamp | None:
        return pd.Timestamp(trading_day(self.fetched)) if self.fetched else None

    def index_named(self, name: str) -> pd.Series:
        from ..performance import level_series

        return level_series(self.indices[self.indices["name"] == name])


def load_data(cfg: Config, conn: sqlite3.Connection) -> Data:
    docs = pd.read_sql_query(
        """SELECT id, email_date, attachment_name, doc_type, layout, account, parse_count,
                  COALESCE(parse_error, extract_error) AS error, text_path IS NOT NULL AS has_text
           FROM documents ORDER BY email_date, id""",
        conn,
    )
    issues = pd.read_sql_query("SELECT subject, message FROM issues ORDER BY subject", conn)
    cash = pd.read_sql_query(
        "SELECT date, kind, ref, amount, description, sources FROM cash_movements ORDER BY date", conn)
    actions = load_actions(cfg.actions_csv, cfg.dividend_withholding)
    tx = load_transactions(conn, cfg.manual_csv, actions)
    prices = load_prices(conn, cfg.prices_csv)
    ph = pd.read_sql_query("SELECT symbol, date, price, volume FROM price_history", conn)
    ph["date"] = pd.to_datetime(ph["date"])
    indices = pd.read_sql_query("SELECT name, date, value FROM index_history", conn)
    indices["date"] = pd.to_datetime(indices["date"])
    ih = indices.loc[indices["name"] == ASPI, ["date", "value"]].reset_index(drop=True)
    fetched = prices["fetched_at"].dropna().max() if len(prices) else None
    changes = pd.read_sql_query(
        """SELECT series, noticed_on, COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last,
                  MAX(ABS(new / old - 1)) AS most
           FROM history_changes WHERE noticed_on >= date('now', '-60 days')
           GROUP BY series, noticed_on ORDER BY noticed_on, series""", conn)
    return Data(docs, issues, cash, actions, tx, prices, ph, ih, fetched if isinstance(fetched, str) else None,
                changes, indices)


def _dates(d: Data, as_of: str | None):
    """(first, last, newest, when, latest).

    `last` is the last trade or corporate action; `newest` the newest day anything has a value for, which
    is as far as "As of" goes. `latest` is False for a past date: it is valued at that day's closes.
    """
    if d.tx.empty:
        return None, None, None, None, True
    dates = pd.concat([d.tx["trade_date"], d.actions["date"]])
    first, last = dates.min(), dates.max()  # a dividend can come after the last trade
    newest = max([last, *([d.price_hist["date"].max()] if len(d.price_hist) else []),
                  *([d.fetched_day] if d.fetched_day is not None else [])])
    when = newest if not as_of else min(max(pd.Timestamp(as_of), first), newest)
    return first, last, newest, when, when >= (d.fetched_day if d.fetched_day is not None else newest)


def _valuation_prices(d: Data, when, latest: bool) -> pd.DataFrame:
    """symbol, price, change_pct, traded_at, source, price_date for valuing holdings on `when`.

    Looking back, each stock's last close on or before that day is used (within STALE_DAYS); data/prices.csv
    overrides are left out, because they're today's prices.
    """
    if latest:
        p = d.prices.copy()
        p["price_date"] = [pd.Timestamp(trading_day(t)) if isinstance(t, str) else pd.NaT for t in p["traded_at"]]
        return p
    p = prices_on(actual_history(d.price_hist, d.actions), when)
    return p.assign(change_pct=float("nan"), traded_at=None, source="CSE close")


def build_payload(cfg: Config, conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    d = load_data(cfg, conn)
    docs, issues, cash, actions_all, tx_all = d.docs, d.issues, d.cash, d.actions, d.tx
    first, last, newest, when, latest = _dates(d, as_of)
    tx = tx_all[tx_all["trade_date"] <= when] if when is not None else tx_all
    actions = actions_all[actions_all["date"] <= when] if when is not None else actions_all
    # The day holdings are valued: the as-of date, or for the latest view the day prices were fetched.
    valued = when if not latest or d.fetched_day is None or when is None else max(when, d.fetched_day)
    fees = fee_rate(tx_all, valued if valued is not None else pd.Timestamp.today(), cfg.fee_rate)
    f = fees["rate"]

    prices = _valuation_prices(d, when, latest)
    pf = compute(tx, dict(zip(prices["symbol"], prices["price"])), actions)
    h = pf.holdings.merge(prices[["symbol", "change_pct", "traded_at", "source", "price_date"]], on="symbol",
                          how="left")
    h["day_change"] = h["change_pct"] / 100  # the CSE reports percent units
    h["value"] = h["market_value"].fillna(h["cost_basis"])  # unpriced holdings count at cost
    h["weight"] = h["value"] / h["value"].sum() if len(h) else h["value"]
    h["priced"] = h["market_value"].notna()
    h["liquidation_value"] = liquidation(h["market_value"], f)
    h["break_even"] = break_even(h["avg_cost"], f)
    h["price_at"] = [t if isinstance(t, str) else (pd.Timestamp(x).strftime("%Y-%m-%d") if pd.notna(x) else None)
                     for t, x in zip(h["traded_at"], h["price_date"])]
    h = h.drop(columns=["change_pct", "price_date"]).sort_values("value", ascending=False)

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

    returns = _returns(pf, h, when, valued, unreal, f)
    history, bench = _history(d, pf, actions, when, latest, cfg.fee_rate)
    unpriced = [f"{sym}: no price from the CSE. If it left the exchange (delisted or bought out), add a DELIST row "
                "with the buyout price to corporate_actions.csv; if it's only suspended, put a price in prices.csv."
                for sym in h.loc[~h["priced"], "symbol"]] if latest else []
    flags = [{"id": f"POS-{i:02d}", "kind": "position", "message": w}
             for i, w in enumerate(pf.warnings + unpriced, 1)]
    flags += [{"id": f"DOC-{i:02d}", "kind": "document", "message": m} for i, m in enumerate(issues["message"], 1)]
    flags += [{"id": f"PX-{i:02d}", "kind": "price", "message":
               f"{c.series}: on {c.noticed_on} the CSE reported {c.n} stored close{'s' if c.n > 1 else ''} "
               f"({c.first}{f' … {c.last}' if c.n > 1 else ''}) differently, by up to {c.most:.0%}. A correction, or "
               "its history adjusted for a split? The new closes are used."}
              for i, c in enumerate(d.changes.itertuples(index=False), 1)]
    account = next(iter(docs["account"].dropna()), "")
    day = lambda t: t.strftime("%Y-%m-%d") if t is not None else None  # noqa: E731
    liquid = _num(h["liquidation_value"].sum(min_count=1))

    return {
        "meta": {
            "account": _mask(account), "currency": cfg.currency,
            "first_trade": day(first),
            "last_trade": tx_all["trade_date"].max().strftime("%Y-%m-%d") if len(tx_all) else None,
            "last_date": day(last), "max_date": day(newest), "as_of": day(when), "valued": day(valued),
            "past": not latest, "prices_fetched": d.fetched,
            "fee_rate": {**fees, "from": day(fees["from"]), "to": day(fees["to"])},
            "documents": len(docs),
        },
        "readouts": {
            "net_invested": pf.net_invested, "cost_basis": float(h["cost_basis"].sum()),
            "market_value": _num(h["market_value"].sum(min_count=1)), "unrealized": _num(unreal),
            "unrealized_pct": _num(unreal / priced_cost) if pd.notna(unreal) and priced_cost else None,
            "realized": pf.realized_pnl, "fees": pf.fees, "turnover": float(turnover),
            "positions": len(h), "priced": int(h["priced"].sum()), "sells": int((tx["side"] == "SELL").sum()),
            "dividends": pf.dividend_income, "dividend_count": len(pf.dividends),
            "liquidation_value": liquid, **returns,
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


def _returns(pf, h: pd.DataFrame, when, valued, unreal, fee: float) -> dict:
    """Total gain and money-weighted return (XIRR), valuing holdings on `valued`, as they stand and as if
    sold then (after selling fees).

    Unpriced holdings count at cost. Under a year, the annual rate would exaggerate, so the plain
    return over the period is given too.
    """
    gain = pf.realized_pnl + pf.dividend_income + (0.0 if pd.isna(unreal) else float(unreal))
    selling = float(h["market_value"].sum() * fee) if len(h) else 0.0
    out = {"total_gain": gain, "gain_if_sold": gain - selling, "selling_fees": selling, "xirr": None,
           "xirr_if_sold": None, "period_return": None, "period_return_if_sold": None, "return_since": None,
           "return_days": None}
    if when is None or pf.transactions.empty:
        return out
    flows = cash_flows(pf.transactions, pf.dividends)
    value = float(h["value"].sum()) if len(h) else 0.0
    rate, rate_net = xirr(flows + [(valued, value)]), xirr(flows + [(valued, value - selling)])
    start = min(dt for dt, _ in flows)
    days = (valued - start).days
    period = lambda r: (1 + r) ** (days / 365) - 1 if r is not None else None  # noqa: E731
    out.update(return_since=start.strftime("%Y-%m-%d"), return_days=days, xirr=rate, xirr_if_sold=rate_net,
               period_return=period(rate), period_return_if_sold=period(rate_net))
    return out


def _history(d: Data, pf, actions: pd.DataFrame, end, latest: bool, fallback_fee: float) -> tuple[list[dict], dict | None]:
    """Daily portfolio value from price history, the same money in the ASPI, and the total gain had everything
    been sold that day (after that day's selling fees), next to the part already locked in.

    Looking at the latest date, the chart runs on to the newest price; looking back (as of a past
    date), it stops there.
    """
    if end is None:
        return [], None
    ph, ih = actual_history(d.price_hist, actions), d.index_hist
    if latest and len(ph):
        end = max(end, ph["date"].max())
    hist = value_history(pf.transactions, actions, ph, ih, end)
    hist["benchmark"], bench = benchmark(hist, cash_flows(pf.transactions, pf.dividends), ih)
    hist["locked"] = hist["realized"] + hist["dividends"]
    rates = fee_rates(d.tx, hist["date"], fallback_fee).to_numpy() if len(hist) else []
    hist["gain_if_sold"] = hist["value"] * (1 - rates) - hist["cost"] + hist["locked"]
    hist = hist[hist["value"].notna()][["date", "value", "benchmark", "gain_if_sold", "locked"]]
    if bench:
        bench = {**bench, "start": bench["start"].strftime("%Y-%m-%d"), "end": bench["end"].strftime("%Y-%m-%d")}
    return _records(hist), bench


def _plain(v):
    """Anything in a payload -> JSON-safe: DataFrames to records, dates to 'YYYY-MM-DD', NaN to null."""
    if isinstance(v, pd.DataFrame):
        return _records(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if v is None or isinstance(v, (bool, str)):
        return v
    if pd.api.types.is_number(v):
        return None if pd.isna(v) else (int(v) if isinstance(v, (int,)) and not isinstance(v, bool) else float(v))
    if v is pd.NaT:
        return None
    return v


def ticker_payload(cfg: Config, conn: sqlite3.Connection, symbol: str, sold_on: str | None = None) -> dict | None:
    """The per-stock page, valued like the dashboard's latest view. None for a symbol never traded."""
    d = load_data(cfg, conn)
    if symbol not in set(d.tx["symbol"]) | set(d.actions["symbol"]):
        return None
    _, _, _, when, _ = _dates(d, None)
    today = max(when, d.fetched_day) if d.fetched_day is not None else when
    prices = d.prices.set_index("symbol")
    price = float(prices.at[symbol, "price"]) if symbol in prices.index else None
    price_at = prices.at[symbol, "traded_at"] if symbol in prices.index else None
    pf = compute(d.tx, dict(zip(d.prices["symbol"], d.prices["price"])), d.actions)
    total = float(pf.holdings["market_value"].fillna(pf.holdings["cost_basis"]).sum())
    info = pd.read_sql_query("SELECT * FROM security_info WHERE symbol = ? ORDER BY fetched_on DESC LIMIT 1", conn,
                             params=(symbol,))
    fees = pd.read_sql_query(f"SELECT contract_no, {FEE_COLUMNS} FROM trades WHERE symbol = ?", conn, params=(symbol,))
    out = ticker_detail(d.tx, d.actions, price, price_at if isinstance(price_at, str) else None,
                        actual_history(d.price_hist, d.actions).merge(
                            d.price_hist[["symbol", "date", "volume"]], on=["symbol", "date"], how="left"),
                        d.index_hist, symbol, today, cfg.fee_rate, total,
                        info.iloc[0].to_dict() if len(info) else None, fees,
                        pd.Timestamp(sold_on) if sold_on else None)
    out["currency"] = cfg.currency
    out["prices_fetched"] = d.fetched
    return _plain(out)


def document_text(cfg: Config, conn: sqlite3.Connection, doc_id: int) -> str | None:
    row = conn.execute("SELECT text_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row or not row["text_path"]:
        return None
    return (cfg.data_dir / row["text_path"]).read_text()
