"""Your trading habits: judging the decisions, not the portfolio (docs/05-your-trading-habits.md).

You'll have tens of trades, not thousands, so every figure here comes with how many trades it rests on, and a
proportion comes with its 95% range (Wilson interval). Prices are closes as traded (`adjust.actual_closes`);
across a split, later closes are put on the trade's share basis with `adjust.multiplier`. A figure that needs
a price nobody has is left out and counted, never guessed.
"""

from __future__ import annotations

import math

import pandas as pd

from .adjust import actual_closes, share_events, to_basis
from .analysis import NON_MARKET, STALE_DAYS, _events, _Ledger, prices_on
from .lots import trips_frame
from .performance import _level_on, ks_pme
from .whatif import _upto, _walk, close_on

HORIZONS = (3, 6, 12)  # months, for switches and buys
SWITCH_DAYS = 7  # a buy within this many days after a sale (about 5 trading days) makes it a switch
BUY_DAYS = 20  # trading days after a buy, for buy timing
STOPS = (0.10, 0.15, 0.20, 0.30)  # trailing stops tried


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% range for a proportion k/n (Wilson 1927). 7 of 12 gives 32% to 81%: small samples prove little."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def _market(r) -> bool:
    return getattr(r, "sources", None) not in NON_MARKET


class _Closes:
    """Each symbol's closes as traded, loaded once."""

    def __init__(self, price_hist: pd.DataFrame, actions: pd.DataFrame | None):
        self.ph, self.actions, self.cache, self.events = price_hist, actions, {}, {}

    def __getitem__(self, symbol: str) -> pd.Series:
        if symbol not in self.cache:
            self.cache[symbol] = actual_closes(self.ph, self.actions, symbol)[0]
        return self.cache[symbol]

    def mult(self, symbol: str, start, end) -> float:
        """`adjust.multiplier`, with each symbol's splits read once."""
        if symbol not in self.events:
            self.events[symbol] = share_events(self.actions, symbol)
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        return math.prod(k for when, k in self.events[symbol] if start < when <= end)

    def later(self, symbol: str, day, price: float, when) -> float | None:
        """Return from `price` on `day` to the close on `when` (on the same share basis), None without one."""
        c = close_on(self[symbol], when)
        if c is None or not price:
            return None
        return c[0] * self.mult(symbol, day, when) / price - 1


# --- 2. Selling winners too early ---------------------------------------------------------------------------

def disposition(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, max_age: int = 7) -> dict:
    """Odean's (1998) PGR and PLR: on the days you sold, what share of your winners, and of your losers, you sold.

    Each stock held at the start of a selling day is up or down (price against average cost), and sold that day
    (realized) or kept (paper). The price is the sale price for what was sold, else the last close (at most
    `max_age` days old; without one the stock is left out). PGR above PLR: quicker to cash in a winner than to
    cut a loser.
    """
    px = price_hist.assign(date=pd.to_datetime(price_hist["date"])).pivot_table(
        index="date", columns="symbol", values="price", aggfunc="last").sort_index()
    sells = tx[(tx["side"] == "SELL") & ~tx["sources"].isin(NON_MARKET)]
    book, events, k = _Ledger(), _events(tx, actions), 0
    n = {"rg": 0, "pg": 0, "rl": 0, "pl": 0, "unpriced": 0, "days": 0}
    for day, sold in sells.groupby("trade_date"):
        while k < len(events) and (events[k][0] < day or (events[k][0] == day and events[k][1] == 0)):
            book.apply(events[k][1], events[k][2])  # positions at the start of the day
            k += 1
        n["days"] += 1
        got = dict(zip(sold["symbol"], sold["price"]))
        for sym, (q, cost) in book.held().items():
            if sym in got:
                price = got[sym]
            else:
                closes = px[sym][:day].dropna() if sym in px else pd.Series(dtype=float)
                if closes.empty or (day - closes.index[-1]).days > max_age:
                    n["unpriced"] += 1
                    continue
                price = closes.iloc[-1]
            n[("r" if sym in got else "p") + ("g" if price > cost / q else "l")] += 1

    def ratio(a, b):
        m = n[a] + n[b]
        return {"value": n[a] / m if m else None, "n": m, "range": wilson(n[a], m)}

    pgr, plr = ratio("rg", "pg"), ratio("rl", "pl")
    return {"pgr": pgr, "plr": plr, "counts": n,
            "difference": pgr["value"] - plr["value"] if None not in (pgr["value"], plr["value"]) else None}


# --- 3. Round trips -------------------------------------------------------------------------------------------

def round_trips(trips: pd.DataFrame) -> dict:
    """Win rate, payoff ratio, profit factor, expectancy and days held, over market sales matched to a buy."""
    t = trips[trips["market"] & trips["matched"]] if len(trips) else trips
    wins, losses = t[t["pnl"] > 0], t[t["pnl"] <= 0]
    n = len(t)
    avg_win = float(wins["pnl"].mean()) if len(wins) else None
    avg_loss = float(losses["pnl"].mean()) if len(losses) else None
    rate = len(wins) / n if n else None
    med = lambda s: float(s.median()) if len(s) else None  # noqa: E731
    return {
        "n": n, "wins": len(wins), "losses": len(losses), "win_rate": rate, "win_range": wilson(len(wins), n),
        "average_win": avg_win, "average_loss": avg_loss,
        "payoff": avg_win / abs(avg_loss) if avg_win is not None and avg_loss else None,
        "profit_factor": float(wins["pnl"].sum() / abs(losses["pnl"].sum())) if len(losses) and losses["pnl"].sum() else None,
        "expectancy": float(t["pnl"].mean()) if n else None, "total": float(t["pnl"].sum()) if n else 0.0,
        "return": float(t["pnl"].sum() / t["cost"].sum()) if n and t["cost"].sum() else None,
        "days_winners": med(wins["days"].dropna()), "days_losers": med(losses["days"].dropna()),
        "fees": float((t["buy_fees"] + t["sell_fees"]).sum()) if n else 0.0,
    }


# --- Each buy, to date: the building block for timing, averaging down, the journal and the stop rule ----------

def _history(tx: pd.DataFrame, actions: pd.DataFrame | None):
    """One pass over every event: each buy's lot, its sales and dividends, the dividend per share on each ex-date,
    and the round trips."""
    lots_info, cash, dps, last = {}, {}, {}, None
    for d, kind, r, (held, cost), book, lots, trips in _walk(tx, actions):
        last = lots
        if kind == 1 and r.side == "BUY":
            lots_info[lots._next] = {
                "lot": lots._next, "symbol": r.symbol, "date": d, "contract_no": r.contract_no or None,
                "quantity": float(r.quantity), "price": float(r.price), "cost": float(r.net_amount),
                "fee_rate": float(r.fees / r.gross_amount) if r.gross_amount else 0.0, "market": _market(r),
                "kind": "new" if held <= 0 else ("down" if r.price < cost / held else "up")}
        for t in trips:
            if t.lot is not None:
                cash.setdefault(t.lot, []).append((d, t.proceeds, "sale" if t.market else "buyout"))
        if kind == 0 and r.action == "DIVIDEND":
            amount = book.log[-1]["amount"]
            per = r.per_share * (1 - float(getattr(r, "tax", 0.0) or 0.0)) if pd.notna(r.per_share) else (
                amount / held if held > 0 else None)
            dps[(r.symbol, d)] = per
            if held > 0:
                for lot in lots.open.get(r.symbol, []):
                    cash.setdefault(lot.id, []).append((d, amount * lot.quantity / held, "dividend"))
    return lots_info, cash, dps, last


def lot_outcomes(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, index_hist: pd.DataFrame,
                 today, fee_today: float, closes: _Closes | None = None, hist=None) -> pd.DataFrame:
    """Every buy as a FIFO lot, to date: what its sales and dividends brought in, plus the shares left at today's
    close after selling fees, against what it cost. `ks_pme` grows each amount at the ASPI to today (above 1: the
    buy beat the index over the same days). None where a share is left that has no close today."""
    from .performance import level_series

    today = pd.Timestamp(today)
    closes = closes or _Closes(price_hist, actions)
    lots_info, cash, _, last = hist or _history(tx, actions)
    left = {lot.id: lot.quantity for ls in (last.open.values() if last else []) for lot in ls}
    level = level_series(index_hist)
    rows = []
    for lid, info in lots_info.items():
        got = cash.get(lid, [])
        q = left.get(lid, 0.0)
        now = close_on(closes[info["symbol"]], today) if q > 0 else None
        value = q * now[0] * (1 - fee_today) if now else (0.0 if q <= 1e-9 else None)
        back = sum(a for _, a, _ in got)
        row = {**info, "sales": sum(a for _, a, k in got if k != "dividend"),
               "dividends": sum(a for _, a, k in got if k == "dividend"), "left": q, "value": value,
               "ended": max(d for d, _, k in got if k != "dividend") if q <= 1e-9 and got else None,
               "outcome": back + value if value is not None else None}
        row["return"] = row["outcome"] / info["cost"] - 1 if row["outcome"] is not None and info["cost"] else None
        row["ks_pme"] = (ks_pme([(info["date"], -info["cost"])] + [(d, a) for d, a, _ in got], value, today, level)
                         if value is not None and info["cost"] and len(level) else None)
        rows.append(row)
    cols = ["lot", "symbol", "date", "contract_no", "quantity", "price", "cost", "fee_rate", "market", "kind", "sales",
            "dividends", "left", "value", "ended", "outcome", "return", "ks_pme"]
    return pd.DataFrame(rows, columns=cols)


def _group(df: pd.DataFrame) -> dict:
    """n, average return, how many made money (with its range), median KS-PME and how many beat the ASPI."""
    r, k = df["return"].dropna(), df["ks_pme"].dropna()
    return {"n": int(len(df)), "priced": int(len(r)), "return": float(r.mean()) if len(r) else None,
            "made_money": float((r > 0).mean()) if len(r) else None, "made_money_range": wilson(int((r > 0).sum()), len(r)),
            "ks_pme": float(k.median()) if len(k) else None, "beat": float((k > 1).mean()) if len(k) else None,
            "beat_n": int(len(k)), "cost": float(df["cost"].sum())}


# --- 4. What fees cost you ----------------------------------------------------------------------------------

FEE_TYPES = ["brokerage", "cds", "cse", "sec", "stl", "clearing", "foreign_brokerage"]


def fees_by_year(tx: pd.DataFrame, fee_columns: pd.DataFrame, hist: pd.DataFrame, growth: pd.Series) -> pd.DataFrame:
    """Per calendar year: fees, average portfolio value (over days with one), fee drag = fees ÷ that, turnover =
    the smaller of buys and sells ÷ that, the year's time-weighted return, and fees by type.

    `fee_columns` has contract_no and each fee; trades without a breakdown (statement-only, typed in) are
    'unknown'. A year needs 20 days with a value for its drag and turnover.
    """
    if tx.empty:
        return pd.DataFrame(columns=["year"])
    t = tx.assign(year=tx["trade_date"].dt.year)
    market = t[~t["sources"].isin(NON_MARKET)]
    v = hist.dropna(subset=["value"]).set_index("date")["value"] if len(hist) else pd.Series(dtype=float)
    split = t[["year", "contract_no", "fees"]].merge(fee_columns, on="contract_no", how="left") \
        if len(fee_columns) else t[["year", "contract_no", "fees"]].assign(**{c: float("nan") for c in FEE_TYPES})
    for c in FEE_TYPES:
        split[c] = split[c].astype(float) if c in split else float("nan")
    known = split[FEE_TYPES].notna().any(axis=1) & (split["contract_no"].fillna("") != "")
    split.loc[~known, FEE_TYPES] = 0.0
    split[FEE_TYPES] = split[FEE_TYPES].fillna(0.0)
    split["unknown"] = (split["fees"] - split[FEE_TYPES].sum(axis=1)).where(~known, 0.0).clip(lower=0)
    rows = []
    for y in sorted(t["year"].unique()):
        yv = v[v.index.year == y]
        avg = float(yv.mean()) if len(yv) >= 20 else None
        m = market[market["year"] == y]
        buys, sells = m.loc[m["side"] == "BUY", "gross_amount"].sum(), m.loc[m["side"] == "SELL", "gross_amount"].sum()
        fees = float(t.loc[t["year"] == y, "fees"].sum())
        g_end = growth[growth.index.year == y]
        g_before = growth[growth.index.year < y]
        twr = (float(g_end.iloc[-1] / g_before.iloc[-1] - 1) if len(g_end) and len(g_before) else
               (float(g_end.iloc[-1] / g_end.iloc[0] - 1) if len(g_end) > 1 else None))
        types = split[split["year"] == y][[*FEE_TYPES, "unknown"]].sum()
        rows.append({"year": int(y), "trades": int((t["year"] == y).sum()), "fees": fees, "buys": float(buys),
                     "sells": float(sells), "average_value": avg, "valued_days": int(len(yv)),
                     "drag": fees / avg if avg else None, "turnover": float(min(buys, sells)) / avg if avg else None,
                     "twr": twr, "twr_from": (g_end.index[0] if len(g_end) and not len(g_before) else None),
                     **{c: float(types[c]) for c in [*FEE_TYPES, "unknown"]}})
    return pd.DataFrame(rows)


def fees_by_stock(tx: pd.DataFrame, pf) -> pd.DataFrame:
    """Per symbol: fees paid, lifetime P&L (realized + dividends + unrealized; None while a holding is unpriced),
    and fees ÷ that P&L. High means the stock was traded more than its gains could pay for."""
    fees = tx.groupby("symbol")["fees"].sum()
    trades = tx.groupby("symbol").size()
    real = pf.realized.groupby("symbol")["pnl"].sum() if len(pf.realized) else pd.Series(dtype=float)
    divs = pf.dividends.groupby("symbol")["amount"].sum() if len(pf.dividends) else pd.Series(dtype=float)
    unreal = pf.holdings.set_index("symbol")["unrealized_pnl"] if len(pf.holdings) else pd.Series(dtype=float)
    rows = []
    for s in sorted(fees.index):
        u = unreal.get(s, 0.0)
        life = None if pd.isna(u) else float(real.get(s, 0.0) + divs.get(s, 0.0) + u)
        rows.append({"symbol": s, "trades": int(trades[s]), "fees": float(fees[s]), "lifetime": life,
                     "fees_share": float(fees[s] / life) if life and life > 0 else None})
    return pd.DataFrame(rows, columns=["symbol", "trades", "fees", "lifetime", "fees_share"]).sort_values(
        "fees", ascending=False).reset_index(drop=True)


# --- 5. Did trading beat holding? -------------------------------------------------------------------------------

def frozen_years(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, growth: pd.Series,
                 dps: dict, today, closes: _Closes | None = None) -> pd.DataFrame:
    """Each year: the holdings of 1 January, left alone all year (prices plus dividends), against that year's
    time-weighted return. trading alpha = TWR − frozen: what your trades added or cost that year.

    A year is left out unless the time-weighted series reaches back to its start and every 1 January holding
    has a close then and at the year's end (today for this year).
    """
    today = pd.Timestamp(today)
    closes = closes or _Closes(price_hist, actions)
    if tx.empty or growth.empty:
        return pd.DataFrame(columns=["year"])
    rows = []
    for y in range(max(tx["trade_date"].min().year, growth.index[0].year), today.year + 1):
        start, end = pd.Timestamp(y - 1, 12, 31), min(pd.Timestamp(y, 12, 31), today)
        g0 = growth[growth.index <= start]
        g1 = growth[growth.index <= end]
        if g0.empty or (start - g0.index[-1]).days > 10 or g1.index[-1] <= g0.index[-1]:
            continue
        held = _upto(tx, actions, start).held()
        if not held:
            continue
        p0, p1 = prices_on(price_hist, start).set_index("symbol"), prices_on(price_hist, end).set_index("symbol")
        v0 = v1 = divs = 0.0
        missing = []
        for sym, (q, _) in held.items():
            if sym not in p0.index or sym not in p1.index:
                missing.append(sym)
                continue
            v0 += q * float(p0.at[sym, "price"])
            v1 += q * closes.mult(sym, start, end) * float(p1.at[sym, "price"])
            for (s, d), per in dps.items():
                if s == sym and start < d <= end:
                    if per is None:
                        missing.append(f"{sym} dividend")
                    else:
                        divs += q * closes.mult(sym, start, d - pd.Timedelta(days=1)) * per
        twr = float(g1.iloc[-1] / g0.iloc[-1] - 1)
        frozen = (v1 + divs) / v0 - 1 if v0 and not missing else None
        rows.append({"year": y, "end": end, "holdings": len(held), "frozen": frozen, "twr": twr,
                     "alpha": twr - frozen if frozen is not None else None, "missing": ", ".join(missing)})
    return pd.DataFrame(rows, columns=["year", "end", "holdings", "frozen", "twr", "alpha", "missing"])


def switches(tx: pd.DataFrame, closes: _Closes, today) -> pd.DataFrame:
    """A sale followed within SWITCH_DAYS by a buy of something else: a switch from X to Y. For each horizon,
    gain = (1 + Y's return) ÷ (1 + X's return) − 1 − what the switch cost in fees (the sale's and the buy's
    fee rates), from the trade prices. Positive: your new idea beat your old one."""
    today = pd.Timestamp(today)
    m = tx[~tx["sources"].isin(NON_MARKET)]
    sells, buys = m[m["side"] == "SELL"], m[m["side"] == "BUY"]
    rows = []
    for s in sells.itertuples(index=False):
        near = buys[(buys["trade_date"] >= s.trade_date) & (buys["trade_date"] <= s.trade_date + pd.Timedelta(days=SWITCH_DAYS))
                    & (buys["symbol"] != s.symbol)]
        for b in near.itertuples(index=False):
            cost = (s.fees / s.gross_amount if s.gross_amount else 0) + (b.fees / b.gross_amount if b.gross_amount else 0)
            row = {"sold": s.trade_date, "from": s.symbol, "bought": b.trade_date, "to": b.symbol, "cost": cost}
            for k in HORIZONS:
                t = b.trade_date + pd.DateOffset(months=k)
                rx = closes.later(s.symbol, s.trade_date, s.price, t) if t <= today else None
                ry = closes.later(b.symbol, b.trade_date, b.price, t) if t <= today else None
                row[f"gain_{k}"] = (1 + ry) / (1 + rx) - 1 - cost if None not in (rx, ry) else None
            rows.append(row)
    return pd.DataFrame(rows, columns=["sold", "from", "bought", "to", "cost", *[f"gain_{k}" for k in HORIZONS]])


def never_sold(tx: pd.DataFrame, closes: _Closes, today, fee_today: float) -> dict:
    """Every share you ever sold on the market, at today's close after selling fees, against what its sales
    brought in. Positive: keeping them all would have paid more (dividends since left out)."""
    today = pd.Timestamp(today)
    sells = tx[(tx["side"] == "SELL") & ~tx["sources"].isin(NON_MARKET)]
    worth = got = 0.0
    n = unknown = 0
    for s in sells.itertuples(index=False):
        c = close_on(closes[s.symbol], today)
        if c is None:
            unknown += 1
            continue
        n += 1
        worth += s.quantity * closes.mult(s.symbol, s.trade_date, today) * c[0] * (1 - fee_today)
        got += s.net_amount
    return {"sales": n, "unpriced": unknown, "worth_today": worth, "proceeds": got,
            "difference": worth - got if n else None}


# --- 6. Timing --------------------------------------------------------------------------------------------------

def buy_timing(lots: pd.DataFrame, closes: _Closes, index_hist: pd.DataFrame) -> dict:
    """Each market buy's return over the next BUY_DAYS trading days (the ASPI's calendar) minus the ASPI's.
    Positive on average: you tend to buy just before a rise."""
    from .performance import level_series

    level = level_series(index_hist)
    out = []
    for r in lots[lots["market"]].itertuples(index=False) if len(lots) else []:
        after = level[level.index > r.date]
        i0 = _level_on(level, r.date)
        if len(after) < BUY_DAYS or not i0:
            continue
        t = after.index[BUY_DAYS - 1]
        ret = closes.later(r.symbol, r.date, r.price, t)
        if ret is not None and (t - r.date).days <= 3 * BUY_DAYS:
            out.append(ret - (float(after.iloc[BUY_DAYS - 1]) / i0 - 1))
    s = pd.Series(out, dtype=float)
    return {"n": int(len(s)), "excess": float(s.mean()) if len(s) else None,
            "positive": float((s > 0).mean()) if len(s) else None, "positive_range": wilson(int((s > 0).sum()), len(s))}


# --- 5 §3 of docs/03: a rule instead of you --------------------------------------------------------------------

def trailing_stop(lots: pd.DataFrame, cash: dict, dps: dict, closes: _Closes, today, rates,
                  stops=STOPS) -> pd.DataFrame:
    """A trailing stop run over your own buys, fees included (docs/03 §5): each lot is sold at the first close X%
    below the highest close since it was bought (the buy price counts as the first high), or held to today.
    Against what you actually did with the same shares, dividends included on both sides.

    Only market buys whose stock has closes from within STALE_DAYS of the buy through today are tried; bought
    before price history starts, the rule couldn't have watched the price. Money from an earlier exit isn't
    reinvested on either side, so compare the totals, not the timing.
    """
    today = pd.Timestamp(today)
    seen: dict = {}
    fee_on = lambda d: seen[d] if d in seen else seen.setdefault(d, float(rates([d]).iloc[0]))  # noqa: E731
    rows = []
    for x in stops:
        rule = actual = 0.0
        n = fired = 0
        for r in lots[lots["market"]].itertuples(index=False) if len(lots) else []:
            if r.outcome is None or pd.isna(r.outcome) or any(k == "buyout" for _, _, k in cash.get(r.lot, [])):
                continue
            c = closes[r.symbol]
            path = c[(c.index >= r.date) & (c.index <= today)]
            if path.empty or (path.index[0] - r.date).days > STALE_DAYS or close_on(c, today) is None:
                continue
            p = to_basis(closes.actions, r.symbol, path, r.date)  # on the buy's share basis
            high = p.cummax().clip(lower=r.price).shift(1, fill_value=r.price)  # the high before each close
            hit = p[p <= (1 - x) * high]
            exit_day = hit.index[0] if len(hit) else None
            stop_day = exit_day if exit_day is not None else today
            exit_px = float(hit.iloc[0]) if len(hit) else float(p.iloc[-1])
            got = r.quantity * exit_px * (1 - fee_on(stop_day))
            got += sum(r.quantity * closes.mult(r.symbol, r.date, d - pd.Timedelta(days=1)) * per
                       for (s, d), per in dps.items() if s == r.symbol and r.date < d <= stop_day and per)
            n += 1
            fired += exit_day is not None
            rule += got
            actual += r.outcome
        rows.append({"stop": x, "lots": n, "fired": fired, "rule": rule, "actual": actual,
                     "difference": rule - actual if n else None})
    return pd.DataFrame(rows, columns=["stop", "lots", "fired", "rule", "actual", "difference"])


# --- 8. The journal -----------------------------------------------------------------------------------------------

def by_reason(lots: pd.DataFrame, journal: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Buys grouped by the reason written in journal.csv, with how each group turned out to date. Also the
    journal's contract numbers that match no buy."""
    if journal.empty or lots.empty:
        return pd.DataFrame(columns=["reason"]), []
    j = lots.merge(journal[["contract_no", "reason"]], on="contract_no", how="inner")
    unmatched = sorted(set(journal["contract_no"]) - set(lots["contract_no"].dropna()))
    rows = [{"reason": reason, **_group(g)} for reason, g in j.groupby("reason")]
    if not rows:
        return pd.DataFrame(columns=["reason"]), unmatched
    return pd.DataFrame(rows).sort_values("cost", ascending=False).reset_index(drop=True), unmatched


# --- 7. Cash left idle ------------------------------------------------------------------------------------------

def cash_idle(balances: pd.DataFrame, cash: pd.DataFrame, tx: pd.DataFrame, dividends: pd.DataFrame,
              hist: pd.DataFrame, tbill: pd.Series | None = None) -> dict | None:
    """The account's return (shares plus the cash at the broker) against the shares' alone (docs/05 §7).

    The window is the latest unbroken run of statements (each opening the previous closing) that starts with a
    portfolio value, so no deposit can be missing. Money in and out of the account: deposits and withdrawals,
    plus what bypasses the broker (dividends and buyouts paid to your bank, rights paid to the company). Cash
    drag = shares-only return − account return: what idle cash cost. With tbill.csv, also the interest the
    average cash balance would have earned in T-bills.
    """
    from .analysis import xirr
    from .extdata import at_or_before
    from .performance import mean_rate

    if balances.empty:
        return None
    b = balances.assign(date=pd.to_datetime(balances["date"]), period_start=pd.to_datetime(balances["period_start"]))
    b = b.sort_values("date").reset_index(drop=True)
    first = len(b) - 1
    while first > 0 and abs(b.at[first, "opening"] - b.at[first - 1, "balance"]) < 0.005 \
            and (b.at[first, "period_start"] - b.at[first - 1, "date"]).days <= 5:
        first -= 1
    gaps = int(first > 0)
    v = hist.dropna(subset=["value"]).set_index("date")["value"] if len(hist) else pd.Series(dtype=float)
    shares_at = lambda d: at_or_before(v, d, 5) if len(v) else None  # noqa: E731
    while first < len(b) and shares_at(b.at[first, "period_start"] - pd.Timedelta(days=1)) is None:
        first += 1  # no price history yet at that statement's start
    if first >= len(b) or shares_at(b.at[len(b) - 1, "date"]) is None:
        return {"statements": int(len(b)), "since": None, "gaps": gaps}
    run = b.iloc[first:]
    start, end = run["period_start"].iloc[0], run["date"].iloc[-1]
    s0, s1 = shares_at(start - pd.Timedelta(days=1)), shares_at(end)
    a0, a1 = s0 + float(run["opening"].iloc[0]), s1 + float(run["balance"].iloc[-1])
    inside = lambda d: start <= pd.Timestamp(d) <= end  # noqa: E731
    acct = [(pd.Timestamp(r.date), -r.amount if r.kind == "DEPOSIT" else r.amount)
            for r in cash.itertuples(index=False) if inside(r.date)]
    acct += [(r.date, r.amount) for r in dividends.itertuples(index=False) if inside(r.date)]
    acct += [(r.trade_date, -r.net_amount if r.side == "BUY" else r.net_amount)
             for r in tx[tx["sources"].isin(NON_MARKET)].itertuples(index=False) if inside(r.trade_date)]
    shares = [(r.trade_date, -r.net_amount if r.side == "BUY" else r.net_amount)
              for r in tx.itertuples(index=False) if inside(r.trade_date)]
    shares += [(r.date, r.amount) for r in dividends.itertuples(index=False) if inside(r.date)]
    days = (end - start).days
    account, alone = xirr([(start, -a0), *acct, (end, a1)]), xirr([(start, -s0), *shares, (end, s1)])
    period = lambda r: (1 + r) ** (days / 365) - 1 if r is not None else None  # noqa: E731
    w = (run["date"] - run["period_start"]).dt.days + 1
    avg_cash = float(((run["opening"] + run["balance"]) / 2 * w).sum() / w.sum())
    ends = [shares_at(d) for d in run["date"]]
    acct_values = [s + c for s, c in zip(ends, run["balance"]) if s is not None]
    forgone = None
    if tbill is not None and len(tbill):
        rates = [mean_rate(tbill, r.period_start, r.date) for r in run.itertuples(index=False)]
        if None not in rates:
            forgone = float(sum(max(0.0, (r.opening + r.balance) / 2) * wd * y / 365
                                for r, wd, y in zip(run.itertuples(index=False), w, rates)))
    annual = days >= 365
    out = {"statements": int(len(b)), "used": int(len(run)), "gaps": gaps, "since": start, "until": end, "days": days,
           "annualized": annual, "account": account if annual else period(account),
           "shares": alone if annual else period(alone), "start_cash": float(run["opening"].iloc[0]),
           "end_cash": float(run["balance"].iloc[-1]), "average_cash": avg_cash,
           "cash_share": avg_cash / (sum(acct_values) / len(acct_values)) if acct_values and sum(acct_values) else None,
           "tbill_forgone": forgone}
    out["drag"] = out["shares"] - out["account"] if None not in (out["shares"], out["account"]) else None
    return out


# --- Everything together ---------------------------------------------------------------------------------------------

def habits(tx: pd.DataFrame, actions: pd.DataFrame | None, price_hist: pd.DataFrame, index_hist: pd.DataFrame,
           today, fee_today: float, rates, pf, hist: pd.DataFrame, growth: pd.Series, fee_columns: pd.DataFrame,
           journal: pd.DataFrame, balances: pd.DataFrame | None = None, cash: pd.DataFrame | None = None,
           tbill: pd.Series | None = None) -> dict:
    """The Habits page. `price_hist` as traded (`adjust.actual_history`); `rates(dates)` the fee rate on each date;
    `pf` the portfolio at today's prices; `hist`/`growth` from `value_history` and `twr_index`; `balances` the
    `cash_balances` table and `cash` the `cash_movements` one."""
    today = pd.Timestamp(today)
    closes = _Closes(price_hist, actions)
    walked = _history(tx, actions)
    _, lot_cash, dps, last = walked
    lots = lot_outcomes(tx, actions, price_hist, index_hist, today, fee_today, closes, walked)
    trips = trips_frame(last.trips if last else [])
    market = lots[lots["market"]] if len(lots) else lots
    sw = switches(tx, closes, today)
    reasons, unmatched = by_reason(lots, journal)
    return {
        "disposition": disposition(tx, actions, price_hist),
        "round_trips": round_trips(trips), "trips": trips[trips["market"] & trips["matched"]] if len(trips) else trips,
        "fees_by_year": fees_by_year(tx, fee_columns, hist, growth), "fees_by_stock": fees_by_stock(tx, pf),
        "frozen": frozen_years(tx, actions, price_hist, growth, dps, today, closes),
        "switches": sw,
        "switch_summary": [{"months": k, "n": int(sw[f"gain_{k}"].notna().sum()),
                            "gain": float(sw[f"gain_{k}"].mean()) if sw[f"gain_{k}"].notna().any() else None,
                            "better": float((sw[f"gain_{k}"].dropna() > 0).mean()) if sw[f"gain_{k}"].notna().any() else None}
                           for k in HORIZONS],
        "never_sold": never_sold(tx, closes, today, fee_today),
        "buy_timing": buy_timing(lots, closes, index_hist),
        "adding": {k: _group(market[market["kind"] == k]) for k in ("new", "down", "up")},
        "trailing_stop": trailing_stop(lots, lot_cash, dps, closes, today, rates),
        "reasons": reasons, "journal_unmatched": unmatched, "journal_entries": int(len(journal)),
        "cash_idle": cash_idle(balances, cash, tx, pf.dividends, hist, tbill) if balances is not None and cash is not None else None,
        "lots": lots,
    }
