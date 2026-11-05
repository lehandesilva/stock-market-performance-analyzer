"""Price series that stay continuous across splits and bonus issues (docs/06 §5).

The ledger gets share counts right through splits; price *series* used for returns, volatility, beta and
after-sale comparisons must be continuous across them too. It isn't known whether the CSE's own history is
adjusted for splits, so each split's ex-date is checked in the stored closes themselves: an unadjusted series
drops to about 1/k that day, an adjusted one doesn't.

- `actual_closes`: the prices that were really paid on each day (undoing any adjustment the CSE made), which is
  what your trade prices and average cost are on.
- `adjusted_closes`: every close on today's share basis (CRSP-style factors), for returns.
"""

from __future__ import annotations

import math

import pandas as pd

from .analysis import STALE_DAYS, _split_hint


def share_events(actions: pd.DataFrame | None, symbol: str) -> list[tuple[pd.Timestamp, float]]:
    """(ex-date, k): each share became k shares. SPLIT new:old → new/old; BONUS new:held → (held+new)/held.

    A BONUS entered only as a quantity received has no ratio, so it can't be turned into a price factor.
    """
    if actions is None or actions.empty:
        return []
    a = actions[(actions["symbol"] == symbol) & actions["action"].isin(["SPLIT", "BONUS"]) & actions["ratio_new"].notna()]
    out = []
    for r in a.itertuples(index=False):
        k = r.ratio_new / r.ratio_old if r.action == "SPLIT" else (r.ratio_old + r.ratio_new) / r.ratio_old
        if k > 0 and abs(k - 1) > 1e-9:
            out.append((pd.Timestamp(r.date), float(k)))
    return sorted(out)


def basis(closes: pd.Series, when: pd.Timestamp, k: float) -> str:
    """'unadjusted' if the stored closes jump by about 1/k at the ex-date, 'adjusted' if not, 'unknown' without a
    close on both sides (within STALE_DAYS)."""
    before = closes[closes.index < when]
    after = closes[closes.index >= when]
    if before.empty or after.empty or (when - before.index[-1]).days > STALE_DAYS \
            or (after.index[0] - when).days > STALE_DAYS:
        return "unknown"
    r = before.iloc[-1] / after.iloc[0]
    return "unadjusted" if abs(math.log(r) - math.log(k)) < abs(math.log(r)) else "adjusted"


def _series(price_hist: pd.DataFrame, symbol: str) -> pd.Series:
    p = price_hist[price_hist["symbol"] == symbol]
    return pd.Series(p["price"].to_numpy(float), index=pd.DatetimeIndex(pd.to_datetime(p["date"]))).sort_index()


def actual_closes(price_hist: pd.DataFrame, actions: pd.DataFrame | None, symbol: str) -> tuple[pd.Series, list[dict]]:
    """The closes as traded, and how each split was found in the stored history."""
    s = _series(price_hist, symbol)
    found = []
    for when, k in share_events(actions, symbol):
        b = basis(s, when, k)
        found.append({"date": when, "factor": k, "history": b})
        if b == "adjusted":  # the CSE divided earlier closes by k: put the prices actually paid back
            s = s.where(s.index >= when, s * k)
    return s, found


def actual_history(price_hist: pd.DataFrame, actions: pd.DataFrame | None) -> pd.DataFrame:
    """price_hist (symbol, date, price) with every split-affected symbol's closes put back to what was paid, so
    ledger share counts on any day can be multiplied by that day's close."""
    affected = {sym for sym in price_hist["symbol"].unique() if share_events(actions, sym)} if len(price_hist) else set()
    if not affected:
        return price_hist
    keep = price_hist[~price_hist["symbol"].isin(affected)]
    fixed = [pd.DataFrame({"symbol": sym, "date": c.index, "price": c.to_numpy()})
             for sym in sorted(affected) for c in [actual_closes(price_hist, actions, sym)[0]]]
    return pd.concat([keep[["symbol", "date", "price"]], *fixed], ignore_index=True)


def adjusted_closes(price_hist: pd.DataFrame, actions: pd.DataFrame | None, symbol: str) -> pd.Series:
    """Every close on today's share basis: a close before an ex-date is divided by that event's k."""
    s, _ = actual_closes(price_hist, actions, symbol)
    for when, k in share_events(actions, symbol):
        s = s.where(s.index >= when, s / k)
    return s


def total_return_closes(price_hist: pd.DataFrame, actions: pd.DataFrame | None, symbol: str,
                        per_share: pd.DataFrame | None) -> pd.Series:
    """`adjusted_closes` with the dividends put back (CRSP-style), for returns and risk: every close before an
    ex-date is multiplied by 1 − D ÷ P, D being what the dividend paid a share (analysis.dividends_per_share:
    after withholding tax) and P the close before the ex-date. A drop on an ex-date is then no loss. Only
    dividends in corporate_actions.csv count, and one without a close in the STALE_DAYS before it is left out."""
    s = adjusted_closes(price_hist, actions, symbol)
    if per_share is None or per_share.empty or s.empty:
        return s
    actual, _ = actual_closes(price_hist, actions, symbol)
    mine = per_share[(per_share["symbol"] == symbol) & per_share["per_share"].notna()]
    for r in mine.itertuples(index=False):
        when = pd.Timestamp(r.date)
        before = actual[actual.index < when]
        if before.empty or (when - before.index[-1]).days > STALE_DAYS:
            continue
        k = 1 - r.per_share / float(before.iloc[-1])  # both on that day's share basis
        if 0 < k < 1:
            s = s.where(s.index >= when, s * k)
    return s


def multiplier(actions: pd.DataFrame | None, symbol: str, start, end) -> float:
    """Shares one share held on `start` had become by `end` (splits and bonus issues in between)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    return math.prod(k for when, k in share_events(actions, symbol) if start < when <= end)


def unrecorded_splits(price_hist: pd.DataFrame, actions: pd.DataFrame | None, symbol: str) -> list[str]:
    """Day-to-day drops in the stored closes to about 1/2 … 1/10 with no split or bonus recorded near them."""
    s = _series(price_hist, symbol)
    known = [when for when, _ in share_events(actions, symbol)]
    out = []
    for (d0, p0), (d1, p1) in zip(s.items(), list(s.items())[1:]):
        if (d1 - d0).days > 7 or any(abs((d1 - w).days) <= STALE_DAYS for w in known):
            continue  # a long gap can hide a real fall; a recorded split explains its own drop
        out += _split_hint(symbol, d0, p0, d1, p1)
    return out
