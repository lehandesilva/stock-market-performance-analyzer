"""NDB Contract Notes ("BOUGHT Note" / "SOLD Note"): the legal record of each trade."""

from __future__ import annotations

import re
from decimal import Decimal

from ..models import FEE_FIELDS, ParsedDocument, ParseError, TradeRecord
from .common import CONTRACT, MONEY, PRICE, QTY, SYMBOL, YMD, Lines, dec, fail, qty, ymd

# Column header -> fee columns in the order they appear.
LAYOUTS = {
    "Contract Qty Security Rate Gross Brokerage CDS Fees CSE Fees SEC STL Amount in RS.":
        ("v1", ("brokerage", "cds", "cse", "sec", "stl")),
    "Contract No Qty Security Rate Gross Value Brokerage CDS Fees CSE Fees SEC STL "
    "Clearing Fee Foreign Br.. Amount in RS.":
        ("v2", ("brokerage", "cds", "cse", "sec", "stl", "clearing", "foreign_brokerage")),
}
PAGE_TOTAL = re.compile(rf"^PageTotal ({MONEY})$")
GRAND_TOTAL = re.compile(rf"^Total ({MONEY})$")


def _row_re(n_fees: int) -> re.Pattern:
    fees = "".join(rf" ({MONEY})" for _ in range(n_fees))
    return re.compile(rf"^({CONTRACT}) ({QTY}) ({SYMBOL}) ({PRICE}) ({MONEY}){fees} ({MONEY})$")


def parse(text: str) -> ParsedDocument:
    lines = Lines(text)
    side_word = lines.search(r"\b(BOUGHT|SOLD) ?Note\b", "BOUGHT/SOLD Note title").group(1)
    side = "BUY" if side_word == "BOUGHT" else "SELL"
    lines.search(rf"^{'Bought' if side == 'BUY' else 'Sold'} by order and for account", "order wording")
    account = lines.search(r"Account No (\S+)", "account number").group(1)
    trade_dates = {m for _, l in lines for m in re.findall(rf"Transaction Date ({YMD})", l)}
    settle_dates = {m for _, l in lines for m in re.findall(rf"Settlement Date ({YMD})", l)}
    if len(trade_dates) != 1 or len(settle_dates) != 1:
        raise ParseError(f"expected one transaction/settlement date, got {trade_dates} / {settle_dates}")
    trade_date, settle_date = ymd(trade_dates.pop()), ymd(settle_dates.pop())
    n_pages = int(lines.search(r"Page No: ?\d+ of (\d+)", "page count").group(1))

    layout = None
    trades: list[TradeRecord] = []
    page_totals: list[Decimal] = []
    grand_total = None
    items = list(lines)
    k = 0
    while k < len(items):
        no, line = items[k]
        if line.startswith("Contract ") and "Amount in RS." in line:
            if line not in LAYOUTS:
                raise fail("unknown Contract Note column layout", items[k])
            this = LAYOUTS[line]
            if layout and this != layout:
                raise fail("column layout changes between pages", items[k])
            layout = this
            row_re = _row_re(len(layout[1]))
            page_net = Decimal(0)
            k += 1
            # Inside the table every line must be a trade row until PageTotal.
            while True:
                if k >= len(items):
                    raise ParseError("table has no PageTotal line")
                if m := PAGE_TOTAL.match(items[k][1]):
                    if dec(m.group(1)) != page_net:
                        raise fail(f"rows add up to {page_net}, PageTotal says {m.group(1)}", items[k])
                    page_totals.append(page_net)
                    break
                m = row_re.match(items[k][1])
                if not m:
                    raise fail("unexpected line inside the trade table", items[k])
                g = m.groups()
                fee_values = dict.fromkeys(FEE_FIELDS, Decimal(0))
                fee_values.update(zip(layout[1], map(dec, g[5:-1])))
                t = TradeRecord(
                    contract_no=g[0], trade_date=trade_date, side=side, symbol=g[2],
                    quantity=qty(g[1]), price=dec(g[3]), gross=dec(g[4]), net=dec(g[-1]),
                    settlement_date=settle_date, fees=fee_values,
                )
                if problems := t.problems():
                    raise fail("; ".join(problems), items[k])
                trades.append(t)
                page_net += t.net
                k += 1
        elif m := GRAND_TOTAL.match(line):
            if grand_total is not None:
                raise fail("more than one Total line", items[k])
            grand_total = dec(m.group(1))
        k += 1

    if layout is None:
        raise ParseError("no Contract Note trade table found")
    if len(page_totals) != n_pages:
        raise ParseError(f"document says {n_pages} page(s) but has {len(page_totals)} trade table(s)")
    if grand_total is None or grand_total != sum(page_totals, Decimal(0)):
        raise ParseError(f"page totals add up to {sum(page_totals, Decimal(0))}, Total says {grand_total}")
    return ParsedDocument("contract_note", f"contract_note/{layout[0]}", account, trades=trades)
