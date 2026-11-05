"""The Performance page (GET /api/performance): how well, compared with what, and for how much risk (docs/04)."""

from __future__ import annotations

import sqlite3

import pandas as pd

from ..adjust import actual_history, adjusted_closes
from ..analysis import STALE_DAYS, benchmark, cash_flows, compute, value_history, xirr
from ..config import Config
from ..extdata import at_or_before, load_ccpi, load_fx, load_indices, load_sectors, load_tbill
from ..performance import (annualize, attribution, bootstrap, brinson, compare_with_index, concentration,
                           converted_xirr, dimson_beta, level_series, mean_rate, monthly_returns, on_calendar,
                           regression, returns, risk_contributions, risk_table, tbill_index, trading_days_per_year,
                           twr_index)
from ..prices import SECTOR, SPSL20
from ..whatif import _upto
from .api import _dates, _plain, load_data


def performance_payload(cfg: Config, conn: sqlite3.Connection) -> dict:
    d = load_data(cfg, conn)
    _, _, _, when, _ = _dates(d, None)
    if when is None:
        return {"currency": cfg.currency, "empty": True}
    today = max(when, d.fetched_day) if d.fetched_day is not None else when
    prices = dict(zip(d.prices["symbol"], d.prices["price"]))
    pf = compute(d.tx, prices, d.actions)
    h = pf.holdings
    value_now = float(h["market_value"].fillna(h["cost_basis"]).sum()) if len(h) else 0.0
    ph = actual_history(d.price_hist, d.actions)
    end = max(today, ph["date"].max()) if len(ph) else today
    hist = value_history(d.tx, d.actions, ph, d.index_hist, end)
    flows = cash_flows(d.tx, pf.dividends)
    growth = twr_index(hist, flows)
    aspi = level_series(d.index_hist)
    n = trading_days_per_year(d.index_hist, today)
    tbill = load_tbill(cfg.tbill_csv)
    out = {"currency": cfg.currency, "empty": False, "as_of": today, "trading_days_per_year": n,
           "files": {name: path.exists() for name, path in (("tbill.csv", cfg.tbill_csv), ("ccpi.csv", cfg.ccpi_csv),
                                                             ("fx.csv", cfg.fx_csv), ("indices.csv", cfg.indices_csv),
                                                             ("sectors.csv", cfg.sectors_csv))}}
    out["money"] = _money(cfg, d, flows, value_now, today)
    if len(growth) < 2:
        out["window"] = None
        return _plain(out)

    start, stop = growth.index[0], growth.index[-1]
    valued = hist.dropna(subset=["value"]).set_index("date")["value"]
    rf = mean_rate(tbill, start, stop)
    risk = risk_table(growth, aspi, n, rf, value_now)
    _, mw = benchmark(hist, flows, d.index_hist)
    if mw is None:  # no index levels: the money-weighted return over the window, without the comparison
        mw = {"xirr": xirr([(start, -float(valued.iloc[0]))] + [(t, a) for t, a in flows if start < t <= stop]
                           + [(stop, float(valued.iloc[-1]))])}
    twr_rate = annualize(risk["growth"], risk["days"]) if risk["growth"] else None
    out["window"] = {"start": start, "end": stop, "days": risk["days"]}
    out["twr"] = {"growth": risk["growth"], "annual": twr_rate, "return": risk["return"], "annualized": risk["annualized"],
                  "xirr": mw.get("xirr"),
                  "timing": mw["xirr"] - twr_rate if risk["annualized"] and None not in (mw.get("xirr"), twr_rate) else None}
    out["risk"] = risk
    out["bootstrap"] = bootstrap(returns(growth), n)
    out["monthly"] = monthly_returns(hist, flows)

    # Growth of 100 against each benchmark over the same days, and the fair comparisons with each
    days = growth.index
    series = {"You": growth / growth.iloc[0] * 100}
    others = {"ASPI": aspi}
    spsl = d.index_named(SPSL20)
    if len(spsl):
        others["S&P SL20"] = spsl
    extra = load_indices(cfg.indices_csv)
    for name, g in extra.groupby("name"):
        others[name] = level_series(g[["date", "value"]])
    if len(tbill):
        others["T-bills"] = tbill_index(tbill, pd.date_range(min(start, tbill.index[0]), stop, freq="D"))
    value_on = lambda t: at_or_before(valued, t, STALE_DAYS)  # noqa: E731
    final = float(valued.iloc[-1])
    rows = []
    for name, level in others.items():
        on = on_calendar(level, days, limit=40 if name not in ("ASPI", "S&P SL20") else 10)
        if on.notna().sum() >= 2:
            first = on.first_valid_index()
            series[name] = on / on[first] * 100
        cmp = compare_with_index([(t, a) for t, a in flows if t <= stop], final, stop, level, value_on)
        lv = on.dropna()
        ret = (annualize(float(lv.iloc[-1] / lv.iloc[0]), (lv.index[-1] - lv.index[0]).days)
               if len(lv) > 1 and (lv.index[-1] - lv.index[0]).days >= 365 else
               (float(lv.iloc[-1] / lv.iloc[0] - 1) if len(lv) > 1 else None))
        rows.append({"name": name, "since": cmp["since"] if cmp else (lv.index[0] if len(lv) else None),
                     "return": ret, "ks_pme": cmp["ks_pme"] if cmp else None,
                     "direct_alpha": cmp["direct_alpha"] if cmp else None})
    out["benchmarks"] = rows
    g = pd.DataFrame(series)
    g = g[g.index.isin(days)]
    g["drawdown"] = growth / growth.cummax() - 1
    out["growth"] = g.reset_index(names="date")
    out["holdings"] = _holdings(cfg, conn, d, h, prices, aspi, n, today)
    out["attribution"] = _attribution(cfg, conn, d, ph, prices, aspi, today, start)
    return _plain(out)


def _money(cfg: Config, d, flows, value_now: float, today) -> dict:
    """The money-weighted return of everything, in rupees, in today's rupees (CCPI) and in US dollars."""
    nominal = xirr(flows + [(today, value_now)]) if flows else None
    ccpi, fx = load_ccpi(cfg.ccpi_csv), load_fx(cfg.fx_csv)
    cpi_now = at_or_before(ccpi, today)
    real = converted_xirr(flows, value_now, today, lambda t: (cpi_now / c) if cpi_now and (c := at_or_before(ccpi, t)) else None) \
        if len(ccpi) and flows else None
    usd = converted_xirr(flows, value_now, today, lambda t: (1 / r) if (r := at_or_before(fx, t, 10)) else None) \
        if len(fx) and flows else None
    since = min(t for t, _ in flows) if flows else None
    cpi_then = at_or_before(ccpi, since) if since is not None else None
    fx_then, fx_now = (at_or_before(fx, since, 10), at_or_before(fx, today, 10)) if since is not None else (None, None)
    return {"since": since, "nominal": nominal, "real": real, "usd": usd,
            "inflation": cpi_now / cpi_then - 1 if cpi_now and cpi_then else None,
            "rupee_vs_usd": fx_then / fx_now - 1 if fx_then and fx_now else None}


def _holdings(cfg: Config, conn, d, h: pd.DataFrame, prices: dict, aspi: pd.Series, n: float, today) -> dict:
    """Concentration, and each holding's risk over the last year: volatility, betas, share of portfolio risk."""
    if h.empty:
        return {"concentration": None, "risk": None, "betas": []}
    w = pd.Series(h["market_value"].fillna(h["cost_basis"]).to_numpy(), index=h["symbol"])
    days = aspi.index[(aspi.index > today - pd.Timedelta(days=365)) & (aspi.index <= today)] if len(aspi) else None
    rets = {}
    for sym in h["symbol"]:
        adj = adjusted_closes(d.price_hist, d.actions, sym)
        if adj.empty:
            continue
        s = on_calendar(adj, days, limit=STALE_DAYS) if days is not None and len(days) else adj[adj.index > today - pd.Timedelta(days=365)]
        rets[sym] = returns(s.dropna()) if s.notna().sum() > 2 else pd.Series(dtype=float)
    r = pd.DataFrame(rets)
    info = pd.read_sql_query("""SELECT s.symbol, s.beta_tri_aspi FROM security_info s JOIN (
                                  SELECT symbol, MAX(fetched_on) AS f FROM security_info GROUP BY symbol) l
                                ON l.symbol = s.symbol AND l.f = s.fetched_on""", conn)
    cse = dict(zip(info["symbol"], info["beta_tri_aspi"]))
    m = returns(aspi)
    betas = []
    for sym in h["symbol"]:
        x = r[sym].dropna() if sym in r else pd.Series(dtype=float)
        reg = regression(x, m, n) if len(x) else None
        betas.append({"symbol": sym, "weight": float(w[sym] / w.sum()) if w.sum() else None,
                      "beta": reg["beta"] if reg else None, "r2": reg["r2"] if reg else None,
                      "dimson_beta": dimson_beta(x, m) if len(x) else None, "cse_beta": cse.get(sym),
                      "days": int(len(x))})
    return {"concentration": concentration(w), "risk": risk_contributions(r, w, n) if len(r.columns) else None,
            "betas": betas}


def _attribution(cfg: Config, conn, d, ph: pd.DataFrame, prices: dict, aspi: pd.Series, today, start) -> dict | None:
    """By stock over the last 12 months (or since price history starts), and by sector with sectors.csv."""
    begin = max(pd.Timestamp(start), today - pd.Timedelta(days=365))

    def values(day, latest: bool) -> dict:
        book = _upto(d.tx, d.actions, day)
        out = {}
        for sym, (q, _) in book.held().items():
            px = prices.get(sym) if latest else None
            if px is None:
                c = ph[(ph["symbol"] == sym) & (ph["date"] <= day)].sort_values("date")
                px = float(c["price"].iloc[-1]) if len(c) and (day - c["date"].iloc[-1]).days <= STALE_DAYS else None
            out[sym] = q * px if px is not None else None
        return out

    v0, v1 = values(begin, False), values(today, True)
    flows: dict[str, list] = {}
    for r in d.tx[(d.tx["trade_date"] > begin) & (d.tx["trade_date"] <= today)].itertuples(index=False):
        flows.setdefault(r.symbol, []).append((r.trade_date, r.net_amount if r.side == "BUY" else -r.net_amount))
    pf = compute(d.tx[d.tx["trade_date"] <= today], {}, d.actions[d.actions["date"] <= today])
    for r in pf.dividends[(pf.dividends["date"] > begin)].itertuples(index=False):
        flows.setdefault(r.symbol, []).append((r.date, -r.amount))
    unpriced = sorted(s for s, v in {**v0, **v1}.items() if v is None)
    attr = attribution(v0, v1, flows, begin, today) if not unpriced else None
    out = {"start": begin, "end": today, "unpriced": unpriced, "stocks": attr, "sectors": None}
    sectors = load_sectors(cfg.sectors_csv)
    if attr is None or not sectors:
        return out
    m0, m1 = at_or_before(aspi, begin, 10), at_or_before(aspi, today, 10)
    sector_return = {}
    for sec in set(sectors.values()):
        lv = d.index_named(SECTOR + sec)
        a, b = at_or_before(lv, begin, 10), at_or_before(lv, today, 10)
        sector_return[sec] = b / a - 1 if a and b else None
    caps = pd.read_sql_query("""SELECT symbol, market_cap FROM daily_quotes WHERE date = (
                                  SELECT MAX(date) FROM daily_quotes WHERE date <= ?)""", conn,
                             params=(begin.strftime("%Y-%m-%d"),))
    caps = caps.dropna()
    total = float(caps["market_cap"].sum())
    mapped = caps[caps["symbol"].isin(sectors)]
    weights = (mapped.assign(sector=mapped["symbol"].map(sectors)).groupby("sector")["market_cap"].sum() / total).to_dict() \
        if total else {}
    out["sectors"] = brinson(attr, sectors, sector_return, weights, m1 / m0 - 1 if m0 and m1 else 0.0) if m0 and m1 else None
    out["index_coverage"] = float(mapped["market_cap"].sum() / total) if total else None
    out["market_return"] = m1 / m0 - 1 if m0 and m1 else None
    return out


def habits_payload(cfg: Config, conn: sqlite3.Connection) -> dict:
    """The Habits page (GET /api/habits): your decisions, judged from your own trades (docs/05)."""
    from ..db import FEE_COLUMNS
    from ..extdata import load_journal
    from ..fees import fee_rate, fee_rates
    from ..habits import habits

    d = load_data(cfg, conn)
    _, _, _, when, _ = _dates(d, None)
    if when is None:
        return {"currency": cfg.currency, "empty": True}
    today = max(when, d.fetched_day) if d.fetched_day is not None else when
    pf = compute(d.tx, dict(zip(d.prices["symbol"], d.prices["price"])), d.actions)
    ph = actual_history(d.price_hist, d.actions)
    end = max(today, ph["date"].max()) if len(ph) else today
    hist = value_history(d.tx, d.actions, ph, d.index_hist, end)
    growth = twr_index(hist, cash_flows(d.tx, pf.dividends))
    fee_columns = pd.read_sql_query(f"SELECT contract_no, {FEE_COLUMNS} FROM trades", conn)
    balances = pd.read_sql_query("SELECT date, period_start, opening, balance FROM cash_balances ORDER BY date", conn)
    out = habits(d.tx, d.actions, ph, d.index_hist, today, fee_rate(d.tx, today, cfg.fee_rate)["rate"],
                 lambda days: fee_rates(d.tx, days, cfg.fee_rate), pf, hist, growth, fee_columns,
                 load_journal(cfg.journal_csv), balances, d.cash, load_tbill(cfg.tbill_csv))
    out.pop("lots")
    return _plain({"currency": cfg.currency, "empty": False, "as_of": today, "trades": len(d.tx),
                   "journal_file": cfg.journal_csv.exists(), **out})
