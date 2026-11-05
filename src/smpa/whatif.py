"""What if I'd sold on a date? (docs/03-what-if-i-sold.md)

The price on the day in question is each stock's close then, from `price_history` (at most STALE_DAYS old);
a holding without one is left unpriced, never guessed. Fees for a sale that didn't happen use the rate in
force on that day (`fees.fee_rate`).
"""

from __future__ import annotations

import pandas as pd

from .analysis import _events, _Ledger, cash_flows, prices_on, xirr


def _upto(tx: pd.DataFrame, actions: pd.DataFrame | None, day: pd.Timestamp) -> _Ledger:
    """The ledger after every trade and corporate action up to and including `day`."""
    book = _Ledger()
    for when, kind, r in _events(tx, actions):
        if when > day:
            break
        book.apply(kind, r)
    return book


def if_sold_on(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, day, fee_rate: float,
               symbol: str | None = None) -> dict:
    """Every holding (or one symbol) as if sold at the close on `day`, plus what was realized and received by then.

    proceeds = shares held at the close × close × (1 − fee); gain = proceeds − cost basis;
    lifetime = gain + realized P&L up to the day + dividends up to the day.
    """
    day = pd.Timestamp(day)
    if symbol is not None:
        tx = tx[tx["symbol"] == symbol]
        actions = actions[actions["symbol"] == symbol] if actions is not None else None
    book = _upto(tx, actions, day)
    closes = prices_on(price_hist, day).set_index("symbol")
    rows = []
    for sym, (qty, cost) in sorted(book.held().items()):
        row = {"symbol": sym, "quantity": qty, "cost": cost, "price": None, "price_date": None, "gross": None,
               "fees": None, "proceeds": None, "gain": None}
        if sym in closes.index:
            px = float(closes.at[sym, "price"])
            gross = qty * px
            row.update(price=px, price_date=closes.at[sym, "price_date"], gross=gross, fees=gross * fee_rate,
                       proceeds=gross * (1 - fee_rate), gain=gross * (1 - fee_rate) - cost)
        rows.append(row)
    h = pd.DataFrame(rows, columns=["symbol", "quantity", "cost", "price", "price_date", "gross", "fees", "proceeds",
                                    "gain"])
    realized = float(sum(x["pnl"] for x in book.realized))
    dividends = float(sum(e["amount"] for e in book.log if e["action"] == "DIVIDEND"))
    priced = h["proceeds"].notna()
    gain = float(h.loc[priced, "gain"].sum())
    done = tx[tx["trade_date"] <= day]
    divs = pd.DataFrame([e for e in book.log if e["action"] == "DIVIDEND"], columns=["date", "amount"])
    final = float(h.loc[priced, "proceeds"].sum() + h.loc[~priced, "cost"].sum())  # unpriced: at cost
    rate = xirr(cash_flows(done, divs) + [(day, final)]) if len(done) else None
    return {"day": day, "fee_rate": fee_rate, "holdings": h, "complete": bool(priced.all()),
            "proceeds": float(h.loc[priced, "proceeds"].sum()), "fees": float(h.loc[priced, "fees"].sum()),
            "cost": float(h["cost"].sum()), "gain": gain, "realized": realized, "dividends": dividends,
            "lifetime": gain + realized + dividends, "xirr": rate,
            "days": int((day - done["trade_date"].min()).days) if len(done) else 0}


def _only(tx: pd.DataFrame, actions: pd.DataFrame | None, symbol: str):
    a = actions[actions["symbol"] == symbol] if actions is not None else None
    return tx[tx["symbol"] == symbol], a


def _walk(tx: pd.DataFrame, actions: pd.DataFrame | None):
    """After each event: (date, kind, row, shares held going in, ledger, lots, round trips it made)."""
    from .lots import Lots

    book, lots = _Ledger(), Lots()
    for d, kind, r in _events(tx, actions):
        held = book.pos.get(r.symbol, (0.0, 0.0))
        n = len(lots.trips)
        book.apply(kind, r)
        lots.apply(kind, r, book.pos.get(r.symbol, (0.0, 0.0))[0])
        yield d, kind, r, held, book, lots, lots.trips[n:]


def close_on(closes: pd.Series, day) -> tuple[float, pd.Timestamp] | None:
    """The last close on or before `day` and its date, unless older than STALE_DAYS."""
    from .analysis import STALE_DAYS

    s = closes[: pd.Timestamp(day)]
    if s.empty or (pd.Timestamp(day) - s.index[-1]).days > STALE_DAYS:
        return None
    return float(s.iloc[-1]), s.index[-1]


def holding_vs_selling(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame,
                       index_hist: pd.DataFrame, symbol: str, day, today, fee_day: float, fee_today: float) -> dict | None:
    """Follow the exact shares of `symbol` held at the close of `day` (a cohort) up to `today` (docs/03 §3).

    Later sales take the oldest shares first, so they come out of the cohort before anything bought after
    `day`; splits scale it, dividends pay it its share. Holding was worth what the cohort brought in plus
    what is left of it today (after selling fees); selling was worth the shares × close on `day` after fees.
    The fair version grows every amount at the index to today, since money from an earlier sale could
    have been invested meanwhile. None if nothing was held on `day`.
    """
    from .adjust import actual_closes
    from .lots import replay
    from .performance import _level_on, level_series

    tx, actions = _only(tx, actions, symbol)
    day, today = pd.Timestamp(day), pd.Timestamp(today)
    _, at_day = replay(tx, actions, until=day)
    ids = {lot.id for lot in at_day.open.get(symbol, [])}
    shares = at_day.held(symbol)
    if shares <= 0:
        return None
    closes, _ = actual_closes(price_hist, actions, symbol)
    on_day, now = close_on(closes, day), close_on(closes, today)
    cash = []
    left = shares
    for d, kind, r, (held, _), book, lots, trips in _walk(tx, actions):
        if d <= day:
            continue
        if d > today:
            break
        if kind == 1:
            cash += [(d, t.proceeds, "sale") for t in trips if t.lot in ids]
        elif r.action == "DIVIDEND" and held > 0:
            mine = sum(lot.quantity for lot in lots.open.get(symbol, []) if lot.id in ids)
            cash.append((d, book.log[-1]["amount"] * mine / held, "dividend"))
        left = sum(lot.quantity for lot in lots.open.get(symbol, []) if lot.id in ids)
    out = {"day": day, "shares": shares, "price_on_day": on_day[0] if on_day else None,
           "price_date": on_day[1] if on_day else None, "price_now": now[0] if now else None, "shares_left": left,
           "sales": sum(a for _, a, k in cash if k == "sale"), "dividends": sum(a for _, a, k in cash if k == "dividend"),
           "value_now": left * now[0] * (1 - fee_today) if now else (0.0 if left == 0 else None)}
    out["selling"] = shares * on_day[0] * (1 - fee_day) if on_day else None
    out["holding"] = out["sales"] + out["dividends"] + out["value_now"] if out["value_now"] is not None else None
    out["difference"] = out["holding"] - out["selling"] if None not in (out["holding"], out["selling"]) else None
    level = level_series(index_hist)
    i_now, i_day = _level_on(level, today), _level_on(level, day)
    grown = [a * i_now / lv for d, a, _ in cash if (lv := _level_on(level, d))] if i_now else []
    if i_now and i_day and len(grown) == len(cash) and out["difference"] is not None:
        out["holding_fair"] = sum(grown) + out["value_now"]
        out["selling_fair"] = out["selling"] * i_now / i_day
        out["difference_fair"] = out["holding_fair"] - out["selling_fair"]
    else:
        out["holding_fair"] = out["selling_fair"] = out["difference_fair"] = None
    return out


WINDOWS = (1, 3, 6, 12)  # months after a sale


def sales_review(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, index_hist: pd.DataFrame,
                 today, symbol: str | None = None) -> pd.DataFrame:
    """Every market sale (not rights or buyouts) next to what happened after it (docs/03 §4, §5).

    after_k = close k months later ÷ sale price − 1 (the later close in the shares sold, through any split);
    vs_k = that against the ASPI over the same months; left_k = shares sold × (later close − sale price),
    negative for a loss you avoided. efficiency = (sale price − average cost) ÷ (highest close while held −
    average cost): the share of the best possible move that the sale captured, in hindsight (sales at a gain).
    """
    from .adjust import actual_closes, multiplier, to_basis
    from .analysis import NON_MARKET
    from .performance import _level_on, level_series

    today = pd.Timestamp(today)
    if symbol is not None:
        tx, actions = _only(tx, actions, symbol)
    level = level_series(index_hist)
    closes, since, rows = {}, {}, []
    for d, kind, r, (held, cost), book, lots, trips in _walk(tx, actions):
        if kind == 1 and r.side == "BUY" and held <= 0:
            since[r.symbol] = d  # a new holding period starts
        if kind != 1 or r.side != "SELL" or getattr(r, "sources", None) in NON_MARKET:
            continue
        if r.symbol not in closes:
            closes[r.symbol] = actual_closes(price_hist, actions, r.symbol)[0]
        c = closes[r.symbol]
        avg = cost / held if held > 0 else None
        row = {"date": d, "symbol": r.symbol, "quantity": r.quantity, "price": r.price, "avg_cost": avg,
               "gain_pct": r.price / avg - 1 if avg else None}
        start = since.get(r.symbol)
        window = c[start:d] if start is not None else c.iloc[:0]
        if len(window):
            high = float(to_basis(actions, r.symbol, window, d).max())
            row["high"] = high
            # only for a sale at a gain: below cost there was no gain to keep, and a high barely above cost
            # would divide by almost nothing
            row["efficiency"] = (r.price - avg) / (high - avg) if avg and high > avg and r.price >= avg else None
        else:
            row["high"] = row["efficiency"] = None
        i0 = _level_on(level, d)
        for k in WINDOWS:
            t = d + pd.DateOffset(months=k)
            later = close_on(c, t) if t <= today else None
            if later is None:
                row.update({f"after_{k}": None, f"vs_{k}": None, f"left_{k}": None})
                continue
            px = later[0] * multiplier(actions, r.symbol, d, t)
            after = px / r.price - 1
            i1 = _level_on(level, t)
            row.update({f"after_{k}": after, f"vs_{k}": (1 + after) / (i1 / i0) - 1 if i0 and i1 else None,
                        f"left_{k}": r.quantity * (px - r.price)})
        rows.append(row)
    cols = ["date", "symbol", "quantity", "price", "avg_cost", "gain_pct", "high", "efficiency",
            *[f"{x}_{k}" for k in WINDOWS for x in ("after", "vs", "left")]]
    return pd.DataFrame(rows, columns=cols)


def exit_curve(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, symbol: str, end,
               rates) -> pd.DataFrame:
    """One stock's total gain had the whole position been sold at each day's close (docs/03 §5): shares ×
    close × (1 − that day's fee rate) − cost + realized so far + dividends so far, on the days it was held.

    `rates(dates)` gives the fee rate on each date. The best point is the maximum favourable excursion
    (MFE), the worst the maximum adverse excursion (MAE).
    """
    from .adjust import actual_closes
    from .analysis import value_history

    tx, actions = _only(tx, actions, symbol)
    closes, _ = actual_closes(price_hist, actions, symbol)
    ph = pd.DataFrame({"symbol": symbol, "date": closes.index, "price": closes.to_numpy()})
    h = value_history(tx, actions, ph, pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), "value": []}),
                      pd.Timestamp(end))
    h = h[h["value"] > 0].copy()  # held and priced (shares from a bonus can have cost nothing)
    if h.empty:
        return h.assign(price=[], gain=[])
    h["price"] = closes.reindex(h["date"]).ffill().to_numpy()
    h["gain"] = h["value"] * (1 - rates(h["date"]).to_numpy()) - h["cost"] + h["realized"] + h["dividends"]
    return h[["date", "price", "value", "cost", "realized", "dividends", "gain"]].reset_index(drop=True)


def sales_summary(sales: pd.DataFrame) -> dict:
    """Averages over every sale in a `sales_review` (docs/03 §4: judge from the average, never one trade).

    Exit efficiency is the median over the sales that have one. For each window: how many sales it covers yet, the average after-sale return and the same against the
    ASPI, the share of sales that went on to beat the ASPI (above half: what you sold tended to do better than
    the market), and the total left on the table (negative: losses avoided).
    """
    eff = sales["efficiency"].dropna() if len(sales) else pd.Series(dtype=float)
    mean = lambda s: float(s.mean()) if len(s) else None  # noqa: E731
    # the median: one sale just above cost after a big rise swamps a mean
    out = {"sales": int(len(sales)), "efficiency": float(eff.median()) if len(eff) else None,
           "efficiency_n": int(len(eff)), "windows": []}
    for k in WINDOWS:
        after = sales[f"after_{k}"].dropna() if len(sales) else pd.Series(dtype=float)
        vs = sales[f"vs_{k}"].dropna() if len(sales) else pd.Series(dtype=float)
        left = sales[f"left_{k}"].dropna() if len(sales) else pd.Series(dtype=float)
        out["windows"].append({"months": k, "n": int(len(after)), "after": mean(after), "vs": mean(vs),
                               "vs_n": int(len(vs)), "beat": mean(vs > 0) if len(vs) else None,
                               "left": float(left.sum()) if len(left) else None})
    return out
