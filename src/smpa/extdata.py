"""Small CSV files you keep in data/ for what the CSE API doesn't give.

Each is checked as strictly as corporate_actions.csv: an unknown column, a bad date or number, or a repeated
row stops with the file and line number instead of being guessed at. A missing file just means the figures
that need it are left out.

| File          | Columns                          | For                                              |
|---------------|----------------------------------|--------------------------------------------------|
| tbill.csv     | date, yield (percent)            | the risk-free rate: Sharpe, Sortino, T-bill PME  |
| ccpi.csv      | month (YYYY-MM), index           | returns after inflation                          |
| fx.csv        | date, lkr_per_usd                | returns in US dollars                            |
| indices.csv   | name, date, value                | more benchmarks, e.g. ASTRI month-end levels     |
| sectors.csv   | symbol, sector                   | sector weights and attribution                   |
| journal.csv   | contract_no, reason[, target, stop, note] | why you bought, next to how it went     |
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd

from .errors import PipelineStop
from .parsers.ndb.common import SYMBOL

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH = re.compile(r"^\d{4}-\d{2}$")
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def _date(v: str, bad) -> pd.Timestamp:
    if not DATE.match(v):
        raise bad(f"date {v!r} must be YYYY-MM-DD")
    try:
        return pd.Timestamp(v)
    except ValueError:
        raise bad(f"{v!r} is not a real date") from None


def _month(v: str, bad) -> pd.Timestamp:
    if not MONTH.match(v):
        raise bad(f"month {v!r} must be YYYY-MM")
    try:
        return pd.Timestamp(v + "-01")
    except ValueError:
        raise bad(f"month {v!r} is not a real month") from None


def _positive(v: str, bad, what: str, below: float | None = None) -> float:
    v2 = v.replace(",", "")
    if not NUMBER.match(v2) or float(v2) <= 0 or (below is not None and float(v2) >= below):
        raise bad(f"{what} {v!r} must be a positive number" + (f" below {below:g}" if below else ""))
    return float(v2)


def _rows(path: Path, required: list[str], optional: list[str] = ()):
    """(line, {column: value}) for each non-blank row, after checking the header."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = [c.strip() for c in reader.fieldnames or []]
        if missing := set(required) - set(cols):
            raise PipelineStop(f"{path.name}: missing column(s) {', '.join(sorted(missing))}; "
                               f"the first line should be {','.join(required)}")
        if unknown := set(cols) - set(required) - set(optional):
            raise PipelineStop(f"{path.name}: unknown column(s) {', '.join(sorted(unknown))}; "
                               f"allowed: {', '.join([*required, *optional])}")
        for raw in reader:
            where = f"{path.name} line {reader.line_num}"
            if any((v or "").strip() for v in raw.get(None, [])):
                raise PipelineStop(f"{where}: more values than the header has columns")
            row = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
            if any(row.values()):
                yield where, row


def _bad(where: str):
    return lambda msg: PipelineStop(f"{where}: {msg}")


def _once(seen: set, key, where: str, what: str) -> None:
    if key in seen:
        raise PipelineStop(f"{where}: {what} appears twice")
    seen.add(key)


def load_tbill(path: Path) -> pd.Series:
    """364-day Treasury bill yields (CBSL weekly auctions) as fractions, by date."""
    if not path.exists():
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    out, seen = {}, set()
    for where, r in _rows(path, ["date", "yield"]):
        bad = _bad(where)
        d = _date(r["date"], bad)
        _once(seen, d, where, r["date"])
        out[d] = _positive(r["yield"], bad, "yield (in percent, e.g. 8.75)", below=100) / 100
    return pd.Series(out, dtype=float).sort_index()


def load_ccpi(path: Path) -> pd.Series:
    """Colombo Consumer Price Index by month (the first of the month as the date)."""
    if not path.exists():
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    out, seen = {}, set()
    for where, r in _rows(path, ["month", "index"]):
        bad = _bad(where)
        m = _month(r["month"], bad)
        _once(seen, m, where, r["month"])
        out[m] = _positive(r["index"], bad, "index")
    return pd.Series(out, dtype=float).sort_index()


def load_fx(path: Path) -> pd.Series:
    """Rupees per US dollar (CBSL), by date."""
    if not path.exists():
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    out, seen = {}, set()
    for where, r in _rows(path, ["date", "lkr_per_usd"]):
        bad = _bad(where)
        d = _date(r["date"], bad)
        _once(seen, d, where, r["date"])
        out[d] = _positive(r["lkr_per_usd"], bad, "lkr_per_usd")
    return pd.Series(out, dtype=float).sort_index()


RESERVED = {"ASPI", "SPSL20"}  # stored by `smpa prices`


def load_indices(path: Path) -> pd.DataFrame:
    """name, date, value: index levels you type in (e.g. ASTRI month-ends), under their own names."""
    cols = ["name", "date", "value"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    rows, seen = [], set()
    for where, r in _rows(path, cols):
        bad = _bad(where)
        name = r["name"]
        if not name or name.upper() in RESERVED or name.startswith("sector:"):
            raise bad(f"name {name!r}: use your own name (ASPI, SPSL20 and sector:… are stored by smpa prices)")
        d = _date(r["date"], bad)
        _once(seen, (name, d), where, f"{name} on {r['date']}")
        rows.append({"name": name, "date": d, "value": _positive(r["value"], bad, "value")})
    return pd.DataFrame(rows, columns=cols)


def load_sectors(path: Path) -> dict[str, str]:
    """symbol -> sector: one of the S&P/CSE industry groups (as allSectors names them, e.g. Banks)."""
    if not path.exists():
        return {}
    out = {}
    for where, r in _rows(path, ["symbol", "sector"]):
        bad = _bad(where)
        sym = r["symbol"].upper()
        if not re.fullmatch(SYMBOL, sym):
            raise bad(f"symbol {r['symbol']!r} should look like JKH.N0000")
        if not r["sector"]:
            raise bad("sector is empty")
        _once(set(out), sym, where, sym)
        out[sym] = r["sector"]
    return out


def load_journal(path: Path) -> pd.DataFrame:
    """contract_no, reason, target, stop, note: why you made a trade, written down at the time."""
    cols = ["contract_no", "reason", "target", "stop", "note"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    rows, seen = [], set()
    for where, r in _rows(path, ["contract_no", "reason"], ["target", "stop", "note"]):
        bad = _bad(where)
        if not r["contract_no"]:
            raise bad("contract_no is empty")
        _once(seen, r["contract_no"], where, f"contract {r['contract_no']}")
        rows.append({"contract_no": r["contract_no"], "reason": r["reason"],
                     "target": _positive(r["target"], bad, "target") if r.get("target") else None,
                     "stop": _positive(r["stop"], bad, "stop") if r.get("stop") else None, "note": r.get("note", "")})
    return pd.DataFrame(rows, columns=cols)


def at_or_before(s: pd.Series, d, max_days: int | None = None) -> float | None:
    """The value of a dated series on or before `d` (None before it starts, or if older than max_days)."""
    if s.empty:
        return None
    x = s[: pd.Timestamp(d)]
    if x.empty or (max_days is not None and (pd.Timestamp(d) - x.index[-1]).days > max_days):
        return None
    return float(x.iloc[-1])
