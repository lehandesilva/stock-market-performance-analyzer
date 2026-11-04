"""Heuristic parser for trade lines in broker statements / contract notes.

A line is accepted as a trade only if it has a side (BUY/SELL, or sits under a
"Purchases"/"Sales" heading), a ticker-like symbol, and three numbers where
quantity x price ~= amount. That consistency check keeps OCR noise out.
Lines without their own date inherit the last date seen in the document
(e.g. a "Trade Date:" header), then the email date.

This is a starting point - write a broker-specific parser once you've looked at
the extracted text in data/text/.
"""

from __future__ import annotations

import re
from datetime import date

from dateutil import parser as dateparser

from . import ParseContext, Transaction

_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_RE = re.compile(
    "|".join(
        [
            r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b",  # 2024-03-12
            r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b",  # 12/03/2024
            rf"\b\d{{1,2}}[- ]{_MON}[- ,]+\d{{2,4}}\b",  # 12-Mar-2024, 12 March 2024
            rf"\b{_MON} \d{{1,2}},? \d{{4}}\b",  # March 12, 2024
        ]
    ),
    re.IGNORECASE,
)

SIDES = {
    "BUY": "BUY", "BOUGHT": "BUY", "PURCHASE": "BUY", "PURCHASED": "BUY", "PURCHASES": "BUY",
    "SELL": "SELL", "SOLD": "SELL", "SALE": "SELL", "SALES": "SELL",
}
SINGLE_LETTER_SIDES = {"B": "BUY", "S": "SELL"}  # only as standalone uppercase tokens

STOPWORDS = {
    *SIDES, *SINGLE_LETTER_SIDES,
    "USD", "LKR", "INR", "EUR", "GBP", "AUD", "CAD", "SGD", "NZD", "HKD", "JPY",
    "TOTAL", "SUBTOTAL", "SUB", "NET", "GROSS", "AMOUNT", "PRICE", "QTY", "QUANTITY", "VALUE",
    "FEE", "FEES", "TAX", "VAT", "WHT", "SEC", "CSE", "CDS", "SEBI", "STT", "GST", "STAMP",
    "DUTY", "BROKERAGE", "COMMISSION", "DATE", "TRADE", "SETTLEMENT", "BALANCE", "CREDIT",
    "DEBIT", "CR", "DR", "REF", "NO", "ACCOUNT", "PAGE", "CONTRACT", "NOTE", "STATEMENT",
    "PLC", "LTD", "LIMITED", "INC", "CORP", "CO", "AND", "THE", "OF", "AT", "ON", "TO",
    "BROUGHT", "FORWARD", "CARRIED", "OPENING", "CLOSING", "ID", "NIC",
}

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9&]{0,11}(?:[.\-][A-Z0-9]{1,6})?$")
NUMBER_RE = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?$")
SPACED_COMMA_RE = re.compile(r"(\d) ?, ?(\d{3})\b")  # OCR: "40 , 737.60" -> "40,737.60"
OCR_DIGITS = str.maketrans("OQDIL", "00011")


def _fix_symbol(sym: str) -> str:
    """OCR often reads 0 as O/Q in suffixes like JKH.N0000 -> JKH.NOQ00; repair those."""
    base, dot, suffix = sym.partition(".")
    if dot and len(suffix) > 1:
        tail = suffix[1:].translate(OCR_DIGITS)
        if tail.isdigit():
            return f"{base}.{suffix[0]}{tail}"
    return sym


def _parse_date(s: str, dayfirst: bool) -> date | None:
    iso = bool(re.match(r"\d{4}", s))  # 2024-03-12 is always Y-M-D
    try:
        d = dateparser.parse(s, dayfirst=dayfirst and not iso, yearfirst=iso).date()
    except (ValueError, OverflowError):
        return None
    return d if 1990 <= d.year <= 2100 else None


def _to_number(tok: str) -> float:
    return abs(float(tok.strip("()").replace(",", "")))


def _match_amounts(nums: list[float]) -> tuple[float, float, float, int] | None:
    """Find (qty, price, gross, index_of_gross) with qty * price ~= gross among ordered numbers."""
    best = None
    for k in range(2, len(nums)):
        g = nums[k]
        if g <= 0:
            continue
        for i in range(k - 1):
            for j in range(i + 1, k):
                a, b = nums[i], nums[j]
                if a <= 0 or b <= 0:
                    continue
                # Prefer the integer one as quantity; otherwise assume qty comes first.
                q, p = (b, a) if (a != int(a) and b == int(b)) else (a, b)
                err = abs(q * p - g)
                tol = 0.006 * q + 0.0001 * g + 0.01  # allows price rounded to 2dp
                if err <= tol and (best is None or err / g < best[0]):
                    best = (err / g, q, p, g, k)
    if best is None:
        return None
    _, q, p, g, k = best
    return q, p, g, k


def _fees(nums: list[float], k: int, gross: float, side: str) -> float:
    """Look right of the gross amount for a net amount within 5% of it."""
    for m in nums[k + 1 : k + 4]:
        if side == "BUY" and gross < m <= gross * 1.05:
            return round(m - gross, 2)
        if side == "SELL" and gross * 0.95 <= m < gross:
            return round(gross - m, 2)
    return 0.0


def parse_line(line: str, ctx: ParseContext, current_date: date | None,
               section_side: str | None) -> Transaction | None:
    clean = SPACED_COMMA_RE.sub(r"\1,\2", line.replace("|", " "))
    dates = [_parse_date(m.group(), ctx.dayfirst) for m in DATE_RE.finditer(clean)]
    dates = [d for d in dates if d]
    tokens = DATE_RE.sub(" ", clean).split()

    side = None
    symbols: list[str] = []
    nums: list[float] = []
    for tok in tokens:
        t = tok.strip(":;,@*")
        up = t.upper()
        if side is None and (up in SIDES or t in SINGLE_LETTER_SIDES):
            side = SIDES.get(up) or SINGLE_LETTER_SIDES[t]
        elif NUMBER_RE.match(t):
            nums.append(_to_number(t))
        elif SYMBOL_RE.match(t) and up not in STOPWORDS:
            symbols.append(t)

    side = side or section_side
    trade_date = dates[0] if dates else current_date or ctx.fallback_date
    if not (side and symbols and trade_date and len(nums) >= 3):
        return None
    match = _match_amounts(nums)
    if not match:
        return None
    q, p, g, k = match
    # Prefer exchange-style symbols (e.g. JKH.N0000) over plain words.
    symbol = _fix_symbol(next((s for s in symbols if "." in s), symbols[0]))
    return Transaction(
        trade_date=trade_date, side=side, symbol=symbol, quantity=q, price=p,
        gross_amount=g, fees=_fees(nums, k, g, side), raw_line=line.strip(),
    )


def parse(text: str, ctx: ParseContext) -> list[Transaction]:
    txs: list[Transaction] = []
    current_date: date | None = None
    section_side: str | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        tx = parse_line(line, ctx, current_date, section_side)
        if tx:
            txs.append(tx)
            continue
        # Not a trade: track context for following lines.
        for m in DATE_RE.finditer(line):
            if d := _parse_date(m.group(), ctx.dayfirst):
                current_date = d
                break
        words = line.split()
        if len(words) <= 4:
            for w in words:
                if (s := SIDES.get(w.strip(":").upper())):
                    section_side = s
                    break
    return txs
