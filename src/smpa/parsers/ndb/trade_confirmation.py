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
    name: str
    fees: tuple[str, ...]  # fee columns in order, between gross and net
    row: re.Pattern
    total: re.Pattern
    header_continuation: str | None  # second header line printed below the main one


LAYOUTS = {
    "Contract No No of Shares Price/Avg Gross Amount Brokerage SEC CSE CDS GOV CESS Net Amount": Layout(
        "v1", ("brokerage", "sec", "cse", "cds", "stl"),
        # contract qty price gross | 5 fees | net
        re.compile(rf"^({CONTRACT}) {Q} {P} {M}" + rf" {M}" * 5 + rf" {M}$"),
        re.compile(rf"^Total {Q} {P} {M}" + rf" {M}" * 5 + rf" {M}$"),
        None,
    ),
    "Trade Date Contract No No of Shares Price/Avg Gross Amount Brokerage SEC Exchange CDS GOV CESS "
    "Clearing Fees Net Amount Settlement Date": Layout(
        "v2", ("brokerage", "sec", "cse", "cds", "stl", "clearing"),
        # trade-date contract qty price gross | 6 fees | net settlement-date foreign-brokerage
        re.compile(rf"^({DMY}) ({CONTRACT}) {Q} {P} {M}" + rf" {M}" * 6 + rf" {M} ({DMY}) {M}$"),
        re.compile(rf"^Total {Q} {P} {M}" + rf" {M}" * 6 + rf" {M} {M}$"),
        "Brokerage",
    ),
}
SECTION = re.compile(rf"^(Purchase|Sale) of (.+) \( ({SYMBOL}) / ({ISIN}) \)$")
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
    if layout.name == "v2":
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
    if layout.name == "v1":
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
            if layout.name == "v2":
                row_date, g = dmy(g[0]), g[1:]
                settle, foreign = dmy(g[-2]), dec(g[-1])
                g = g[:-2]
                if row_date != trade_date:
                    raise fail(f"row trade date {row_date} differs from header {trade_date}", item)
            else:
                settle, foreign = settle_header, Decimal(0)
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
    if layout.name == "v2":
        checks.append(("Net Settlement Value", buys - sells))
    for label, expected in checks:
        got = dec(lines.search(rf"^{label} {M}$", label).group(1))
        if got != expected:
            raise ParseError(f"{label} is {got}, trades add up to {expected}")
    return ParsedDocument("trade_confirmation", f"trade_confirmation/{layout.name}", account, trades=trades)
