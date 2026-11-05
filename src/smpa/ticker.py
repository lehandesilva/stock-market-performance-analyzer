"""One stock: how it has done for you, and why (docs/02-per-ticker-page.md).

`analysis._Ledger` keeps each symbol apart (only the portfolio-wide `invested` total is shared), so running
the same engine on one symbol's trades and corporate actions gives exact per-stock numbers: every symbol's
lifetime P&L adds up to the dashboard's total gain.
"""

from __future__ import annotations

import pandas as pd

from .adjust import actual_closes, adjusted_closes
from .analysis import STALE_DAYS, cash_flows, compute, xirr
from .fees import break_even, fee_rate, fee_rates
from .lots import average_age, open_lots, replay, trips_frame
from .performance import (compare_with_index, dimson_beta, level_series, max_drawdown, regression, returns,
                          trading_days_per_year, volatility)
from .whatif import _only, _walk, close_on, exit_curve, holding_vs_selling, if_sold_on, sales_review

PARTICIPATION = 0.15  # share of a day's turnover you could sell without moving the price much


def ticker_detail(tx: pd.DataFrame, actions: pd.DataFrame, price: float | None, price_at, price_hist: pd.DataFrame,
                  index_hist: pd.DataFrame, symbol: str, today, fallback_fee: float, portfolio_value: float,
                  info: dict | None = None, fee_columns: pd.DataFrame | None = None,
                  sold_on=None) -> dict:
    """Everything the per-stock page shows. `price` is the latest price (None if unknown); `info` the latest
    security_info row; `fee_columns` the trades table's fee breakdown by contract number."""
    today = pd.Timestamp(today)
    t, a = _only(tx, actions, symbol)
    f = fee_rate(tx, today, fallback_fee)["rate"]
    rates = lambda days: fee_rates(tx, days, fallback_fee)  # noqa: E731
    pf = compute(t, {symbol: price} if price else {}, a)
    h = pf.holdings.iloc[0].to_dict() if len(pf.holdings) else None
    qty = h["quantity"] if h else 0.0
    value = (h["market_value"] if h and pd.notna(h["market_value"]) else (h["cost_basis"] if h else 0.0))
    unreal = h["unrealized_pnl"] if h and pd.notna(h["unrealized_pnl"]) else None
    selling = value * f if h and price else 0.0
    flows = cash_flows(t, pf.dividends)
    gain = pf.realized_pnl + pf.dividend_income + (unreal or 0.0)
    first = t["trade_date"].min() if len(t) else None
    span = (today - first).days if first is not None else 0
    rate, rate_net = (xirr(flows + [(today, value)]), xirr(flows + [(today, value - selling)])) if len(t) else (None, None)
    period = lambda r: (1 + r) ** (span / 365) - 1 if r is not None else None  # noqa: E731

    _, lots = replay(t, a)
    held_lots = open_lots(lots, symbol, today)
    trips = trips_frame(lots.trips)
    closes, splits = actual_closes(price_hist, a, symbol)
    adj = adjusted_closes(price_hist, a, symbol)
    level = level_series(index_hist)
    n = trading_days_per_year(index_hist, today)

    company = next((c for c in t["company"].dropna() if c), None) or (info or {}).get("name")
    out = {
        "symbol": symbol, "company": company, "isin": (info or {}).get("isin"), "as_of": today, "fee_rate": f,
        "position": {
            "quantity": qty, "avg_cost": h["avg_cost"] if h else None, "cost_basis": h["cost_basis"] if h else 0.0,
            "price": price, "price_at": price_at, "value": value if h else 0.0, "priced": bool(h and price),
            "liquidation_value": value - selling if h and price else None,
            "break_even": break_even(h["avg_cost"], f) if h else None, "unrealized": unreal,
            "unrealized_pct": h["unrealized_pct"] if h and pd.notna(h["unrealized_pct"]) else None,
            "weight": value / portfolio_value if h and portfolio_value else None,
        },
        "lifetime": {
            "realized": pf.realized_pnl, "unrealized": unreal, "dividends": pf.dividend_income, "total": gain,
            "total_if_sold": gain - selling, "fees": pf.fees, "fees_share": pf.fees / gain if gain > 0 else None,
            "xirr": rate, "xirr_if_sold": rate_net, "period_return": period(rate),
            "period_return_if_sold": period(rate_net), "days": span, "first_buy": first,
            "complete": h is None or bool(price),
        },
        "holding": {"first_buy": first, "average_age": average_age(held_lots),
                    "trips": int(len(trips)), "splits": splits},
        "warnings": pf.warnings,
    }
    out["yields"] = _yields(pf.actions, h, price, today)
    out["range"] = _range(info, closes, price, today)
    out["betas"] = {k: (info or {}).get(k) for k in ("beta_tri_aspi", "beta_spsl", "beta_period", "beta_quarter")}
    out["risk"] = _risk(adj, level, first, today, n)
    out["liquidity"] = _liquidity(price_hist, symbol, index_hist, value if h else 0.0, today, n)

    # Charts: [A] price with your trades and average cost, [B] gain if sold each day, [C] shares and money
    since = (first - pd.Timedelta(days=30)) if first is not None else today
    px = closes[closes.index >= since]
    out["prices"] = [{"date": d, "price": float(v)} for d, v in px.items()]
    out["trades"] = [{"date": r.trade_date, "side": r.side, "quantity": r.quantity, "price": r.price,
                      "kind": r.sources} for r in t.itertuples(index=False)]
    steps = []
    for d, kind, r, _, book, _, _ in _walk(t, a):
        q, c = book.pos.get(symbol, (0.0, 0.0))
        steps.append({"date": d, "quantity": q, "cost": c, "avg_cost": c / q if q else None})
    st = pd.DataFrame(steps, columns=["date", "quantity", "cost", "avg_cost"]).groupby("date").last().reset_index()
    if len(st):
        st["break_even"] = st["avg_cost"] / (1 - rates(st["date"]).to_numpy())
    out["steps"] = st
    curve = exit_curve(tx, actions, price_hist, symbol, today, rates)
    out["curve"] = curve
    if len(curve):
        best, worst = curve.loc[curve["gain"].idxmax()], curve.loc[curve["gain"].idxmin()]
        out["excursion"] = {"best": {"date": best["date"], "gain": best["gain"]},
                            "worst": {"date": worst["date"], "gain": worst["gain"]}}
    else:
        out["excursion"] = None

    out["lots"] = held_lots
    out["sales"] = sales_review(tx, actions, price_hist, index_hist, today, symbol)
    out["fills"] = _fills(t, fee_columns)
    out["actions"] = pf.actions
    vs = compare_with_index(flows, value if h else 0.0, today, level, lambda d: _value_on(curve, d))
    out["vs_index"] = vs
    if sold_on is not None:
        out["if_sold"] = _if_sold(tx, actions, price_hist, index_hist, symbol, sold_on, today, fallback_fee, gain - selling,
                                  rate_net)
    return out


def _value_on(curve: pd.DataFrame, day) -> float | None:
    if curve.empty:
        return None
    c = curve[curve["date"] <= pd.Timestamp(day)]
    return float(c["value"].iloc[-1]) if len(c) and (pd.Timestamp(day) - c["date"].iloc[-1]).days <= STALE_DAYS else None


def _yields(log: pd.DataFrame, h: dict | None, price: float | None, today) -> dict:
    """Yield on cost (what your money earns) and trailing yield (what a buyer today would), last 12 months."""
    d = log[(log["action"] == "DIVIDEND") & (log["date"] > today - pd.DateOffset(years=1)) & (log["date"] <= today)]
    income = float(d["amount"].sum())
    dps = float((d["amount"] / d["held"]).where(d["held"] > 0).sum()) if len(d) else 0.0
    return {"income_12m": income, "per_share_12m": dps, "count_12m": int(len(d)),
            "on_cost": income / h["cost_basis"] if h and h["cost_basis"] else None,
            "trailing": dps / price if price else None}


def _range(info: dict | None, closes: pd.Series, price: float | None, today) -> dict | None:
    """52-week high and low (the CSE's, else from stored closes), where the price sits in it, and how far it
    is below the high."""
    hi, lo, source = (info or {}).get("hi_52w"), (info or {}).get("lo_52w"), "CSE"
    if not (hi and lo):
        year = closes[closes.index > today - pd.Timedelta(days=365)]
        if year.empty:
            return None
        hi, lo, source = float(year.max()), float(year.min()), "closes"
    out = {"high": hi, "low": lo, "source": source, "position": None, "from_high": None}
    if price:
        out["position"] = (price - lo) / (hi - lo) if hi > lo else None
        out["from_high"] = price / hi - 1
    return out


def _risk(adj: pd.Series, level: pd.Series, first, today, n: float) -> dict | None:
    """Volatility and drawdown of the (split-adjusted) price since your first buy, and its beta on the ASPI over
    the last year, ordinary and corrected for thin trading. On the ASPI's calendar, so a day without a trade is
    a day without a move."""
    if adj.empty or first is None:
        return None
    s = adj[adj.index >= first - pd.Timedelta(days=STALE_DAYS)]
    if not level.empty:
        days = level.index[(level.index >= s.index[0]) & (level.index <= today)] if len(s) else level.index[:0]
        s = s.reindex(s.index.union(days)).ffill(limit=STALE_DAYS).reindex(days) if len(days) else s
    s = s[s.index >= first]
    r = returns(s)
    if len(r) < 10:
        return None
    out = {"volatility": volatility(r, n), "days": int(len(r)), "drawdown": max_drawdown(s),
           "beta": None, "dimson_beta": None, "r2": None}
    if not level.empty:
        m = returns(level)
        year = r[r.index > today - pd.Timedelta(days=365)]
        reg = regression(year, m, n)
        if reg:
            out.update(beta=reg["beta"], r2=reg["r2"], dimson_beta=dimson_beta(year, m))
    return out


def _liquidity(price_hist: pd.DataFrame, symbol: str, index_hist: pd.DataFrame, value: float, today, n: float) -> dict:
    """Average daily turnover over the last year (close × shares traded; days without a trade count as zero),
    days to sell the position at PARTICIPATION of it, and days without a trade in the last three months."""
    p = price_hist[(price_hist["symbol"] == symbol)].copy()
    p["date"] = pd.to_datetime(p["date"])
    year = p[(p["date"] > today - pd.Timedelta(days=365)) & (p["date"] <= today)]
    vol = year["volume"] if "volume" in year else pd.Series(dtype=float)
    out = {"turnover_per_day": None, "days_to_exit": None, "no_trade_days": None, "trading_days": None}
    if len(year) and vol.notna().any():
        adt = float((year["price"] * vol.fillna(0)).sum() / n)
        out["turnover_per_day"] = adt
        out["days_to_exit"] = value / (PARTICIPATION * adt) if adt and value else None
    days = pd.to_datetime(index_hist["date"]) if index_hist is not None and len(index_hist) else pd.Series(dtype="datetime64[ns]")
    recent = days[(days > today - pd.Timedelta(days=91)) & (days <= today)]
    if len(recent):
        traded = set(p.loc[p["volume"].fillna(1) > 0, "date"]) if "volume" in p else set(p["date"])
        out["no_trade_days"] = int(sum(d not in traded for d in recent))
        out["trading_days"] = int(len(recent))
    return out


def _fills(t: pd.DataFrame, fee_columns: pd.DataFrame | None) -> pd.DataFrame:
    """This stock's trades with each fee, where a Contract Note or Trade Confirmation gave the breakdown."""
    cols = ["trade_date", "side", "quantity", "price", "gross_amount", "fees", "net_amount", "contract_no", "sources"]
    out = t[cols].copy()
    if fee_columns is not None and len(fee_columns):
        out = out.merge(fee_columns, on="contract_no", how="left")
    return out.sort_values(["trade_date", "contract_no"], ascending=False)


def _if_sold(tx, actions, price_hist, index_hist, symbol, day, today, fallback_fee, actual_if_sold, actual_xirr) -> dict:
    """Selling all of this stock at the close on `day`, against what actually happened (sold today, after fees)."""
    from .adjust import actual_history

    day = pd.Timestamp(day)
    f_day = fee_rate(tx, day, fallback_fee)["rate"]
    r = if_sold_on(tx, actions, actual_history(price_hist, actions), day, f_day, symbol)
    [h] = r["holdings"].to_dict("records") or [None]
    cohort = holding_vs_selling(tx, actions, price_hist, index_hist, symbol, day, today, f_day,
                                fee_rate(tx, today, fallback_fee)["rate"])
    return {"day": day, "fee_rate": f_day, "held": h is not None, "quantity": h["quantity"] if h else 0.0,
            "price": h["price"] if h else None, "price_date": h["price_date"] if h else None,
            "proceeds": h["proceeds"] if h else None, "gain": r["gain"] if h and h["price"] is not None else None,
            "gain_pct": r["gain"] / r["cost"] if h and h["price"] is not None and r["cost"] else None,
            "realized": r["realized"], "dividends": r["dividends"],
            "lifetime": r["lifetime"] if r["complete"] else None, "xirr": r["xirr"] if r["complete"] else None,
            "actual": actual_if_sold, "actual_xirr": actual_xirr,
            "better_by": r["lifetime"] - actual_if_sold if r["complete"] and h else None, "cohort": cohort}
