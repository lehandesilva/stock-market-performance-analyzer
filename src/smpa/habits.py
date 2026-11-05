"""Your trading habits: the decisions, not the portfolio (docs/05-your-trading-habits.md).

You'll have tens of trades, not thousands, so every figure here comes with how many it rests on (`n`), and a
share of them with its 95% range (a Wilson interval): 7 wins in 12 is 58%, but anything from 32% to 81% fits,
skill and luck alike. Prices are closes as traded (`adjust.actual_history`), and returns run through any split
since (`adjust.multiplier`). A price that isn't known leaves that trade out, never guessed.
"""

from __future__ import annotations

import math

import pandas as pd

from .adjust import actual_closes, multiplier
from .analysis import NON_MARKET, _events, _Ledger, compute, dividends_per_share
from .lots import Lots, replay, trips_frame
from .performance import _level_on, level_series
from .whatif import close_on

SWITCH_DAYS = 7  # a buy within about 5 trading days after a sale makes the two a switch
BUY_WINDOW = 20  # trading days after a buy for "buy timing"
LATER = (3, 12)  # months after a buy or a switch to see how it turned out
SWITCH_LATER = (3, 6, 12)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """The 95% range for a share k of n (Wilson 1927). Unlike p ± 2 standard errors it stays inside 0…1 and
    is still sensible for small n, or for 0 or n out of n."""
    if n <= 0:
        return None
    p, d = k / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def share_of(k: int, n: int) -> dict:
    """k of n as a share, with its 95% range (None without any)."""
    return {"k": int(k), "n": int(n), "share": k / n if n else None, "range": wilson(k, n)}


def _mean(s: pd.Series) -> float | None:
    s = pd.Series(s, dtype=float).dropna()
    return float(s.mean()) if len(s) else None


def _median(s: pd.Series) -> float | None:
    s = pd.Series(s, dtype=float).dropna()
    return float(s.median()) if len(s) else None


def _closes(price_hist: pd.DataFrame, actions, symbols) -> dict[str, pd.Series]:
    return {s: actual_closes(price_hist, actions, s)[0] for s in sorted(set(symbols))}


def _market(tx: pd.DataFrame) -> pd.Series:
    return ~tx["sources"].isin(NON_MARKET) if "sources" in tx else pd.Series(True, index=tx.index)


def decisions(tx: pd.DataFrame, side: str) -> pd.DataFrame:
    """Market trades on one side, with the fills of one stock on one day joined into a single decision: a buy
    split into two contracts is still one buy. `price` is the average the fills were done at."""
    m = tx[(tx["side"] == side) & _market(tx)]
    out = (m.groupby(["trade_date", "symbol"], sort=True)
           .agg(quantity=("quantity", "sum"), gross_amount=("gross_amount", "sum"), fees=("fees", "sum"),
                net_amount=("net_amount", "sum")).reset_index())
    out["price"] = out["gross_amount"] / out["quantity"]
    return out


# --- §2 Selling winners too early: the disposition effect ------------------------------------------------------

def disposition(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, max_age: int = 7) -> dict:
    """Odean's (1998) PGR and PLR. On each day you sold something, every stock held at the start of the day is up
    or down (its price against your average cost), and either sold that day (realized) or kept (paper).

    PGR = realized gains ÷ (realized + paper gains), PLR the same for losses. PGR above PLR means you're quicker
    to cash in a winner than to cut a loser: the disposition effect. A stock kept without a close in the
    `max_age` days before can't be called up or down, so it's left out.
    """
    px = (price_hist.assign(date=pd.to_datetime(price_hist["date"]))
          .pivot_table(index="date", columns="symbol", values="price", aggfunc="last").sort_index()
          if len(price_hist) else pd.DataFrame())
    sells = tx[(tx["side"] == "SELL") & _market(tx)]
    book, events, k = _Ledger(), _events(tx, actions), 0
    n = {"realized_gains": 0, "paper_gains": 0, "realized_losses": 0, "paper_losses": 0}
    for day, sold in sells.groupby("trade_date"):
        while k < len(events) and (events[k][0] < day or (events[k][0] == day and events[k][1] == 0)):
            book.apply(events[k][1], events[k][2])  # positions at the start of the day
            k += 1
        got = dict(zip(sold["symbol"], sold["price"]))
        for sym, (q, cost) in book.held().items():
            if sym in got:
                price = got[sym]
            else:
                c = px[sym][:day].dropna() if sym in px else pd.Series(dtype=float)
                if c.empty or (day - c.index[-1]).days > max_age:
                    continue
                price = float(c.iloc[-1])
            if abs(price - cost / q) < 1e-9:
                continue  # exactly at cost: neither
            n[("realized_" if sym in got else "paper_") + ("gains" if price > cost / q else "losses")] += 1
    pgr = share_of(n["realized_gains"], n["realized_gains"] + n["paper_gains"])
    plr = share_of(n["realized_losses"], n["realized_losses"] + n["paper_losses"])
    gap = pgr["share"] - plr["share"] if pgr["n"] and plr["n"] else None
    return {"days": int(sells["trade_date"].nunique()), "counts": n, "pgr": pgr, "plr": plr, "difference": gap}


# --- §3 Round trips ------------------------------------------------------------------------------------------------

def round_trips(tx: pd.DataFrame, actions: pd.DataFrame | None) -> pd.DataFrame:
    """Each market sale matched to the buys it came from, oldest first (FIFO): one row per piece, with its
    return on what those shares cost. Sales the data has no buy for, and buyouts, are left out."""
    _, lots = replay(tx, actions)
    t = trips_frame(lots.trips)
    t = t[t["market"] & t["matched"]].drop(columns=["market", "matched"]).reset_index(drop=True)
    t["return"] = (t["pnl"] / t["cost"]).where(t["cost"] > 0)
    return t.sort_values("sold", ascending=False, kind="stable").reset_index(drop=True)


def trip_stats(trips: pd.DataFrame) -> dict:
    """docs/05 §3: win rate (with its range), average win and loss, payoff ratio (average win ÷ |average loss|),
    profit factor (all wins ÷ |all losses|, above 1 made money), expectancy (the average result per trip) and the
    median days winners and losers were held. A trip that broke even counts as a loss: it didn't pay its fees."""
    wins, losses = trips[trips["pnl"] > 0], trips[trips["pnl"] <= 0]
    avg_win, avg_loss = _mean(wins["pnl"]), _mean(losses["pnl"])
    lost = float(losses["pnl"].sum())
    return {"n": int(len(trips)), "win_rate": share_of(len(wins), len(trips)),
            "average_win": avg_win, "average_loss": avg_loss,
            "average_win_return": _mean(wins["return"]), "average_loss_return": _mean(losses["return"]),
            "payoff": avg_win / abs(avg_loss) if avg_win is not None and avg_loss else None,
            "profit_factor": float(wins["pnl"].sum()) / abs(lost) if lost < 0 else None,
            "expectancy": _mean(trips["pnl"]), "total": float(trips["pnl"].sum()),
            "median_days_win": _median(wins["days"]), "median_days_loss": _median(losses["days"]),
            "n_win": int(len(wins)), "n_loss": int(len(losses))}


# --- §4 What fees cost you --------------------------------------------------------------------------------------------

FEE_TYPES = ["brokerage", "cds", "cse", "sec", "stl", "clearing", "foreign_brokerage"]


def fees_by_year(tx: pd.DataFrame, fee_parts: pd.DataFrame, hist: pd.DataFrame, growth: pd.Series) -> pd.DataFrame:
    """Per calendar year: fees paid, each type of fee (from the Contract Note or Trade Confirmation; `unknown`
    for trades without a breakdown), market buys and sells, the average portfolio value over the days it is
    known, fee drag (fees ÷ that average), turnover (the smaller of buys and sells ÷ that average, as funds define
    it) and the year's time-weighted return to set the drag against."""
    cols = ["year", "fees", *FEE_TYPES, "unknown", "buys", "sells", "average_value", "days_valued", "fee_drag",
            "turnover", "twr", "twr_from", "twr_to"]
    if tx.empty:
        return pd.DataFrame(columns=cols)
    parts = fee_parts[["contract_no", *FEE_TYPES]] if len(fee_parts) else pd.DataFrame(columns=["contract_no", *FEE_TYPES])
    t = tx.merge(parts.astype({c: float for c in FEE_TYPES}), on="contract_no", how="left")  # manual rows: no parts
    known = t[FEE_TYPES].notna().any(axis=1)
    rest = (t["fees"] - t[FEE_TYPES].fillna(0).sum(axis=1)).round(2)  # what the parts don't explain
    t["unknown"] = rest.where(known, t["fees"]).clip(lower=0)
    t["year"] = t["trade_date"].dt.year
    market = t[_market(t)]
    v = hist.dropna(subset=["value"]).set_index("date")["value"] if len(hist) else pd.Series(dtype=float)
    rows = []
    for year in range(int(t["year"].min()), int(t["year"].max() if v.empty else max(t["year"].max(), v.index[-1].year)) + 1):
        y = t[t["year"] == year]
        m = market[market["year"] == year]
        buys = float(m.loc[m["side"] == "BUY", "gross_amount"].sum())
        sells = float(m.loc[m["side"] == "SELL", "gross_amount"].sum())
        vy = v[v.index.year == year] if len(v) else v
        avg = float(vy.mean()) if len(vy) else None
        fees = float(y["fees"].sum())
        tw = _year_twr(growth, year)
        rows.append({"year": year, "fees": fees, **{c: float(y[c].sum()) for c in FEE_TYPES},
                     "unknown": float(y["unknown"].sum()), "buys": buys, "sells": sells, "average_value": avg,
                     "days_valued": int(len(vy)), "fee_drag": fees / avg if avg else None,
                     "turnover": min(buys, sells) / avg if avg else None, **tw})
    return pd.DataFrame(rows, columns=cols)


def _year_twr(growth: pd.Series, year: int) -> dict:
    """The time-weighted return within a calendar year: from the last point of the year before (or the first in
    it) to the last point in it."""
    g = growth.dropna()
    inside = g[g.index.year == year]
    if inside.empty:
        return {"twr": None, "twr_from": None, "twr_to": None}
    before = g[g.index.year < year]
    start = before.index[-1] if len(before) else inside.index[0]
    if start == inside.index[-1]:
        return {"twr": None, "twr_from": None, "twr_to": None}
    return {"twr": float(inside.iloc[-1] / g[start] - 1), "twr_from": start, "twr_to": inside.index[-1]}


def fees_by_stock(tx: pd.DataFrame, actions: pd.DataFrame | None, prices: dict) -> pd.DataFrame:
    """Per symbol: fees paid and the lifetime P&L (realized + unrealized + dividends, after fees). `share` is fees
    ÷ that P&L: high means the stock was traded more than its gains could pay for. Unpriced holdings and stocks
    that lost money have no share."""
    pf = compute(tx, prices, actions)
    realized = pf.realized.groupby("symbol")["pnl"].sum() if len(pf.realized) else pd.Series(dtype=float)
    divs = pf.dividends.groupby("symbol")["amount"].sum() if len(pf.dividends) else pd.Series(dtype=float)
    held = pf.holdings.set_index("symbol")
    rows = []
    for sym, g in tx.groupby("symbol"):
        unreal = held.at[sym, "unrealized_pnl"] if sym in held.index else 0.0
        life = None if pd.isna(unreal) else float(realized.get(sym, 0.0) + divs.get(sym, 0.0) + unreal)
        fees = float(g["fees"].sum())
        rows.append({"symbol": sym, "trades": int(len(g)), "fees": fees, "lifetime": life,
                     "share": fees / life if life and life > 0 else None})
    return pd.DataFrame(rows, columns=["symbol", "trades", "fees", "lifetime", "share"]).sort_values(
        "fees", ascending=False).reset_index(drop=True)


# --- §5 Did trading beat holding? -------------------------------------------------------------------------------------

def _book_on(tx: pd.DataFrame, actions: pd.DataFrame | None, day) -> _Ledger:
    """The ledger after every event up to and including `day`."""
    book = _Ledger()
    for d, kind, r in _events(tx, actions):
        if d > day:
            break
        book.apply(kind, r)
    return book


def trading_alpha(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, growth: pd.Series,
                  today) -> pd.DataFrame:
    """Per calendar year: what the shares held on 1 January would have returned with no trade all year (closes
    through any split, plus the dividends on them), against the time-weighted return you actually got from the
    same days. The difference is your trading alpha: what that year's trades added or cost. The current year
    runs to `today`. A year needs a close for every frozen holding at both ends, and the growth series.

    Only dividends in corporate_actions.csv count, and you usually record only those you received: for shares
    you had sold by the ex-date, the frozen portfolio's dividends are then missing, which flatters trading.
    """
    cols = ["year", "start", "end", "holdings", "frozen", "twr", "alpha", "complete"]
    if tx.empty:
        return pd.DataFrame(columns=cols)
    today = pd.Timestamp(today)
    divs = dividends_per_share(tx, actions)
    closes = _closes(price_hist, actions, tx["symbol"])
    g = growth.dropna()
    rows = []
    for year in range(tx["trade_date"].min().year + 1, today.year + 1):
        start, end = pd.Timestamp(year - 1, 12, 31), min(pd.Timestamp(year, 12, 31), today)
        frozen = _book_on(tx, actions, start).held()
        if not frozen:
            continue
        v0 = v1 = paid = 0.0
        complete = True
        for sym, (q, _) in frozen.items():
            c = closes.get(sym, pd.Series(dtype=float))
            p0, p1 = close_on(c, start), close_on(c, end)
            if p0 is None or p1 is None:
                complete = False
                break
            v0 += q * p0[0]
            v1 += q * multiplier(actions, sym, start, end) * p1[0]
            for r in divs[(divs["symbol"] == sym) & (divs["date"] > start) & (divs["date"] <= end)].itertuples(index=False):
                if r.per_share is None or pd.isna(r.per_share):
                    complete = False  # paid something, but how much a share isn't known
                    break
                # on the frozen shares as they stood going into the ex-date
                paid += r.per_share * q * multiplier(actions, sym, start, r.date - pd.Timedelta(days=1))
            if not complete:
                break
        fr = (v1 + paid) / v0 - 1 if complete and v0 > 0 else None
        g0, g1 = _at(g, start), _at(g, end)
        twr = g1 / g0 - 1 if g0 and g1 else None
        rows.append({"year": year, "start": start, "end": end, "holdings": len(frozen), "frozen": fr, "twr": twr,
                     "alpha": twr - fr if None not in (twr, fr) else None, "complete": complete})
    return pd.DataFrame(rows, columns=cols)


def _at(s: pd.Series, day, max_days: int = 10) -> float | None:
    """s on or before `day`, at most `max_days` old."""
    x = s[: pd.Timestamp(day)]
    return float(x.iloc[-1]) if len(x) and (pd.Timestamp(day) - x.index[-1]).days <= max_days else None


def switches(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, today) -> pd.DataFrame:
    """A market sale of one stock followed within SWITCH_DAYS by a market buy of another: a switch (docs/05 §5).
    Each buy pairs with the latest sale before it (or on the same day) not already paired; fills of one stock
    on one day count as one trade.

    gain_k = (1 + bought's return) ÷ (1 + sold's return) − 1 − the fees both legs cost (about 2.26%), each
    return from its own trade price to the close k months after the buy, through any split.
    """
    today = pd.Timestamp(today)
    sells, buys = decisions(tx, "SELL"), decisions(tx, "BUY")
    used, rows = set(), []
    closes = _closes(price_hist, actions, pd.concat([sells["symbol"], buys["symbol"]]))
    for b in buys.itertuples():
        cand = sells[(sells["trade_date"] <= b.trade_date) & (sells["trade_date"] >= b.trade_date - pd.Timedelta(days=SWITCH_DAYS))
                     & (sells["symbol"] != b.symbol) & ~sells.index.isin(used)]
        if cand.empty:
            continue
        s = cand.iloc[-1]
        used.add(cand.index[-1])
        cost = s["fees"] / s["gross_amount"] + b.fees / b.gross_amount if s["gross_amount"] and b.gross_amount else 0.0
        row = {"sold_on": s["trade_date"], "sold": s["symbol"], "sold_price": s["price"], "bought_on": b.trade_date,
               "bought": b.symbol, "bought_price": b.price, "amount": float(b.gross_amount), "fees": cost}
        for k in SWITCH_LATER:
            t = b.trade_date + pd.DateOffset(months=k)
            x = close_on(closes[s["symbol"]], t) if t <= today else None
            y = close_on(closes[b.symbol], t) if t <= today else None
            if x is None or y is None:
                row[f"gain_{k}"] = None
                continue
            rx = x[0] * multiplier(actions, s["symbol"], s["trade_date"], t) / s["price"]
            ry = y[0] * multiplier(actions, b.symbol, b.trade_date, t) / b.price
            row[f"gain_{k}"] = ry / rx - 1 - cost
        rows.append(row)
    cols = ["sold_on", "sold", "sold_price", "bought_on", "bought", "bought_price", "amount", "fees",
            *[f"gain_{k}" for k in SWITCH_LATER]]
    return pd.DataFrame(rows, columns=cols)


def never_sold(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, index_hist: pd.DataFrame,
               today) -> dict:
    """Every share you ever sold on the market, at its latest close (through any split since), against what the
    sales brought in, and against that money had it gone into the ASPI (docs/05 §5). Blunt: the dividends the
    shares would have paid meanwhile are left out. Sales of a stock without a recent close aren't counted."""
    today = pd.Timestamp(today)
    sells = decisions(tx, "SELL")
    closes = _closes(price_hist, actions, sells["symbol"])
    level = level_series(index_hist)
    i_now = _level_on(level, today)
    proceeds = value = grown = 0.0
    priced, unpriced, index_ok = 0, set(), i_now is not None
    for r in sells.itertuples(index=False):
        now = close_on(closes[r.symbol], today)
        if now is None:
            unpriced.add(r.symbol)
            continue
        priced += 1
        proceeds += r.net_amount
        value += r.quantity * multiplier(actions, r.symbol, r.trade_date, today) * now[0]
        i0 = _level_on(level, r.trade_date)
        if i0 is None or i_now is None:
            index_ok = False
        else:
            grown += r.net_amount * i_now / i0
    return {"sales": int(len(sells)), "priced": priced, "unpriced": sorted(unpriced), "proceeds": proceeds,
            "value": value, "difference": value - proceeds if priced else None,
            "in_aspi": grown if priced and index_ok else None}


# --- §6 Timing -------------------------------------------------------------------------------------------------------

def buys_review(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, index_hist: pd.DataFrame,
                today) -> pd.DataFrame:
    """Every market buy (fills of one stock on one day joined), tagged from the position at the start of that day:
    `new` (none held), `winner` (adding to a stock held above your average cost) or `down` (averaging down, below
    it). Then how it turned out (docs/05 §6): the stock's return from the price paid over the next BUY_WINDOW
    trading days (the ASPI's days) minus the ASPI's, and its return 3 and 12 months later, through any split."""
    today = pd.Timestamp(today)
    level = level_series(index_hist)
    buys = decisions(tx, "BUY")
    closes = _closes(price_hist, actions, buys["symbol"])
    days_with_buys, start = set(buys["trade_date"]), {}
    book = _Ledger()
    for d, kind, r in _events(tx, actions):
        if kind == 1 and d in days_with_buys and d not in start:
            start[d] = dict(book.pos)  # after the day's corporate actions, before its trades
        book.apply(kind, r)
    rows = []
    for b in buys.itertuples(index=False):
        d = b.trade_date
        q, cost = start[d].get(b.symbol, (0.0, 0.0))
        tag = "new" if q <= 0 else ("winner" if b.price > cost / q else "down")
        row = {"date": d, "symbol": b.symbol, "quantity": b.quantity, "price": b.price, "kind": tag,
               "avg_cost": cost / q if q > 0 else None}
        c = closes[b.symbol]
        after = level.index[level.index > d]
        t = after[BUY_WINDOW - 1] if len(after) >= BUY_WINDOW and after[BUY_WINDOW - 1] <= today else None
        later = close_on(c, t) if t is not None else None
        i0 = _level_on(level, d)
        if later is not None and i0:
            stock = later[0] * multiplier(actions, b.symbol, d, t) / b.price - 1
            aspi = float(level[t]) / i0 - 1
            row.update(after_20=stock, aspi_20=aspi, excess_20=stock - aspi)
        else:
            row.update(after_20=None, aspi_20=None, excess_20=None)
        for k in LATER:
            t = d + pd.DateOffset(months=k)
            x = close_on(c, t) if t <= today else None
            row[f"after_{k}"] = x[0] * multiplier(actions, b.symbol, d, t) / b.price - 1 if x else None
        rows.append(row)
    cols = ["date", "symbol", "quantity", "price", "kind", "avg_cost", "after_20", "aspi_20", "excess_20",
            *[f"after_{k}" for k in LATER]]
    return pd.DataFrame(rows, columns=cols)


def buys_summary(buys: pd.DataFrame) -> list[dict]:
    """Averages by kind of buy (and over all of them), each with how many buys it covers."""
    out = []
    for kind, g in [("all", buys), *((k, buys[buys["kind"] == k]) for k in ("new", "winner", "down"))]:
        row = {"kind": kind, "n": int(len(g))}
        for col in ("excess_20", *(f"after_{k}" for k in LATER)):
            x = pd.Series(g[col], dtype=float).dropna()
            row[col], row[f"n_{col}"] = (float(x.mean()) if len(x) else None), int(len(x))
        out.append(row)
    return out


def switch_summary(sw: pd.DataFrame) -> list[dict]:
    """The average gain of your switches k months on, with how many it covers and how many came out ahead."""
    out = []
    for k in SWITCH_LATER:
        x = pd.Series(sw[f"gain_{k}"], dtype=float).dropna()
        out.append({"months": k, "n": int(len(x)), "average": float(x.mean()) if len(x) else None,
                    "ahead": share_of(int((x > 0).sum()), len(x))})
    return out


# --- §8 The journal ----------------------------------------------------------------------------------------------------

def journal_review(tx: pd.DataFrame, actions: pd.DataFrame | None, journal: pd.DataFrame, prices: dict,
                   fee: float) -> pd.DataFrame:
    """Each buy you gave a reason for (matched by contract number) and how its own shares did, FIFO: what they
    sold for less what they cost, the dividends paid on them, and what is left at today's price after selling
    fees. `outcome` is None while some of them are held without a price."""
    cols = ["contract_no", "date", "symbol", "quantity", "cost", "reason", "target", "stop", "note", "realized",
            "dividends", "left", "value", "outcome", "return"]
    if journal.empty or tx.empty:
        return pd.DataFrame(columns=cols)
    why = journal.set_index("contract_no")
    book, lots = _Ledger(), Lots()
    lot_of, paid, buys = {}, {}, {}
    for d, kind, r in _events(tx, actions):
        held = book.pos.get(r.symbol, (0.0, 0.0))[0]
        book.apply(kind, r)
        lots.apply(kind, r, book.pos.get(r.symbol, (0.0, 0.0))[0])
        if kind == 1 and r.side == "BUY" and r.contract_no in why.index:
            lot = lots.open[r.symbol][-1]
            lot_of[lot.id], buys[lot.id] = r.contract_no, r
        elif kind == 0 and r.action == "DIVIDEND" and held > 0:
            amount = book.log[-1]["amount"]
            for lot in lots.open.get(r.symbol, []):
                paid[lot.id] = paid.get(lot.id, 0.0) + amount * lot.quantity / held
    realized: dict[int, float] = {}
    for t in lots.trips:
        if t.lot in lot_of:
            realized[t.lot] = realized.get(t.lot, 0.0) + t.pnl
    left = {lot.id: lot for ls in lots.open.values() for lot in ls if lot.id in lot_of}
    rows = []
    for lid, r in buys.items():
        j = why.loc[r.contract_no]
        lot = left.get(lid)
        px = prices.get(r.symbol)
        value = (lot.quantity * px * (1 - fee) - lot.cost if px else None) if lot else 0.0
        outcome = realized.get(lid, 0.0) + paid.get(lid, 0.0) + value if value is not None else None
        rows.append({"contract_no": r.contract_no, "date": r.trade_date, "symbol": r.symbol, "quantity": r.quantity,
                     "cost": r.net_amount, "reason": j["reason"], "target": j["target"], "stop": j["stop"],
                     "note": j["note"], "realized": realized.get(lid, 0.0), "dividends": paid.get(lid, 0.0),
                     "left": lot.quantity if lot else 0.0, "value": value, "outcome": outcome,
                     "return": outcome / r.net_amount if outcome is not None and r.net_amount else None})
    return pd.DataFrame(rows, columns=cols).sort_values("date", ascending=False).reset_index(drop=True)


def by_reason(reviewed: pd.DataFrame) -> list[dict]:
    """The journal grouped by reason: how many buys, what they made in all, their average return, and how many
    made money (with its range). Reasons are compared ignoring case and spaces at the ends, and shown as first
    written."""
    if reviewed.empty:
        return []
    r = reviewed.sort_values("date", kind="stable").assign(key=reviewed["reason"].str.strip().str.lower())
    out = []
    for _, g in r.groupby("key"):
        done = g[g["outcome"].notna()]
        out.append({"reason": g["reason"].iloc[0].strip(), "n": int(len(g)), "priced": int(len(done)),
                    "outcome": float(done["outcome"].sum()) if len(done) else None, "average_return": _mean(done["return"]),
                    "made_money": share_of(int((done["outcome"] > 0).sum()), len(done))})
    return sorted(out, key=lambda x: -x["n"])
