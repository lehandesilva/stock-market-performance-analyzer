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
