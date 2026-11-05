"""How well, not just how much: time-weighted return, risk, and fair comparisons with an index (docs/04).

Risk is always measured on a *return* series (the time-weighted growth of your picks, or a stock's adjusted
closes), never on market value: buying more shares makes value jump, and that isn't a gain.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .analysis import xirr

FALLBACK_DAYS = 240  # CSE trading days in a year (Poya days and holidays close it); only used without index history


def trading_days_per_year(index_hist: pd.DataFrame, end=None) -> float:
    """N for annualizing: index days in the 365 before `end`. With less than a year of index history, its
    pace is scaled up; with almost none, FALLBACK_DAYS. US formulas assume 252, which overstates CSE
    volatility by about 2.5%."""
    if index_hist is None or len(index_hist) < 20:
        return float(FALLBACK_DAYS)
    d = pd.to_datetime(index_hist["date"]).drop_duplicates().sort_values()
    end = pd.Timestamp(end) if end is not None else d.iloc[-1]
    year = d[(d > end - pd.Timedelta(days=365)) & (d <= end)]
    span = (year.iloc[-1] - year.iloc[0]).days if len(year) > 1 else 0
    if span >= 350:
        return float(len(year))
    return float(len(year) * 365 / span) if span >= 60 else float(FALLBACK_DAYS)


def returns(levels: pd.Series) -> pd.Series:
    """Simple returns between consecutive points of a level series (a growth index, closes)."""
    s = levels.dropna().sort_index()
    return (s / s.shift(1) - 1).dropna()


def volatility(r: pd.Series, n: float) -> float | None:
    return float(r.std(ddof=1) * math.sqrt(n)) if len(r) > 2 else None


def downside_deviation(r: pd.Series, n: float) -> float | None:
    return float(math.sqrt((np.minimum(r, 0) ** 2).mean()) * math.sqrt(n)) if len(r) > 2 else None


def max_drawdown(growth: pd.Series) -> dict | None:
    """Worst fall from a high: depth, peak, trough, and the day it got back above the peak (None if not yet)."""
    g = growth.dropna().sort_index()
    if len(g) < 2:
        return None
    dd = g / g.cummax() - 1
    trough = dd.idxmin()
    peak = g[:trough].idxmax()
    after = g[g.index > trough]
    back = after[after >= g[peak]]
    rec = back.index[0] if len(back) else None
    return {"depth": float(dd.min()), "peak": peak, "trough": trough, "recovered": rec,
            "days": int(((rec if rec is not None else g.index[-1]) - peak).days),
            "ulcer": float(math.sqrt((dd ** 2).mean()))}


def _aligned(a: pd.Series, b: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"a": a, "b": b}).dropna()


def regression(r: pd.Series, m: pd.Series, n: float) -> dict | None:
    """Beta, alpha (annualized) and R² of daily returns `r` on market returns `m` (same days only)."""
    df = _aligned(r, m)
    if len(df) < 20 or df["b"].var() == 0:
        return None
    beta = float(np.cov(df["a"], df["b"], ddof=1)[0, 1] / df["b"].var(ddof=1))
    alpha = float(df["a"].mean() - beta * df["b"].mean())
    corr = float(np.corrcoef(df["a"], df["b"])[0, 1])
    te = float((df["a"] - df["b"]).std(ddof=1) * math.sqrt(n))
    return {"beta": beta, "alpha": (1 + alpha) ** n - 1, "r2": corr ** 2, "days": len(df), "tracking_error": te}


def dimson_beta(stock: pd.Series, market: pd.Series, lags: int = 1) -> float | None:
    """Beta for a thinly traded stock (Dimson 1979): slopes on lagged, same-day and leading market returns, summed.

    A stale close makes a stock react to the market a day or more late, so an ordinary regression sees less
    co-movement than there is and its beta comes out too close to zero (Scholes & Williams 1977).
    """
    df = pd.DataFrame({"s": stock, **{f"m{k}": market.shift(k) for k in range(-lags, lags + 1)}}).dropna()
    if len(df) < 20 + 2 * lags:
        return None
    X = np.column_stack([np.ones(len(df)), df.drop(columns="s").to_numpy()])
    coef, *_ = np.linalg.lstsq(X, df["s"].to_numpy(), rcond=None)
    return float(coef[1:].sum())


def _level_on(level: pd.Series, d) -> float | None:
    """Index level on or before `d` (None before the series starts)."""
    s = level[: pd.Timestamp(d)] if len(level) else level
    return float(s.iloc[-1]) if len(s) else None


def ks_pme(flows: list[tuple], final_value: float, end, level: pd.Series) -> float | None:
    """Kaplan–Schoar PME: money out (and what's left) over money in, every flow grown to `end` at the index.

    Above 1 beat the index. Unlike replaying flows into index units, it can't go negative.
    """
    at_end = _level_on(level, end)
    grow = [(a, _level_on(level, d)) for d, a in flows]
    if at_end is None or any(lv is None for _, lv in grow):
        return None
    out = sum(a * at_end / lv for a, lv in grow if a > 0) + final_value
    put_in = sum(-a * at_end / lv for a, lv in grow if a < 0)
    return out / put_in if put_in else None


def direct_alpha(flows: list[tuple], final_value: float, end, level: pd.Series) -> float | None:
    """Direct alpha (Gredil, Griffiths & Stucke 2014): every flow grown to `end` at the index, then XIRR.

    The result is your yearly return over the index.
    """
    at_end = _level_on(level, end)
    grow = [(d, a, _level_on(level, d)) for d, a in flows]
    if at_end is None or any(lv is None for _, _, lv in grow):
        return None
    return xirr([(d, a * at_end / lv) for d, a, lv in grow] + [(pd.Timestamp(end), final_value)])


def level_series(index_hist: pd.DataFrame) -> pd.Series:
    """index_hist (date, value) as a sorted Series of levels."""
    if index_hist is None or index_hist.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = pd.Series(index_hist["value"].to_numpy(float), index=pd.to_datetime(index_hist["date"])).sort_index()
    return s[~s.index.duplicated(keep="last")]


def compare_with_index(flows: list[tuple], final_value: float, end, level: pd.Series, value_on) -> dict | None:
    """KS-PME and direct alpha against an index, from the first day it has a level if that's after the first
    flow: earlier flows are then replaced by the value held that day (`value_on(day)`, None if unknown)."""
    if level.empty or not flows:
        return None
    start = max(level.index[0], min(d for d, _ in flows))
    if min(d for d, _ in flows) < start:
        v0 = value_on(start)
        if v0 is None:
            return None
        flows = [(start, -v0)] + [(d, a) for d, a in flows if d > start]
    pme = ks_pme(flows, final_value, end, level)
    alpha = direct_alpha(flows, final_value, end, level)
    if pme is None and alpha is None:
        return None
    return {"since": start, "ks_pme": pme, "direct_alpha": alpha,
            "index_return": float(_level_on(level, end) / _level_on(level, start) - 1)}
