"""Portfolio maths on parsed transactions (average-cost method), corporate actions and XIRR."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .actions import empty_actions

TX_COLUMNS = [
    "trade_date", "side", "symbol", "quantity", "price", "gross_amount", "fees", "net_amount",
    "contract_no", "sources", "company",
]
RIGHTS_SOURCE = "rights issue"  # subscriptions paid to the company: purchases, but not market turnover
ACTION_LOG_COLUMNS = ["date", "symbol", "action", "detail", "shares", "amount", "note"]


@dataclass
class Portfolio:
    transactions: pd.DataFrame
    holdings: pd.DataFrame
    realized: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    actions: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=ACTION_LOG_COLUMNS))

    @property
    def dividends(self) -> pd.DataFrame:
        return self.actions[self.actions["action"] == "DIVIDEND"]

    @property
    def dividend_income(self) -> float:
        return float(self.dividends["amount"].sum())

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


def load_transactions(conn: sqlite3.Connection, manual_csv: Path | None = None,
                      actions: pd.DataFrame | None = None) -> pd.DataFrame:
    """Reconciled trades (one per contract number), manual additions, and rights subscriptions."""
    df = pd.read_sql_query(
        """SELECT trade_date, side, symbol, quantity, price, gross AS gross_amount, fees_total AS fees,
                  net AS net_amount, contract_no, sources, company
           FROM trades""",
        conn,
    )
    if manual_csv and manual_csv.exists():
        m = load_manual(manual_csv)
        df = m if df.empty else pd.concat([df, m], ignore_index=True)
    if actions is not None and (rights := rights_as_trades(actions)) is not None:
        df = rights if df.empty else pd.concat([df, rights], ignore_index=True)
    if df.empty:
        return pd.DataFrame(columns=TX_COLUMNS)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # Buys before sells on the same day (CSE has no short selling), then execution order.
    return df.sort_values(["trade_date", "side", "contract_no"], kind="stable").reset_index(drop=True)[TX_COLUMNS]


def load_manual(path: Path) -> pd.DataFrame:
    """Extra trades not in any document: trade_date,side,symbol,quantity,price[,fees]."""
    m = pd.read_csv(path)
    m["fees"] = m["fees"].fillna(0.0) if "fees" in m else 0.0
    m["side"] = m["side"].str.upper()
    m["symbol"] = m["symbol"].str.upper().str.strip()
    m["gross_amount"] = m["quantity"] * m["price"]
    m["net_amount"] = m["gross_amount"] + m["fees"].where(m["side"] == "BUY", -m["fees"])
    m["contract_no"] = ""
    m["sources"] = "manual"
    m["company"] = None
    return m[TX_COLUMNS]


def rights_as_trades(actions: pd.DataFrame) -> pd.DataFrame | None:
    """RIGHTS rows -> BUY trades at the subscription price (no brokerage: you pay the company)."""
    r = actions[actions["action"] == "RIGHTS"]
    if r.empty:
        return None
    gross = r["quantity"] * r["price"]
    return pd.DataFrame({
        "trade_date": r["date"].dt.strftime("%Y-%m-%d"), "side": "BUY", "symbol": r["symbol"],
        "quantity": r["quantity"], "price": r["price"], "gross_amount": gross, "fees": 0.0, "net_amount": gross,
        "contract_no": "", "sources": RIGHTS_SOURCE, "company": None,
    })[TX_COLUMNS]


def load_prices(conn: sqlite3.Connection, override_csv: Path | None = None) -> pd.DataFrame:
    """Latest prices from `smpa prices`, with data/prices.csv (symbol,price) taking precedence."""
    df = pd.read_sql_query("SELECT symbol, price, change_pct, traded_at, fetched_at FROM prices", conn)
    df["source"] = "CSE"
    if override_csv and override_csv.exists():
        o = pd.read_csv(override_csv)[["symbol", "price"]]
        o["symbol"] = o["symbol"].astype(str).str.upper().str.strip()
        o["source"] = override_csv.name
        df = pd.concat([df[~df["symbol"].isin(o["symbol"])], o], ignore_index=True)
    return df


def compute(tx: pd.DataFrame, prices: dict[str, float] | None = None,
            actions: pd.DataFrame | None = None) -> Portfolio:
    """Positions, realized P&L and corporate-action effects.

    Bonus issues, splits and dividends apply to the shares held at the start of their date (the ex-date),
    so trades on that day come after them. They change the share count, never the cost, so the average
    cost per share falls. Rights subscriptions arrive in `tx` as purchases (see `load_transactions`).
    """
    prices = prices or {}
    actions = empty_actions() if actions is None else actions
    pos: dict[str, tuple[float, float]] = {}  # symbol -> (quantity, cost basis)
    realized, warnings, log = [], [], []
    events = [(r.trade_date, 1, i, r) for i, r in enumerate(tx.itertuples(index=False))]
    events += [(a.date, 0, i, a) for i, a in enumerate(actions.itertuples(index=False)) if a.action != "RIGHTS"]
    events.sort(key=lambda e: e[:3])
    for _, kind, _, r in events:
        qty, cost = pos.get(r.symbol, (0.0, 0.0))
        if kind == 0:
            qty = _apply_action(r, qty, log, warnings)
        elif r.side == "BUY":
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
    for a in actions[actions["action"] == "RIGHTS"].itertuples(index=False):
        log.append({"date": a.date, "symbol": a.symbol, "action": a.action,
                    "detail": f"subscribed at {a.price:,.2f}", "shares": a.quantity,
                    "amount": -a.quantity * a.price, "note": a.note})
    log_df = pd.DataFrame(log, columns=ACTION_LOG_COLUMNS).sort_values("date", kind="stable").reset_index(drop=True)
    log_df[["shares", "amount"]] = log_df[["shares", "amount"]].astype(float)
    div_by_symbol = log_df[log_df["action"] == "DIVIDEND"].groupby("symbol")["amount"].sum()

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
            "dividends": float(div_by_symbol.get(sym, 0.0)),
        })
    num_cols = ["quantity", "avg_cost", "cost_basis", "price", "market_value", "unrealized_pnl", "unrealized_pct",
                "dividends"]
    holdings = pd.DataFrame(rows, columns=["symbol", *num_cols])
    # Unpriced holdings are None; keep the columns numeric (NaN) even when nothing is priced.
    holdings[num_cols] = holdings[num_cols].astype(float)
    realized_df = pd.DataFrame(realized, columns=["trade_date", "symbol", "quantity", "proceeds", "cost", "pnl"])
    return Portfolio(tx, holdings, realized_df, warnings, log_df)


def _apply_action(a, qty: float, log: list[dict], warnings: list[str]) -> float:
    """Apply one BONUS / SPLIT / DIVIDEND to a holding of `qty` shares; returns the new share count."""
    day = f"{a.date:%Y-%m-%d}"
    entry = {"date": a.date, "symbol": a.symbol, "action": a.action, "shares": 0.0, "amount": 0.0, "note": a.note}
    if a.action == "DIVIDEND":
        if pd.notna(a.amount):
            entry.update(detail="cash received", amount=a.amount)
        else:
            entry.update(detail=f"{a.per_share:,.2f} a share on {qty:,.0f}", amount=a.per_share * qty)
            if qty <= 0:
                warnings.append(f"{day}: dividend per share on {a.symbol}, but none held then - enter the amount.")
    elif a.action == "BONUS":
        ratio = f"{a.ratio_new:g} for every {a.ratio_old:g}" if pd.notna(a.ratio_new) else ""
        add = a.quantity if pd.notna(a.quantity) else math.floor(qty * a.ratio_new / a.ratio_old + 1e-9)
        entry.update(detail=ratio or "shares received", shares=add)
        if pd.isna(a.quantity) and qty <= 0:
            warnings.append(f"{day}: bonus issue on {a.symbol}, but none held then - enter the quantity received.")
        qty += add
    else:  # SPLIT
        new = math.floor(qty * a.ratio_new / a.ratio_old + 1e-9)
        word = "split" if a.ratio_new >= a.ratio_old else "consolidation"
        entry.update(detail=f"{word} {a.ratio_new:g}:{a.ratio_old:g}", shares=new - qty)
        if qty <= 0:
            warnings.append(f"{day}: split of {a.symbol}, but none held then.")
        qty = new
    log.append(entry)
    return qty


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


def cash_flows(tx: pd.DataFrame, dividends: pd.DataFrame | None = None) -> list[tuple[pd.Timestamp, float]]:
    """Your money in and out of shares: buys (incl. rights) are negative, sells and dividends positive."""
    flows = [(r.trade_date, -r.net_amount if r.side == "BUY" else r.net_amount) for r in tx.itertuples(index=False)]
    if dividends is not None:
        flows += [(r.date, r.amount) for r in dividends.itertuples(index=False)]
    return flows


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """Annual rate r with sum(amount / (1 + r) ** (days / 365)) = 0, as Excel's XIRR. None if undefined."""
    flows = [(d, float(a)) for d, a in flows if a]
    if not (any(a > 0 for _, a in flows) and any(a < 0 for _, a in flows)):
        return None
    t0 = min(d for d, _ in flows)
    ts = [((d - t0).days / 365.0, a) for d, a in flows]

    def npv(r: float) -> float:
        return sum(a * math.exp(-t * math.log1p(r)) for t, a in ts)

    lo, hi = -0.9999, 1.0  # -99.99% a year .. +100%, widened upward as needed
    f_lo = npv(lo)
    while (npv(hi) > 0) == (f_lo > 0):
        hi *= 4
        if hi > 1e6:
            return None
    for _ in range(200):  # bisection: slow but can't diverge
        mid = (lo + hi) / 2
        if (npv(mid) > 0) == (f_lo > 0):
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2
