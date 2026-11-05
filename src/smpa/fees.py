"""The fee rate a sale would pay on a given day, worked out from your own trades.

Every trade pays a stack of percentage fees (brokerage, STL, CSE, SEC, CDS, clearing): about 1.12% each way
until mid-2025 and 1.132% since the clearing fee was added. The rates change, so they are never hard-coded:
the rate on a day is the median fees ÷ gross of your last few broker trades on or before it. Small trades pay
slightly more because each fee is rounded to the cent, which is why a median beats any single trade.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_RATE = 0.01132  # 2026 rates, used only when there are no broker trades to measure
SAMPLE = 5  # trades in the median
SPAN_DAYS = 180  # ... all within this many days of the latest of them, so old rates don't mix in
NOT_BROKER = {"manual", "rights issue", "delisting"}  # fees typed in by hand, or none paid


def trade_rates(tx: pd.DataFrame) -> pd.DataFrame:
    """date, rate (fees ÷ gross) of every broker trade, oldest first. Statement-only trades count: their total
    fee is exact even though its breakdown isn't known."""
    if tx.empty:
        return pd.DataFrame(columns=["date", "rate"])
    t = tx[~tx["sources"].isin(NOT_BROKER) & (tx["gross_amount"] > 0)]
    out = pd.DataFrame({"date": pd.to_datetime(t["trade_date"]), "rate": t["fees"] / t["gross_amount"]})
    return out.sort_values("date", kind="stable").reset_index(drop=True)


def fee_rate(tx: pd.DataFrame, day, fallback: float = DEFAULT_RATE, rates: pd.DataFrame | None = None) -> dict:
    """{rate, trades, from, to}: the rate in force on `day`. Before your first trade, your first few trades
    are used; with no broker trades at all, `fallback` (`trades` is then 0)."""
    r = trade_rates(tx) if rates is None else rates
    if r.empty:
        return {"rate": fallback, "trades": 0, "from": None, "to": None}
    day = pd.Timestamp(day)
    before = r[r["date"] <= day]
    if len(before):
        pick = before.tail(SAMPLE)
        pick = pick[pick["date"] >= pick["date"].iloc[-1] - pd.Timedelta(days=SPAN_DAYS)]
    else:
        pick = r.head(SAMPLE)
        pick = pick[pick["date"] <= pick["date"].iloc[0] + pd.Timedelta(days=SPAN_DAYS)]
    return {"rate": float(pick["rate"].median()), "trades": len(pick),
            "from": pick["date"].iloc[0], "to": pick["date"].iloc[-1]}


def fee_rates(tx: pd.DataFrame, days, fallback: float = DEFAULT_RATE) -> pd.Series:
    """`fee_rate` on each of `days` (a sorted sequence of dates)."""
    r = trade_rates(tx)
    out, cache = {}, {}
    for d in days:
        d = pd.Timestamp(d)
        k = int(r["date"].searchsorted(d, side="right"))  # trades on or before d
        if k not in cache:
            cache[k] = fee_rate(tx, d, fallback, r)["rate"]
        out[d] = cache[k]
    return pd.Series(out, dtype=float)


def liquidation(value, rate: float):
    """What selling at `value` would bring in after fees."""
    return value * (1 - rate)


def break_even(avg_cost, rate: float):
    """The price at which selling gets back what the shares cost, fees on both sides included."""
    return avg_cost / (1 - rate)
