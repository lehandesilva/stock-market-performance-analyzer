"""NDB Trade Confirmations: one per trading day, purchases and sales grouped by security."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ..models import FEE_FIELDS, ParsedDocument, ParseError, TradeRecord
from .common import CONTRACT, DMY, ISIN, MONEY, PRICE, QTY, SYMBOL, Lines, dec, dmy, fail, qty

M, P, Q = rf"({MONEY})", rf"({PRICE})", rf"({QTY})"


@dataclass(frozen=True)
class Layout:
    name: str  # month this layout was first seen
    fees: tuple[str, ...]  # fee columns in order, between gross and net
    row_dates: bool  # each row carries its own trade and settlement dates
    foreign: bool  # trailing Foreign Brokerage column
    net_settlement: bool  # footer has a "Net Settlement Value" line
    header_continuation: str | None = None  # second header line printed below the main one

    @property
    def row(self) -> re.Pattern:
        # [trade-date] contract qty price gross | fees | net [settlement-date] [foreign-brokerage]
        return re.compile(
            ("^" + rf"({DMY}) " if self.row_dates else "^") + rf"({CONTRACT}) {Q} {P} {M}"
            + rf" {M}" * len(self.fees) + rf" {M}" + (rf" ({DMY})" if self.row_dates else "")
            + (rf" {M}" if self.foreign else "") + "$"
        )

    @property
    def total(self) -> re.Pattern:
        # Total qty avg-price gross | fees | net [foreign-brokerage]
        return re.compile(rf"^Total {Q} {P} {M}" + rf" {M}" * len(self.fees) + rf" {M}"
                          + (rf" {M}" if self.foreign else "") + "$")


FIVE_FEES = ("brokerage", "sec", "cse", "cds", "stl")
LAYOUTS = {
    "Contract No No of Shares Price/Avg Gross Amount Brokerage SEC CSE CDS GOV CESS Net Amount":
        Layout("2021-05", FIVE_FEES, row_dates=False, foreign=False, net_settlement=False),
    "Trade Date Contract No No of Shares Price/Avg Gross Amount Brokerage SEC CSE CDS GOV CESS Net Amount "
    "Settlement Date":
        Layout("2021-07", FIVE_FEES, row_dates=True, foreign=False, net_settlement=False),
    "Trade Date Contract No No of Shares Price/Avg Gross Amount Brokerage SEC CSE CDS GOV CESS Net Amount "
    "Settlement":
        Layout("2025-03", FIVE_FEES, row_dates=True, foreign=True, net_settlement=True,
               header_continuation="Brokerage"),
    "Trade Date Contract No No of Shares Price/Avg Gross Amount Brokerage SEC Exchange CDS GOV CESS Net Amount "
    "Settlement":  # CSE column renamed "Exchange"
        Layout("2025-05", FIVE_FEES, row_dates=True, foreign=True, net_settlement=True,
               header_continuation="Brokerage"),
    "Trade Date Contract No No of Shares Price/Avg Gross Amount Brokerage SEC Exchange CDS GOV CESS "
    "Clearing Fees Net Amount Settlement Date":
        Layout("2026-09", (*FIVE_FEES, "clearing"), row_dates=True, foreign=True, net_settlement=True,
               header_continuation="Brokerage"),
}
# The ISIN can be blank, e.g. "( COOP.N0000 / )".
SECTION = re.compile(rf"^(Purchase|Sale) of (.+) \( ({SYMBOL}) / (?:({ISIN}) )?\)$")
RULE = re.compile(r"^(-+|=+)( (-+|=+))*$")
PURCHASE_TOTAL = re.compile(rf"^Purchase Total {M}$")


def _total_check(section: list[TradeRecord], g: tuple[str, ...], layout: Layout) -> list[str]:
    """The per-security Total row must equal the sum of its rows."""
    vals = list(map(dec, g[1:]))
    n = len(layout.fees)
    exp_q = sum(t.quantity for t in section)
    exp = {
        "gross": sum((t.gross for t in section), Decimal(0)),
        "net": sum((t.net for t in section), Decimal(0)),
        **{f: sum((t.fees[f] for t in section), Decimal(0)) for f in (*layout.fees, "foreign_brokerage")},
    }
    got = {"gross": vals[1], **dict(zip(layout.fees, vals[2:2 + n])), "net": vals[2 + n]}
    if layout.foreign:
        got["foreign_brokerage"] = vals[3 + n]
    out = [f"Total {k} is {got[k]}, rows add up to {exp[k]}" for k in got if got[k] != exp[k]]
    if qty(g[0]) != exp_q:
        out.append(f"Total quantity is {g[0]}, rows add up to {exp_q}")
    elif exp_q and abs(vals[0] - exp["net"] / exp_q) > Decimal("0.0001"):
        out.append(f"Total Price/Avg {vals[0]} != net / quantity")
    return out


def parse(text: str) -> ParsedDocument:
    lines = Lines(text)
    account = lines.search(r"^Client A/C No\. : (\S+)$", "client account number").group(1)
    trade_date = dmy(lines.search(rf"^Trade Date : ({DMY})$", "trade date").group(1))

    items = list(lines)
    start = next((k for k, (_, l) in enumerate(items) if l in LAYOUTS), None)
    if start is None:
        hdr = next((it for it in items if it[1].startswith(("Contract No", "Trade Date Contract"))), None)
        raise fail("unknown Trade Confirmation column layout", hdr) if hdr else ParseError(
            "no Trade Confirmation table header found")
    layout = LAYOUTS[items[start][1]]

    settle_header = None
    if not layout.row_dates:
        settle_header = dmy(lines.search(rf"^Settlement Date : ({DMY})$", "settlement date").group(1))

    trades: list[TradeRecord] = []
    section: list[TradeRecord] | None = None
    side = symbol = isin = company = None
    k = start + 1
    if layout.header_continuation:
        if k >= len(items) or items[k][1] != layout.header_continuation:
            raise fail(f"expected header continuation {layout.header_continuation!r}", items[min(k, len(items) - 1)])
        k += 1
    while True:
        if k >= len(items):
            raise ParseError("table has no 'Purchase Total' line")
        item = items[k]
        line = item[1]
        if PURCHASE_TOTAL.match(line):
            if section is not None:
                raise fail("security section has no Total row", item)
            break
        if m := SECTION.match(line):
            if section is not None:
                raise fail("previous security section has no Total row", item)
            side = "BUY" if m.group(1) == "Purchase" else "SELL"
            company, symbol, isin = m.group(2), m.group(3), m.group(4)
            section = []
        elif RULE.match(line):
            pass
        elif (m := layout.total.match(line)) and section is not None:
            if not section:
                raise fail("Total row without trade rows", item)
            if problems := _total_check(section, m.groups(), layout):
                raise fail("; ".join(problems), item)
            trades += section
            section = None
        elif (m := layout.row.match(line)) and section is not None:
            g = list(m.groups())
            foreign = dec(g.pop()) if layout.foreign else Decimal(0)
            settle = settle_header
            if layout.row_dates:
                row_date, settle = dmy(g.pop(0)), dmy(g.pop())
                if row_date != trade_date:
                    raise fail(f"row trade date {row_date} differs from header {trade_date}", item)
            fees = dict.fromkeys(FEE_FIELDS, Decimal(0))
            fees.update(zip(layout.fees, map(dec, g[4:-1])))
            fees["foreign_brokerage"] = foreign
            t = TradeRecord(
                contract_no=g[0], trade_date=trade_date, side=side, symbol=symbol, quantity=qty(g[1]),
                price=dec(g[2]), gross=dec(g[3]), net=dec(g[-1]), settlement_date=settle, fees=fees,
                isin=isin, company=company,
            )
            if problems := t.problems():
                raise fail("; ".join(problems), item)
            section.append(t)
        else:
            raise fail("unexpected line inside the trade table", item)
        k += 1

    buys = sum((t.net for t in trades if t.side == "BUY"), Decimal(0))
    sells = sum((t.net for t in trades if t.side == "SELL"), Decimal(0))
    checks = [("Purchase Total", buys), ("Sales Total", sells)]
    if layout.net_settlement:
        checks.append(("Net Settlement Value", buys - sells))
    for label, expected in checks:
        got = dec(lines.search(rf"^{label} {M}$", label).group(1))
        if got != expected:
            raise ParseError(f"{label} is {got}, trades add up to {expected}")
    return ParsedDocument("trade_confirmation", f"trade_confirmation/{layout.name}", account, trades=trades)
