"""Alerts about what you hold (docs/06 §8): a price below break-even, a new 52-week low, a big move today, and
what the CSE announced about a company you hold.

They only point things out. The CSE's announcements name the company but give no symbol, and for a dividend no
XD date or amount, so the corporate_actions.csv row an alert suggests is for you to complete and add; nothing
is ever written for you.
"""

from __future__ import annotations

import re

import pandas as pd

BIG_MOVE = 0.05  # a day's change of this much, either way
RECENT = 30  # days an announcement stays an alert
YEAR_SPAN = 330  # stored closes must reach back this far to call a price a 52-week low

# What an announcement's category says, and the corporate_actions.csv row it may call for.
KINDS = [
    (re.compile(r"SCRIP DIVIDEND"), "a scrip dividend", "BONUS", "quantity=<shares received>"),
    (re.compile(r"DIVIDEND"), "a cash dividend", "DIVIDEND", "per_share=<amount a share>"),
    (re.compile(r"RIGHTS"), "a rights issue", "RIGHTS", "quantity=<shares subscribed>, price=<subscription price>"),
    (re.compile(r"SUB-?DIVISION|CONSOLIDATION|SPLIT"), "a share split", "SPLIT", "ratio=<new>:<old>"),
    (re.compile(r"CAPITALI[SZ]ATION|BONUS"), "a bonus issue", "BONUS", "ratio=<new>:<held>"),
    (re.compile(r"DELIST|DE-LIST"), "a delisting", "DELIST", "price=<buyout price>"),
    (re.compile(r"MANDATORY OFFER|TAKEOVER"), "an offer for the shares", None, None),
    (re.compile(r"TRADING HALTED|SUSPEN"), "a trading halt", None, None),
]


def _name(s) -> str:
    """Company names as the CSE and the documents may write them: case, punctuation and '&' ignored."""
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", str(s).upper().replace("&", " AND ")).split())


def price_alerts(holdings: pd.DataFrame, info: pd.DataFrame, closes: pd.DataFrame, today) -> list[dict]:
    """`holdings`: the overview's rows (symbol, price, break_even, day_change, priced); `info`: the latest
    security_info row per symbol (lo_52w); `closes`: price_history, for a 52-week low the CSE hasn't given."""
    today = pd.Timestamp(today)
    lows = dict(zip(info["symbol"], info["lo_52w"])) if len(info) else {}
    out = []
    for h in holdings[holdings["priced"]].itertuples(index=False):
        p = float(h.price)
        if pd.notna(h.break_even) and p < h.break_even:
            out.append({"symbol": h.symbol, "rule": "break-even",
                        "message": f"{h.symbol} at {p:,.2f} is below its break-even of {h.break_even:,.2f}: selling "
                                   "now would lose money on it, after fees both ways."})
        low = lows.get(h.symbol)
        if low is None or pd.isna(low):
            c = closes[(closes["symbol"] == h.symbol) & (closes["date"] < today)
                       & (closes["date"] >= today - pd.Timedelta(days=365))]
            low = float(c["price"].min()) if len(c) and (today - c["date"].min()).days >= YEAR_SPAN else None
        if low is not None and p <= low:
            out.append({"symbol": h.symbol, "rule": "52-week low",
                        "message": f"{h.symbol} at {p:,.2f} is at a 52-week low (it was {float(low):,.2f})."})
        if pd.notna(h.day_change) and abs(h.day_change) >= BIG_MOVE:
            out.append({"symbol": h.symbol, "rule": "big move",
                        "message": f"{h.symbol} moved {h.day_change * 100:+.1f}% today, to {p:,.2f}."})
    return out


def announcement_alerts(announcements: pd.DataFrame, names: dict[str, set[str]], today) -> list[dict]:
    """Announcements in the last RECENT days about a company you hold (`names`: symbol -> its names as written in
    your documents and by the CSE), with the corporate_actions.csv row each kind may call for."""
    today = pd.Timestamp(today)
    by_name = {_name(n): sym for sym, ns in names.items() for n in ns if n}
    out = []
    recent = announcements[pd.to_datetime(announcements["date"]) >= today - pd.Timedelta(days=RECENT)]
    for a in recent.sort_values(["date", "id"], ascending=False).itertuples(index=False):
        sym = by_name.get(_name(a.company))
        kind = next(((what, action, fields) for rx, what, action, fields in KINDS if rx.search(a.category.upper())), None)
        if sym is None or kind is None:
            continue
        what, action, fields = kind
        day = pd.Timestamp(a.date).strftime("%d %b %Y")
        suggest = (f"date=<the {'XD' if action in ('DIVIDEND', 'BONUS', 'RIGHTS') else 'effective'} date>, symbol={sym}, "
                   f"action={action}, {fields}") if action else None
        out.append({"symbol": sym, "rule": "announcement", "suggest": suggest,
                    "message": f"{a.company} ({sym}) announced {what} on {day} ({a.category.title()})."
                               + (" Once its dates and amounts are out, add a row to data/corporate_actions.csv."
                                  if action else "")})
    return out
