"""Shared helpers for NDB Securities documents."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

from ..models import ParseError

BROKER_MARKER = "NDB SECURITIES (PVT) LTD."

# Token patterns (pdfplumber output is single-space separated).
# Integer part with or without thousands separators: Contract Notes print 1000, the others 1,000.
_INT = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
MONEY = rf"-?{_INT}?\.\d{{2}}"  # 1,234.56 | 1234.56 | 0.05 | .55 (2021 contract notes)
PRICE = rf"{_INT}?\.\d{{2,4}}"  # 6.70 | 6.7000 | .40
QTY = _INT  # 745 | 1000 | 1,000
SYMBOL = r"[A-Z][A-Z0-9&]*\.[A-Z]\d{4}"  # JKH.N0000
ISIN = r"LK[0-9A-Z]{10}"
CONTRACT = r"\d{6,}"
DMY = r"\d{2}/\d{2}/\d{4}"
YMD = r"\d{4}/\d{2}/\d{2}"


def dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def qty(s: str) -> int:
    return int(s.replace(",", ""))


def dmy(s: str) -> date:
    return datetime.strptime(s, "%d/%m/%Y").date()


def mdy(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def ymd(s: str) -> date:
    return datetime.strptime(s, "%Y/%m/%d").date()


def norm(line: str) -> str:
    return " ".join(line.split())


class Lines:
    """Document text as numbered, whitespace-normalised lines (blank lines dropped)."""

    def __init__(self, text: str):
        self.items = [
            (i, norm(raw))
            for i, raw in enumerate(text.replace("\f", "\n").splitlines(), start=1)
            if raw.strip()
        ]

    def __iter__(self) -> Iterator[tuple[int, str]]:
        return iter(self.items)

    def search(self, pattern: str, what: str) -> re.Match:
        """First match of `pattern` in any line; ParseError if absent."""
        rx = re.compile(pattern)
        for _, line in self.items:
            if m := rx.search(line):
                return m
        raise ParseError(f"could not find {what}")

    def index_of(self, exact: str, start: int = 0) -> int | None:
        for k in range(start, len(self.items)):
            if self.items[k][1] == exact:
                return k
        return None


def fail(message: str, item: tuple[int, str]) -> ParseError:
    return ParseError(message, item[0], item[1])
