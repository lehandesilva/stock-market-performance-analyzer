"""CSV exports (docs/06 §8): a yearly summary for tax time, every transaction, and one file per stock."""

from __future__ import annotations

import pandas as pd

from .analysis import NON_MARKET, compute


def yearly_summary(tx: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Per calendar year: trades, buys and sells (gross), fees, realized P&L (average cost), dividends received,
    and the tax withheld from them (per_share rows with [tax].dividend_withholding; an `amount` is what reached
    you, so its tax isn't known)."""
    pf = compute(tx, {}, actions)
    years = sorted({*tx["trade_date"].dt.year, *pf.dividends["date"].dt.year} if len(tx) or len(pf.dividends) else [])
    taxed = actions[(actions["action"] == "DIVIDEND") & actions["per_share"].notna()] if len(actions) else actions
    rate = {(r.date, r.symbol): float(r.tax or 0.0) for r in taxed.itertuples(index=False)}
    divs = pf.dividends.assign(withheld=[a * rate.get((d, s), 0.0) / (1 - rate.get((d, s), 0.0))
                                         for d, s, a in zip(pf.dividends["date"], pf.dividends["symbol"],
                                                            pf.dividends["amount"])])
    rows = []
    for y in years:
        t = tx[tx["trade_date"].dt.year == y]
        market = t[~t["sources"].isin(NON_MARKET)]
        r = pf.realized[pf.realized["trade_date"].dt.year == y] if len(pf.realized) else pf.realized
        dv = divs[divs["date"].dt.year == y]
        rows.append({"year": int(y), "trades": int(len(t)),
                     "bought": round(float(market.loc[market["side"] == "BUY", "gross_amount"].sum()), 2),
                     "sold": round(float(market.loc[market["side"] == "SELL", "gross_amount"].sum()), 2),
                     "fees": round(float(t["fees"].sum()), 2), "realized_pnl": round(float(r["pnl"].sum()), 2),
                     "dividends": round(float(dv["amount"].sum()), 2),
                     "dividend_tax_withheld": round(float(dv["withheld"].sum()), 2)})
    return pd.DataFrame(rows, columns=["year", "trades", "bought", "sold", "fees", "realized_pnl", "dividends",
                                       "dividend_tax_withheld"])


def stock_history(tx: pd.DataFrame, actions: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Everything that happened to one stock, oldest first: trades (each sale with its realized P&L at average
    cost) and corporate actions, with the shares held after each."""
    from .whatif import _walk

    t = tx[tx["symbol"] == symbol]
    a = actions[actions["symbol"] == symbol] if len(actions) else actions
    out = []
    for d, kind, r, _, book, _, _ in _walk(t, a):
        held = book.pos.get(symbol, (0.0, 0.0))[0]
        if kind == 1:
            out.append({"date": d, "event": r.side, "quantity": r.quantity, "price": r.price, "gross": r.gross_amount,
                        "fees": r.fees, "net": r.net_amount,
                        "realized_pnl": round(book.realized[-1]["pnl"], 2) if r.side == "SELL" else None,
                        "cash": None, "held_after": held, "contract_no": r.contract_no or None, "detail": r.sources})
        else:
            e = book.log[-1]
            out.append({"date": d, "event": r.action, "quantity": e["shares"] or None, "price": None, "gross": None,
                        "fees": None, "net": None, "realized_pnl": None,
                        "cash": e["amount"] if r.action == "DIVIDEND" else None, "held_after": held,
                        "contract_no": None, "detail": e["detail"]})
    df = pd.DataFrame(out, columns=["date", "event", "quantity", "price", "gross", "fees", "net", "realized_pnl", "cash",
                                    "held_after", "contract_no", "detail"])
    if len(df):
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def transactions(tx: pd.DataFrame) -> pd.DataFrame:
    out = tx.copy()
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    return out


def to_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")
