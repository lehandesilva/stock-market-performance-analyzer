"""Records produced by statement parsers, and the errors they raise."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# Every fee the broker has charged across layouts. Layouts that lack a column
# (e.g. clearing fees before they were introduced) record it as 0.
FEE_FIELDS = ("brokerage", "cds", "cse", "sec", "stl", "clearing", "foreign_brokerage")
CENT = Decimal("0.01")


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


class ParseError(Exception):
    """A document doesn't match any known format, or fails its own checks."""

    def __init__(self, message: str, line_no: int | None = None, line: str | None = None):
        self.message, self.line_no, self.line = message, line_no, line
        where = f" (line {line_no}: {line.strip()!r})" if line_no is not None and line else ""
        super().__init__(message + where)


class ReconcileError(Exception):
    """Two documents disagree about the same trade or cash movement."""


@dataclass
class TradeRecord:
    contract_no: str
    trade_date: date
    side: str  # "BUY" | "SELL"
    symbol: str
    quantity: int
    price: Decimal
    gross: Decimal
    net: Decimal  # BUY: cash paid. SELL: cash received.
    settlement_date: date | None = None
    fees: dict[str, Decimal] | None = None  # None when only the total is known (statements)
    isin: str | None = None
    company: str | None = None

    @property
    def fees_total(self) -> Decimal:
        if self.fees is not None:
            return sum(self.fees.values(), Decimal(0))
        return self.net - self.gross if self.side == "BUY" else self.gross - self.net

    def problems(self) -> list[str]:
        """Arithmetic checks every trade line must pass."""
        out = []
        if self.quantity <= 0:
            out.append(f"quantity must be positive, got {self.quantity}")
        if cents(self.quantity * self.price) != self.gross:
            out.append(f"quantity x price = {cents(self.quantity * self.price)} but gross is {self.gross}")
        if self.fees is not None:
            fees = self.fees_total
            expected = self.gross + fees if self.side == "BUY" else self.gross - fees
            if expected != self.net:
                op = "+" if self.side == "BUY" else "-"
                out.append(f"gross {op} fees = {expected} but net is {self.net}")
        elif self.fees_total < 0:
            out.append(f"net {self.net} implies negative fees for a {self.side}")
        return out


@dataclass
class CashRecord:
    ref: str  # receipt number, e.g. RCT202103000001
    date: date
    kind: str  # "DEPOSIT"
    amount: Decimal
    description: str = ""


@dataclass
class ParsedDocument:
    doc_type: str  # contract_note | trade_confirmation | statement | receipt
    layout: str  # e.g. "contract_note/v2"
    account: str
    trades: list[TradeRecord] = field(default_factory=list)
    cash: list[CashRecord] = field(default_factory=list)
