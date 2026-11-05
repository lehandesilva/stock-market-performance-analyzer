"""Portfolio maths on parsed transactions (average-cost method), corporate actions and XIRR."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .actions import empty_actions
from .errors import PipelineStop

TX_COLUMNS = [
    "trade_date", "side", "symbol", "quantity", "price", "gross_amount", "fees", "net_amount",
    "contract_no", "sources", "company",
]
RIGHTS_SOURCE = "rights issue"  # subscriptions paid to the company: purchases, but not market turnover
DELIST_SOURCE = "delisting"  # shares bought out when a company left the exchange: a sale, but not turnover
NON_MARKET = {RIGHTS_SOURCE, DELIST_SOURCE}
ACTION_LOG_COLUMNS = ["date", "symbol", "action", "detail", "shares", "amount", "note", "held"]  # held: going in


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
    """Reconciled trades (one per contract number), manual additions, rights subscriptions and delistings."""
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
    df = _sorted(df)
    if actions is not None:
        for a in actions[actions["action"] == "DELIST"].itertuples(index=False):
            df = _sorted(pd.concat([df, _delisting(df, actions, a)], ignore_index=True))
    return df


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    # Buys before sells on the same day (CSE has no short selling), then execution order.
    return df.sort_values(["trade_date", "side", "contract_no"], kind="stable").reset_index(drop=True)[TX_COLUMNS]


def _delisting(tx: pd.DataFrame, actions: pd.DataFrame, a) -> pd.DataFrame:
    """A DELIST row -> a sale of every share held going into that day, at the buyout price, without fees."""
    book = _Ledger()
    for d, kind, r in _events(tx, actions):
        if d > a.date or (d == a.date and kind == 1):
            break
        book.apply(kind, r)
    qty = book.pos.get(a.symbol, (0.0, 0.0))[0]
    if qty <= 0:
        raise PipelineStop(f"corporate_actions.csv: DELIST of {a.symbol} on {a.date:%Y-%m-%d}, "
                           "but no shares of it are held then")
    gross = qty * a.price
    return pd.DataFrame([{"trade_date": a.date, "side": "SELL", "symbol": a.symbol, "quantity": qty,
                          "price": a.price, "gross_amount": gross, "fees": 0.0, "net_amount": gross,
                          "contract_no": "", "sources": DELIST_SOURCE, "company": None}])


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
    book = _Ledger()
    for _, kind, r in _events(tx, actions):
        book.apply(kind, r)
    pos, realized, warnings, log = book.pos, book.realized, book.warnings, book.log
    for sym, (d, px) in book.last_price.items():  # and since the last trade, against today's price
        if pos.get(sym, (0, 0))[0] > 0 and prices.get(sym):
            warnings += _split_hint(sym, d, px, None, prices[sym])
    for a in actions[actions["action"] == "RIGHTS"].itertuples(index=False):
        log.append({"date": a.date, "symbol": a.symbol, "action": a.action,
                    "detail": f"subscribed at {a.price:,.2f}", "shares": a.quantity,
                    "amount": -a.quantity * a.price, "note": a.note, "held": float("nan")})
    notes = {(a.symbol, a.date): a.note for a in actions[actions["action"] == "DELIST"].itertuples(index=False)}
    for r in tx[tx["sources"] == DELIST_SOURCE].itertuples(index=False) if "sources" in tx else []:
        log.append({"date": r.trade_date, "symbol": r.symbol, "action": "DELIST",
                    "detail": f"bought out at {r.price:,.2f}" if r.price else "written off", "shares": -r.quantity,
                    "amount": r.net_amount, "note": notes.get((r.symbol, r.trade_date), ""), "held": r.quantity})
    log_df = pd.DataFrame(log, columns=ACTION_LOG_COLUMNS).sort_values("date", kind="stable").reset_index(drop=True)
    log_df[["shares", "amount", "held"]] = log_df[["shares", "amount", "held"]].astype(float)
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


def _events(tx: pd.DataFrame, actions: pd.DataFrame | None) -> list[tuple]:
    """(date, kind, row) in the order they apply: by date, corporate actions (kind 0) before trades (1)."""
    events = [(r.trade_date, 1, i, r) for i, r in enumerate(tx.itertuples(index=False))]
    if actions is not None:
        events += [(a.date, 0, i, a) for i, a in enumerate(actions.itertuples(index=False)) if a.action not in ("RIGHTS", "DELIST")]
    events.sort(key=lambda e: e[:3])
    return [(d, kind, r) for d, kind, _, r in events]


class _Ledger:
    """Positions built up one event at a time (shared by `compute` and `value_history`)."""

    def __init__(self):
        self.pos: dict[str, tuple[float, float]] = {}  # symbol -> (quantity, cost basis)
        self.realized: list[dict] = []
        self.warnings: list[str] = []
        self.log: list[dict] = []
        self.invested = 0.0  # buys - sell proceeds so far
        self.last_price: dict[str, tuple[pd.Timestamp, float]] = {}  # symbol -> last market trade

    def apply(self, kind: int, r) -> None:
        qty, cost = self.pos.get(r.symbol, (0.0, 0.0))
        if kind == 0:
            qty = _apply_action(r, qty, self.log, self.warnings)
            if r.action in ("SPLIT", "BONUS"):
                self.last_price.pop(r.symbol, None)  # prices before and after aren't comparable
            self.pos[r.symbol] = (qty, cost)
            return
        if getattr(r, "sources", None) not in NON_MARKET:  # those prices are set by a company, not the market
            if r.symbol in self.last_price:
                self.warnings += _split_hint(r.symbol, *self.last_price[r.symbol], r.trade_date, r.price)
            self.last_price[r.symbol] = (r.trade_date, r.price)
        if r.side == "BUY":
            qty, cost = qty + r.quantity, cost + r.net_amount
            self.invested += r.net_amount
        else:
            self.invested -= r.net_amount
            known = min(r.quantity, qty)
            if r.quantity > qty + 1e-9:
                self.warnings.append(
                    f"{r.trade_date:%Y-%m-%d}: sold {r.quantity:g} {r.symbol} but only {qty:g} "
                    "bought in the data - missing earlier statements? Unknown lots counted at zero gain."
                )
            avg = cost / qty if qty else 0.0
            cost_known = avg * known
            cost_unknown = r.net_amount * (r.quantity - known) / r.quantity
            cost_sold = cost_known + cost_unknown
            self.realized.append({
                "trade_date": r.trade_date, "symbol": r.symbol, "quantity": r.quantity,
                "proceeds": r.net_amount, "cost": cost_sold, "pnl": r.net_amount - cost_sold,
            })
            qty, cost = qty - known, cost - cost_known
            if qty <= 1e-9:
                qty, cost = 0.0, 0.0
        self.pos[r.symbol] = (qty, cost)

    def held(self) -> dict[str, tuple[float, float]]:
        return {s: qc for s, qc in self.pos.items() if qc[0] > 0}


def _split_hint(symbol: str, d0: pd.Timestamp, p0: float, d1: pd.Timestamp | None, p1: float) -> list[str]:
    """A warning when a price fell to about 1/2, 1/3 ... 1/10 of an earlier one: a split no one recorded?

    Splits and bonus issues never appear on broker statements, and a missing one makes the average cost
    far too high (a 1-into-5 split looks like an 80% loss). A real crash can also trip this; the
    message asks to check, nothing is changed.
    """
    ratio = p0 / p1 if p1 else 0
    k = round(ratio)
    if not (2 <= k <= 10 and abs(ratio / k - 1) <= 0.12):
        return []
    when = f"on {d1:%Y-%m-%d}" if d1 is not None else "now"
    return [f"{symbol}: price went from {p0:,.2f} on {d0:%Y-%m-%d} to {p1:,.2f} {when}, about 1/{k}. "
            f"A share split or bonus issue? Add it to corporate_actions.csv (e.g. SPLIT with ratio {k}:1)."]


def held_between(tx: pd.DataFrame, actions: pd.DataFrame | None, since) -> list[str]:
    """Symbols held at any time from `since` (a date) on."""
    since = pd.Timestamp(since)
    book, out = _Ledger(), set()
    for d, kind, r in _events(tx, actions):
        if d >= since and not out.issuperset(book.held()):
            out |= set(book.held())  # what was held going into `since`
        book.apply(kind, r)
        if d >= since:
            out |= set(book.held())
    return sorted(out | set(book.held()))


def dividends_per_share(tx: pd.DataFrame, actions: pd.DataFrame | None) -> pd.DataFrame:
    """symbol, date, per_share (after withholding tax) of every dividend in corporate_actions.csv: as entered, or
    the amount received ÷ the shares held going into the ex-date. None when only an amount is known and no
    shares were held then."""
    book, rows = _Ledger(), []
    for d, kind, r in _events(tx, actions):
        if kind == 0 and r.action == "DIVIDEND":
            held = book.pos.get(r.symbol, (0.0, 0.0))[0]
            if pd.notna(r.per_share):
                dps = r.per_share * (1 - float(getattr(r, "tax", 0.0) or 0.0))
            else:
                dps = r.amount / held if held > 0 else None
            rows.append({"symbol": r.symbol, "date": d, "per_share": dps})
        book.apply(kind, r)
    return pd.DataFrame(rows, columns=["symbol", "date", "per_share"])


def _apply_action(a, qty: float, log: list[dict], warnings: list[str]) -> float:
    """Apply one BONUS / SPLIT / DIVIDEND to a holding of `qty` shares; returns the new share count."""
    day = f"{a.date:%Y-%m-%d}"
    entry = {"date": a.date, "symbol": a.symbol, "action": a.action, "shares": 0.0, "amount": 0.0, "note": a.note,
             "held": qty}
    if a.action == "DIVIDEND":
        if pd.notna(a.amount):
            entry.update(detail="cash received", amount=a.amount)
        else:
            tax = float(getattr(a, "tax", 0.0) or 0.0)
            entry.update(detail=f"{a.per_share:,.2f} a share on {qty:,.0f}" + (f", less {tax * 100:g}% tax" if tax else ""),
                         amount=a.per_share * qty * (1 - tax))
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


STALE_DAYS = 31  # a price older than this doesn't count as today's value


def prices_on(price_hist: pd.DataFrame, day) -> pd.DataFrame:
    """symbol, price, price_date: each symbol's last close on or before `day`, unless older than STALE_DAYS.

    A thinly traded stock's last close can be days old, so its date always comes with it.
    """
    day = pd.Timestamp(day)
    ph = price_hist.assign(date=pd.to_datetime(price_hist["date"]))
    last = ph[ph["date"] <= day].sort_values("date", kind="stable").groupby("symbol").last().reset_index()
    last = last[(day - last["date"]).dt.days <= STALE_DAYS]
    return last.rename(columns={"date": "price_date"})[["symbol", "price", "price_date"]].reset_index(drop=True)


def value_history(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame,
                  index_hist: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    """Portfolio value on each day with prices, next to net invested and the index level.

    `price_hist` has symbol, date, price; `index_hist` has date, value. A day's value is left empty
    unless every holding has a price from the last STALE_DAYS days, so the line never mixes in cost.
    `realized` and `dividends` are the totals up to and including the day.
    """
    cols = ["date", "value", "invested", "cost", "index", "fresh",  # fresh: nothing held going into the day
            "realized", "dividends"]
    if tx.empty:
        return pd.DataFrame(columns=cols)
    start = tx["trade_date"].min()
    ph = price_hist.assign(date=pd.to_datetime(price_hist["date"]))
    ih = index_hist.assign(date=pd.to_datetime(index_hist["date"]))
    days = sorted({*ph["date"], *ih["date"], end})
    days = [d for d in days if start <= d <= end]
    if not days:
        return pd.DataFrame(columns=cols)
    calendar = pd.date_range(min(days[0], ph["date"].min() if len(ph) else days[0]), end, freq="D")
    px = (ph.pivot_table(index="date", columns="symbol", values="price", aggfunc="last")
          .reindex(calendar).ffill(limit=STALE_DAYS))
    level = ih.set_index("date")["value"].sort_index()
    level = level[~level.index.duplicated(keep="last")].reindex(calendar).ffill(limit=10)
    book, events, k, rows = _Ledger(), _events(tx, actions), 0, []
    realized = dividends = 0.0
    n_real = n_log = 0
    for d in days:
        while k < len(events) and events[k][0] < d:  # everything before the day...
            book.apply(events[k][1], events[k][2])
            k += 1
        fresh = not book.held()
        while k < len(events) and events[k][0] <= d:  # ...then the day itself
            book.apply(events[k][1], events[k][2])
            k += 1
        held = book.held()
        realized += sum(x["pnl"] for x in book.realized[n_real:])
        dividends += sum(e["amount"] for e in book.log[n_log:] if e["action"] == "DIVIDEND")
        n_real, n_log = len(book.realized), len(book.log)
        value = 0.0
        for sym, (q, _) in held.items():
            p = px.at[d, sym] if sym in px.columns else float("nan")
            if pd.isna(p):
                value = float("nan")
                break
            value += q * p
        rows.append({"date": d, "value": value, "invested": book.invested,
                     "cost": sum(c for _, c in held.values()), "index": level.get(d, float("nan")), "fresh": fresh,
                     "realized": realized, "dividends": dividends})
    return pd.DataFrame(rows, columns=cols)


def benchmark(hist: pd.DataFrame, flows: list[tuple[pd.Timestamp, float]],
              index_hist: pd.DataFrame) -> tuple[pd.Series, dict | None]:
    """What the same money would be worth in the index (a "public market equivalent").

    From the first day with both a portfolio value and an index level, that value is put in the index;
    afterwards every buy adds index units and every sale or dividend takes some out, at that day's level.
    If nothing was held going into that first day, it starts from nothing and that day's trades count as
    flows, so both sides start from the prices actually paid.

    Selling a big winner can take out more than the index side holds: buy for 1,000 and sell for 3,000 with
    the index flat, and it would have to be −2,000 (docs/04 §5). So the line stops on the day it would go
    below zero (`emptied`), and the index's money-weighted return is then unknown. KS-PME and direct alpha
    can't go negative: they compare the same cash flows over the same window, and are the numbers to read.
    Returns the equivalent value per row of `hist`, and the window with both money-weighted returns.
    """
    from .performance import direct_alpha, ks_pme, level_series  # performance imports this module

    equiv = pd.Series(float("nan"), index=hist.index)
    ok = hist["value"].notna() & hist["index"].notna()
    if not ok.any():
        return equiv, None
    first, last = hist.index[ok][0], hist.index[ok][-1]
    s_day, v0, a0 = hist.at[first, "date"], hist.at[first, "value"], hist.at[first, "index"]
    fresh = bool(hist["fresh"].get(first, False)) if "fresh" in hist else False
    level = level_series(index_hist)
    if fresh:
        v0 = 0.0
    window = sorted((d, a) for d, a in flows if d > s_day or (fresh and d == s_day))
    units, j, emptied = v0 / a0, 0, None
    for i in hist.index[hist.index >= first]:
        d = hist.at[i, "date"]
        while j < len(window) and window[j][0] <= d:
            fd, amt = window[j]
            at = level[:fd].iloc[-1] if len(level[:fd]) else a0
            units -= amt / at  # a buy (negative flow) buys units; a sale or dividend sells them
            j += 1
            if units * at < -0.005 and emptied is None:  # took out more than it held: the line can't go on
                emptied = fd
        if emptied is not None:
            break
        if pd.notna(hist.at[i, "index"]):
            equiv[i] = units * hist.at[i, "index"]
    e_day = hist.at[last, "date"]
    inside = [(s_day, -v0), *((d, a) for d, a in window if d <= e_day)]
    final = float(hist.at[last, "value"])
    mine = xirr([*inside, (e_day, final)])
    theirs = xirr([*inside, (e_day, equiv[last])]) if emptied is None else None
    alpha = direct_alpha(inside, final, e_day, level)
    days = (e_day - s_day).days
    period = lambda r: (1 + r) ** (days / 365) - 1 if r is not None else None  # noqa: E731
    return equiv, {"start": s_day, "end": e_day, "days": days, "xirr": mine, "index_xirr": theirs,
                   "period_return": period(mine), "index_period_return": period(theirs), "emptied": emptied,
                   "ks_pme": ks_pme(inside, final, e_day, level), "direct_alpha": alpha, "period_alpha": period(alpha)}
