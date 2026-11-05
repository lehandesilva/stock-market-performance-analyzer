"""Corporate actions the broker's documents don't show: data/corporate_actions.csv.

Dividends are paid, and bonus shares credited, by the company's registrar straight to you and your CDS
account, so they never appear on an NDB statement. Each row is checked as strictly as a parsed document:
anything unclear stops with the file and line number instead of guessing.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd

from .errors import PipelineStop
from .parsers.ndb.common import SYMBOL

ACTION_COLUMNS = ["date", "symbol", "action", "ratio_new", "ratio_old", "quantity", "price", "amount",
                  "per_share", "note"]
OPTIONAL = {"ratio", "quantity", "price", "amount", "per_share", "note"}
# Which optional fields each action takes ("note" is always allowed).
FIELDS = {
    "DIVIDEND": {"amount", "per_share"},   # one of them
    "BONUS": {"ratio", "quantity"},        # ratio, or the exact shares received (wins if both are given)
    "SPLIT": {"ratio"},
    "RIGHTS": {"quantity", "price"},       # shares subscribed, at the subscription price
    "DELIST": {"price"},                   # left the exchange: what each share was bought out at (0: nothing)
}
RATIO = re.compile(r"^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


def empty_actions() -> pd.DataFrame:
    df = pd.DataFrame(columns=ACTION_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_actions(path: Path | None) -> pd.DataFrame:
    """Read and validate the corporate actions file. A missing file means no actions."""
    if not path or not path.exists():
        return empty_actions()
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = [c.strip() for c in reader.fieldnames or []]
        if missing := {"date", "symbol", "action"} - set(cols):
            raise PipelineStop(f"{path.name}: missing column(s) {', '.join(sorted(missing))}")
        if unknown := set(cols) - {"date", "symbol", "action"} - OPTIONAL:
            raise PipelineStop(f"{path.name}: unknown column(s) {', '.join(sorted(unknown))}; "
                               f"allowed: date, symbol, action, {', '.join(sorted(OPTIONAL))}")
        rows = []
        for n, raw in enumerate(reader, start=2):
            row = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
            if not any(row.values()):
                continue  # blank line
            rows.append(_row(row, f"{path.name} line {n}"))
    if not rows:
        return empty_actions()
    df = pd.DataFrame(rows, columns=ACTION_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date", kind="stable").reset_index(drop=True)


def _row(row: dict, where: str) -> dict:
    def bad(msg: str) -> PipelineStop:
        return PipelineStop(f"{where}: {msg}")

    action = row["action"].upper()
    if action not in FIELDS:
        raise bad(f"unknown action {row['action']!r}; use one of {', '.join(FIELDS)}")
    if not DATE.match(row["date"]):
        raise bad(f"date {row['date']!r} must be YYYY-MM-DD")
    try:
        pd.Timestamp(row["date"])
    except ValueError:
        raise bad(f"{row['date']!r} is not a real date") from None
    symbol = row["symbol"].upper()
    if not re.fullmatch(SYMBOL, symbol):
        raise bad(f"symbol {row['symbol']!r} should look like JKH.N0000")
    given = {k for k in OPTIONAL - {"note"} if row.get(k)}
    if extra := given - FIELDS[action]:
        raise bad(f"{', '.join(sorted(extra))} doesn't apply to {action}")

    def number(key: str, integer: bool = False) -> float | None:
        v = row.get(key, "").replace(",", "")
        if not v:
            return None
        zero_ok = action == "DELIST"  # shares that were written off
        if not NUMBER.match(v) or (float(v) <= 0 and not zero_ok) or (integer and not v.isdigit()):
            raise bad(f"{key} {row[key]!r} must be a positive {'whole number' if integer else 'number'}")
        return float(v)

    out = {"date": row["date"], "symbol": symbol, "action": action, "ratio_new": None, "ratio_old": None,
           "quantity": number("quantity", integer=True), "price": number("price"), "amount": number("amount"),
           "per_share": number("per_share"), "note": row.get("note", "")}
    if row.get("ratio"):
        m = RATIO.match(row["ratio"])
        if not m or float(m.group(1)) <= 0 or float(m.group(2)) <= 0:
            raise bad(f"ratio {row['ratio']!r} must look like 1:10")
        out["ratio_new"], out["ratio_old"] = float(m.group(1)), float(m.group(2))

    if action == "DIVIDEND" and len(given) != 1:
        raise bad("DIVIDEND needs exactly one of amount (cash received) or per_share")
    if action == "BONUS" and not given:
        raise bad("BONUS needs a ratio (e.g. 1:10) or the quantity of shares received")
    if action == "SPLIT" and not given:
        raise bad("SPLIT needs a ratio, e.g. 2:1 (each share becomes two) or 1:5 (five become one)")
    if action == "RIGHTS" and given != {"quantity", "price"}:
        raise bad("RIGHTS needs the quantity subscribed and the subscription price")
    if action == "DELIST" and not given:
        raise bad("DELIST needs the price each share was bought out at (0 if you got nothing)")
    return out
