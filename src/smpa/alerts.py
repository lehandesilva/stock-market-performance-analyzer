"""Things worth a look today (docs/06 §8): a holding below break-even, at a new 52-week low or after a big move,
and CSE announcements from the companies you hold.

Announcements come with a company name but no symbol, ex-date or amount, so an alert can only *suggest* a
corporate_actions.csv row, with what's unknown left for you to fill in. Nothing is ever written for you.
"""

from __future__ import annotations

import re

import pandas as pd

BIG_MOVE = 0.05  # a day's change this large, either way
ANNOUNCED_DAYS = 30  # announcements this recent
LOW_DAYS = 300  # stored closes must span this many days to call a 52-week low from them
# Announcement category -> the corporate_actions.csv row it calls for, and what to fill in
SUGGEST = {
    "CASH DIVIDEND": ("DIVIDEND", "per_share=<amount a share>"),
    "RIGHTS ISSUE": ("RIGHTS", "quantity=<shares subscribed>,price=<subscription price>"),
    "SUB-DIVISION OF SHARES": ("SPLIT", "ratio=<new:old>"),
    "SUBDIVISION OF SHARES": ("SPLIT", "ratio=<new:old>"),
    "CAPITALIZATION OF RESERVES": ("BONUS", "ratio=<new:held>"),
    "CAPITALISATION OF RESERVES": ("BONUS", "ratio=<new:held>"),
    "SCRIP DIVIDEND": ("BONUS", "quantity=<shares received>"),
    "DELISTING": ("DELIST", "price=<buyout price a share>"),
}


def company_key(name: str | None) -> str:
    """'Alpha PLC.' and 'ALPHA PLC' alike: upper case, letters and digits only, without PLC / LIMITED."""
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    s = re.sub(r"\b(PLC|LIMITED|LTD|THE)\b", " ", s)
    return " ".join(s.split())


def alerts(holdings: pd.DataFrame, price_hist: pd.DataFrame, lows: dict[str, float], names: dict[str, set[str]],
           announcements: pd.DataFrame, actions: pd.DataFrame, today) -> list[dict]:
    """`holdings` as on the dashboard (symbol, price, break_even, day_change, priced); `price_hist` closes as
    traded; `lows` the CSE's 52-week low per symbol; `names` every company name seen per symbol (statements,
    the CSE); `announcements` the announcements table; `actions` corporate_actions.csv."""
    today = pd.Timestamp(today)
    out = []
    for h in holdings[holdings["priced"]].itertuples(index=False):
        if pd.notna(h.break_even) and h.price < h.break_even:
            out.append({"kind": "break-even", "symbol": h.symbol,
                        "message": f"{h.symbol} at {h.price:,.2f} is below its break-even {h.break_even:,.2f} "
                                   "(average cost plus selling fees): selling now would lock in a loss."})
        closes = price_hist[(price_hist["symbol"] == h.symbol)
                            & (pd.to_datetime(price_hist["date"]) > today - pd.Timedelta(days=365))]
        span = (pd.to_datetime(closes["date"]).max() - pd.to_datetime(closes["date"]).min()).days if len(closes) else 0
        low = float(closes["price"].min()) if span >= LOW_DAYS else lows.get(h.symbol)
        if low is not None and pd.notna(low) and h.price <= low:
            out.append({"kind": "52-week low", "symbol": h.symbol,
                        "message": f"{h.symbol} at {h.price:,.2f} is at its lowest in a year "
                                   f"({'stored closes' if span >= LOW_DAYS else 'the CSE says'} {low:,.2f})."})
        if pd.notna(h.day_change) and abs(h.day_change) >= BIG_MOVE:
            out.append({"kind": "big move", "symbol": h.symbol,
                        "message": f"{h.symbol} moved {h.day_change * 100:+.1f}% on its last trading day."})
    keys = {company_key(n): sym for sym, ns in names.items() for n in ns if company_key(n)}
    recent = announcements[pd.to_datetime(announcements["date"]) > today - pd.Timedelta(days=ANNOUNCED_DAYS)] \
        if len(announcements) else announcements
    for a in recent.sort_values("date").itertuples(index=False) if len(recent) else []:
        sym = keys.get(company_key(a.company))
        if sym is None:
            continue
        msg = f"{a.date}: {a.company} announced {a.category.lower()}."
        action = SUGGEST.get(a.category.upper())
        if action:
            done = actions[(actions["symbol"] == sym) & (actions["action"] == action[0])
                           & (actions["date"] >= pd.Timestamp(a.date) - pd.Timedelta(days=7))]
            if len(done):
                continue  # already recorded
            msg += (f" Once the details are out, add to corporate_actions.csv: <ex-date>,{sym},{action[0]} with "
                    f"{action[1]}, note \"announced {a.date}\".")
        out.append({"kind": "announcement", "symbol": sym, "message": msg,
                    "suggest": action[0] if action else None})
    return out
