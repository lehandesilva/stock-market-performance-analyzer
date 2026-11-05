"""Cash at the broker (docs/05 §7): the balance on each day, and what leaving it idle cost.

Each statement gives the cash at the start and end of its period, and its rows were checked against its own
running balance when it was parsed. Between statements, and from the last one to today, the balance is worked
out from the trades and receipts in your documents. Where that doesn't arrive at the next statement's
balance, something is missing (a statement, a deposit, a charge): the balance is taken from the statement
again from there, and the account's return only starts after the last such break.

Balances are credit positive: money held for you. Market trades move cash at the broker; dividends, rights
subscriptions (paid to the company) and buyouts are taken to go straight to and from your bank.
"""

from __future__ import annotations

import pandas as pd

from .analysis import NON_MARKET, xirr

TOLERANCE = 0.005  # half a cent


def flows_at_broker(tx: pd.DataFrame, movements: pd.DataFrame) -> pd.Series:
    """Cash in (+) and out (−) of the broker account each day: sales and deposits in, buys and withdrawals out."""
    parts = []
    market = tx[~tx["sources"].isin(NON_MARKET)] if len(tx) else tx
    if len(market):
        parts.append(pd.Series(market["net_amount"].where(market["side"] == "SELL", -market["net_amount"]).to_numpy(float),
                               index=pd.DatetimeIndex(market["trade_date"])))
    if len(movements):
        amount = movements["amount"].where(movements["kind"] == "DEPOSIT", -movements["amount"])
        parts.append(pd.Series(amount.to_numpy(float), index=pd.DatetimeIndex(pd.to_datetime(movements["date"]))))
    if not parts:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return pd.concat(parts).groupby(level=0).sum().sort_index()


def cash_history(balances: pd.DataFrame, flows: pd.Series, today) -> dict | None:
    """The cash at the end of each day from the first statement to `today`, and the breaks: where the trades and
    receipts since a statement don't arrive at the next statement's balance. None without a statement.

    `balances`: period_start, period_end, opening, closing (the cash_balances table). `anchor` is the last day
    the balance is known from a statement with nothing missing after it: the account return starts there.
    `known` is False on the days before a break back to the statement before it, where the worked-out balance
    is wrong somewhere (`series` keeps it, to show how far out it got).
    """
    if balances.empty:
        return None
    b = balances.assign(period_start=pd.to_datetime(balances["period_start"]),
                        period_end=pd.to_datetime(balances["period_end"])).sort_values("period_start")
    end = max(pd.Timestamp(today), b["period_end"].max())
    days = pd.date_range(b["period_start"].iloc[0], end, freq="D")
    opens, closes = dict(zip(b["period_start"], b["opening"])), dict(zip(b["period_end"], b["closing"]))
    f = flows.groupby(level=0).sum().reindex(days, fill_value=0.0) if len(flows) else pd.Series(0.0, index=days)
    bal, out, breaks = None, [], []
    known = pd.Series(True, index=days)
    anchor = (days[0] - pd.Timedelta(days=1), float(b["opening"].iloc[0]))  # the end of the day before the first
    since = days[0]  # the last day a statement gave the balance
    for d in days:
        if d in opens:  # the statement's balance at the start of the day
            if bal is not None and abs(bal - opens[d]) > TOLERANCE:
                breaks.append({"date": d, "at": "opening", "worked_out": bal, "statement": float(opens[d]), "since": since})
                anchor = (d - pd.Timedelta(days=1), float(opens[d]))
                known[(known.index > since) & (known.index < d)] = False
            bal, since = float(opens[d]), d
        bal = round(bal + float(f[d]), 2)  # amounts are in cents: keep float noise out
        if d in closes:
            if abs(bal - closes[d]) > TOLERANCE:
                breaks.append({"date": d, "at": "closing", "worked_out": bal, "statement": float(closes[d]), "since": since})
                known[(known.index > since) & (known.index < d)] = False
                bal = float(closes[d])
                anchor = (d, bal)
            since = d
        out.append(bal)
    series = pd.Series(out, index=days)
    last = b.iloc[-1]
    return {"series": series, "known": known, "breaks": breaks, "statements": int(len(b)), "first": b["period_start"].iloc[0],
            "last": last["period_end"], "confirmed": float(last["closing"]), "now": float(series[pd.Timestamp(today)])
            if pd.Timestamp(today) in series.index else float(series.iloc[-1]),
            "anchor": anchor[0], "anchor_cash": anchor[1]}


def account_return(cash: dict, shares_then: float | None, account_flows: list[tuple], share_flows: list[tuple],
                   value_now: float, today, values: pd.Series | None = None, tbill: pd.Series | None = None) -> dict:
    """The money-weighted return of the whole account, shares plus cash at the broker, from `cash["anchor"]` on;
    and the same for the shares alone. The gap is cash drag: what leaving cash idle cost (docs/05 §7).

    `shares_then`: the shares' value at the end of the anchor day (None if a holding had no close then).
    `account_flows`: money you put in (−) and took out (+): deposits, withdrawals, dividends, rights subscriptions
    and buyouts. `share_flows`: as analysis.cash_flows. `values`: the shares' value each day (for the cash's share
    of the account); `tbill`: 364-day yields, for the interest the idle cash could have earned.
    """
    a, today = cash["anchor"], pd.Timestamp(today)
    s = cash["series"][(cash["series"].index > a) & (cash["series"].index <= today)]
    out = {"start": a, "end": today, "days": int((today - a).days), "cash_then": cash["anchor_cash"],
           "shares_then": shares_then, "average_cash": float(s.mean()) if len(s) else None, "cash_share": None,
           "tbill_forgone": None, "account": None, "shares": None, "drag": None}
    if values is not None and len(values):
        v = values[(values.index > a) & (values.index <= today)].dropna()
        both = pd.concat([s.reindex(v.index), v], axis=1, keys=["cash", "shares"]).dropna()
        total = float((both["cash"] + both["shares"]).mean()) if len(both) else 0.0
        out["cash_share"] = float(both["cash"].clip(lower=0).mean()) / total if total > 0 else None
    if tbill is not None and len(tbill) and len(s):
        y = tbill.reindex(tbill.index.union(s.index)).ffill().reindex(s.index)
        if y.notna().all():
            out["tbill_forgone"] = float((s.clip(lower=0) * y / 365).sum())
    out["period_account"] = out["period_shares"] = None
    if shares_then is None:
        return out
    inside = lambda flows: [(d, x) for d, x in flows if a < d <= today]  # noqa: E731
    then = cash["anchor_cash"] + shares_then
    out["account"] = xirr([(a, -then), *inside(account_flows), (today, cash["now"] + value_now)])
    out["shares"] = xirr([(a, -shares_then), *inside(share_flows), (today, value_now)])
    if None not in (out["account"], out["shares"]):
        out["drag"] = out["shares"] - out["account"]
    period = lambda r: (1 + r) ** (out["days"] / 365) - 1 if r is not None else None  # noqa: E731  under a year
    out["period_account"], out["period_shares"] = period(out["account"]), period(out["shares"])
    return out
