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


def on_calendar(levels: pd.Series, days, limit: int = 10) -> pd.Series:
    """`levels` on each of `days`: the last level on or before it, at most `limit` days old."""
    if levels.empty:
        return pd.Series(float("nan"), index=pd.DatetimeIndex(days))
    out = levels.reindex(levels.index.union(pd.DatetimeIndex(days))).ffill()
    age = pd.Series(levels.index, index=levels.index).reindex(out.index).ffill()
    out[(out.index - age).dt.days > limit] = float("nan")
    return out.reindex(pd.DatetimeIndex(days))


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


# --- Time-weighted return (docs/04 §1) -------------------------------------------------------------------------

def twr_index(hist: pd.DataFrame, flows: list[tuple]) -> pd.Series:
    """Growth of 1 rupee kept in your share picks, chain-linked daily. `flows` as from analysis.cash_flows.

    r_t = (V_t + money out_t) / (V_t-1 + money in_t) − 1. Buys count at the start of their day (bought at the
    trade price, so the move to the close is that day's return); sales and dividends at the end. A flow on a
    day without a valuation joins the next valued day, whose return then covers the whole gap.
    """
    v = hist.dropna(subset=["value"]).set_index("date")["value"].sort_index()
    if v.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    put_in = pd.Series(0.0, index=v.index)
    taken_out = pd.Series(0.0, index=v.index)
    for when, amt in flows:
        later = v.index[v.index >= when]
        if len(later) and when > v.index[0] or (len(later) and when == v.index[0] and _fresh_start(hist)):
            (put_in if amt < 0 else taken_out)[later[0]] += abs(amt)
    base = v.shift(1).fillna(0) + put_in
    r = ((v + taken_out) / base - 1)[base > 0]
    growth = (1 + r).cumprod()
    if not _fresh_start(hist):  # held going into the first day: growth starts there at 1
        growth = pd.concat([pd.Series([1.0], index=[v.index[0]]), growth[growth.index > v.index[0]]])
    return growth


def _fresh_start(hist: pd.DataFrame) -> bool:
    """Nothing was held going into the first valued day (its buys are then that day's money in)."""
    h = hist.dropna(subset=["value"])
    return bool(h["fresh"].iloc[0]) if "fresh" in h and len(h) else False


def annualize(growth: float, days: int) -> float | None:
    """A growth factor over `days` as a yearly rate. Under a year this exaggerates, so callers show the plain one."""
    return growth ** (365 / days) - 1 if days > 0 and growth > 0 else None


def modified_dietz(v_start: float, v_end: float, flows: list[tuple], start, end) -> float | None:
    """R = (V_end − V_start − F) / (V_start + Σ wᵢ Fᵢ), wᵢ = days left in the period after flow i ÷ days in it.

    Flows are money into the portfolio (buys positive here); for a period with gaps or big flows."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    days = (end - start).days
    if days <= 0:
        return None
    f = sum(a for _, a in flows)
    denom = v_start + sum(a * (end - pd.Timestamp(d)).days / days for d, a in flows)
    return (v_end - v_start - f) / denom if denom > 0 else None


def monthly_returns(hist: pd.DataFrame, flows: list[tuple]) -> pd.DataFrame:
    """month, return: Modified Dietz for each calendar month between the last valued days of consecutive months."""
    v = hist.dropna(subset=["value"]).set_index("date")["value"].sort_index()
    if len(v) < 2:
        return pd.DataFrame(columns=["month", "return"])
    ends = v.groupby(v.index.to_period("M")).tail(1)
    rows = []
    for (d0, v0), (d1, v1) in zip(ends.items(), list(ends.items())[1:]):
        inside = [(d, -a) for d, a in flows if d0 < d <= d1]  # money in positive
        rows.append({"month": d1.to_period("M").to_timestamp(), "return": modified_dietz(v0, v1, inside, d0, d1)})
    return pd.DataFrame(rows, columns=["month", "return"])


def risk_table(growth: pd.Series, market: pd.Series, n: float, rf: float | None, value: float) -> dict:
    """Risk measured on the time-weighted growth series (docs/04 §3), against market levels on the same days."""
    r = returns(growth)
    days = (growth.index[-1] - growth.index[0]).days if len(growth) > 1 else 0
    total = float(growth.iloc[-1] / growth.iloc[0]) if len(growth) > 1 else None
    ann = annualize(total, days) if total else None
    dd = max_drawdown(growth)
    out = {"days": days, "observations": int(len(r)), "trading_days_per_year": n, "growth": total,
           "return": ann if days >= 365 else (total - 1 if total else None), "annualized": days >= 365,
           "volatility": volatility(r, n), "downside": downside_deviation(r, n), "drawdown": dd, "risk_free": rf}
    vol, down = out["volatility"], out["downside"]
    out["sharpe"] = (ann - rf) / vol if None not in (ann, rf, vol) and vol else None
    out["sortino"] = (ann - rf) / down if None not in (ann, rf, down) and down else None
    out["calmar"] = ann / abs(dd["depth"]) if ann is not None and dd and dd["depth"] < 0 else None
    on_days = on_calendar(market, growth.index)
    m = returns(on_days) if not market.empty else pd.Series(dtype=float)
    reg = regression(r, m, n) if len(m) else None
    out["market"] = reg
    if reg:
        mg = on_days.dropna()
        m_ann = annualize(float(mg.iloc[-1] / mg.iloc[0]), (mg.index[-1] - mg.index[0]).days) if len(mg) > 1 else None
        out["market_return"] = m_ann
        out["information_ratio"] = (ann - m_ann) / reg["tracking_error"] if None not in (ann, m_ann) and reg["tracking_error"] else None
    else:
        out["market_return"] = out["information_ratio"] = None
    out["var"] = value_at_risk(r, value)
    return out


def value_at_risk(r: pd.Series, value: float, level: float = 0.05) -> dict | None:
    """Historical one-day VaR and expected shortfall: the 5th percentile of daily returns, and the average of
    the worst 5%, applied to today's value. Daily returns have fat tails, so no normal curve is assumed."""
    if len(r) < 100:
        return None
    q = float(r.quantile(level))
    tail = r[r <= q]
    return {"var_return": q, "es_return": float(tail.mean()), "var": q * value, "es": float(tail.mean()) * value,
            "observations": int(len(r)), "worst": float(r.min()), "worst_day": r.idxmin()}


def bootstrap(r: pd.Series, n: float, block: int = 10, trials: int = 2000, seed: int = 0) -> dict | None:
    """A range of one-year outcomes from resampling runs of `block` real daily returns (docs/04 §8). Blocks keep
    calm and stormy stretches together. A range under the assumption that the next year looks like a mix of
    past ones, never a forecast."""
    if len(r) < 250:
        return None
    rng = np.random.default_rng(seed)
    a = r.to_numpy()
    need, starts = int(round(n)), len(a) - block + 1
    k = -(-need // block)
    idx = rng.integers(0, starts, size=(trials, k))[:, :, None] + np.arange(block)
    year = np.prod(1 + a[idx].reshape(trials, -1)[:, :need], axis=1) - 1
    p5, p50, p95 = np.percentile(year, [5, 50, 95])
    return {"p5": float(p5), "p50": float(p50), "p95": float(p95), "loss_chance": float((year < 0).mean()),
            "block": block, "trials": trials, "days": int(len(a))}


# --- Benchmarks you supply: T-bills (docs/04 §3, §5) ---------------------------------------------------------

def tbill_index(yields: pd.Series, days) -> pd.Series:
    """A T-bill account as an index: grows each day at the latest 364-day yield (simple interest per day)."""
    days = pd.DatetimeIndex(days)
    if yields.empty or days.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    cal = pd.date_range(min(days[0], yields.index[0]), days[-1], freq="D")
    y = yields.reindex(yields.index.union(cal)).ffill().reindex(cal)
    level = (1 + y.fillna(0) / 365).cumprod()
    level = level[level.index >= yields.index[0]]
    return level.reindex(days.intersection(level.index))


def mean_rate(yields: pd.Series, start, end) -> float | None:
    """The average yield in force over [start, end], weighting each by how long it applied."""
    if yields.empty:
        return None
    cal = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    y = yields.reindex(yields.index.union(cal)).ffill().reindex(cal).dropna()
    return float(y.mean()) if len(y) else None


# --- Real and dollar returns (docs/04 §6) -----------------------------------------------------------------------

def converted_xirr(flows: list[tuple], final_value: float, end, factor) -> float | None:
    """XIRR with every flow and the final value multiplied by `factor(date)` (None when it isn't known)."""
    out = []
    for d, a in [*flows, (pd.Timestamp(end), final_value)]:
        k = factor(d)
        if k is None:
            return None
        out.append((d, a * k))
    return xirr(out)


# --- Concentration and where the risk comes from (docs/04 §4, docs/02) --------------------------------------------

def concentration(weights: pd.Series) -> dict | None:
    """HHI = Σ wᵢ²; effective number of holdings = 1 / HHI; top-1 and top-3 weights."""
    w = weights[weights > 0]
    if w.empty:
        return None
    w = (w / w.sum()).sort_values(ascending=False)
    hhi = float((w ** 2).sum())
    return {"hhi": hhi, "effective": 1 / hhi, "count": int(len(w)), "top1": float(w.iloc[0]),
            "top3": float(w.iloc[:3].sum())}


def risk_contributions(rets: pd.DataFrame, weights: pd.Series, n: float) -> dict | None:
    """Each holding's share of portfolio volatility, wᵢ × Cov(rᵢ, r_p) ÷ σ_p (they add up to σ_p), with today's
    weights on past daily returns; the average correlation between pairs; and the diversification ratio
    Σ wᵢσᵢ ÷ σ_p (above 1: diversification is lowering the risk)."""
    cols = [c for c in rets.columns if c in weights.index and weights[c] > 0]
    r = rets[cols].dropna()
    if len(cols) == 0 or len(r) < 30:
        return None
    w = weights[cols] / weights[cols].sum()
    cov = r.cov() * n
    var_p = float(w @ cov @ w)
    if var_p <= 0:
        return None
    sigma_p = var_p ** 0.5
    contrib = (cov @ w) * w / sigma_p
    sig = pd.Series(np.sqrt(np.diag(cov)), index=cols)
    corr = r.corr().to_numpy()
    pairs = corr[np.triu_indices(len(cols), 1)] if len(cols) > 1 else np.array([])
    rows = [{"symbol": c, "weight": float(w[c]), "volatility": float(sig[c]), "contribution": float(contrib[c]),
             "risk_share": float(contrib[c] / sigma_p)} for c in cols]
    return {"volatility": sigma_p, "average_correlation": float(pairs.mean()) if len(pairs) else None,
            "diversification_ratio": float((w * sig).sum() / sigma_p), "days": int(len(r)), "holdings": rows}


# --- Attribution: where the return came from (docs/04 §7) -----------------------------------------------------------

def attribution(start_values: dict, end_values: dict, flows: dict[str, list[tuple]], start, end) -> pd.DataFrame | None:
    """Each stock's contribution to the portfolio's Modified Dietz return over [start, end]: its gain over the
    period (end value − start value − money in, dividends counting as money out) ÷ the portfolio's MD
    denominator. The contributions add up exactly to the portfolio's return.

    `start_values`/`end_values`: symbol -> value (None if unpriced); `flows`: symbol -> [(date, money in)].
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    days = (end - start).days
    syms = sorted(set(start_values) | set(end_values) | set(flows))
    if days <= 0 or not syms:
        return None
    rows = []
    for s in syms:
        v0, v1 = start_values.get(s, 0.0), end_values.get(s, 0.0)
        if v0 is None or v1 is None:
            return None  # a holding without a price at one end: can't say
        f = flows.get(s, [])
        rows.append({"symbol": s, "start": v0, "end": v1, "money_in": sum(a for _, a in f),
                     "weighted_in": sum(a * (end - d).days / days for d, a in f)})
    df = pd.DataFrame(rows)
    denom = float(df["start"].sum() + df["weighted_in"].sum())
    if denom <= 0:
        return None
    df["gain"] = df["end"] - df["start"] - df["money_in"]
    df["contribution"] = df["gain"] / denom
    df["denominator"] = df["start"] + df["weighted_in"]
    df["return"] = (df["gain"] / df["denominator"]).where(df["denominator"] > 0)
    return df.sort_values("contribution", ascending=False).reset_index(drop=True)


def brinson(attr: pd.DataFrame, sectors: dict[str, str], sector_return: dict[str, float | None],
            index_weights: dict[str, float], market_return: float) -> pd.DataFrame | None:
    """Brinson–Fachler by sector: allocation = (your weight − the index's) × (sector return − index return),
    did you overweight sectors that did well; selection = your weight × (your return in it − the sector's),
    did you pick well within it. Your weights and returns are Modified Dietz, like `attribution`."""
    a = attr.assign(sector=attr["symbol"].map(sectors).fillna("Unmapped"))
    total = float(a["denominator"].sum())
    if total <= 0:
        return None
    rows = []
    for sec, g in a.groupby("sector"):
        w = float(g["denominator"].sum() / total)
        r = float(g["gain"].sum() / g["denominator"].sum()) if g["denominator"].sum() > 0 else None
        rb, wb = sector_return.get(sec), index_weights.get(sec, 0.0)
        rows.append({"sector": sec, "weight": w, "return": r, "index_weight": wb, "index_return": rb,
                     "allocation": (w - wb) * (rb - market_return) if rb is not None else None,
                     "selection": w * (r - rb) if None not in (r, rb) else None})
    return pd.DataFrame(rows).sort_values("weight", ascending=False).reset_index(drop=True)
