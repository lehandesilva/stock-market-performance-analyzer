"""NDB "Statement of Accounts": trades (B/S), deposits (R) and payments to you (PV), with a running balance."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from ..models import CashRecord, ParsedDocument, ParseError, TradeRecord, cents
from .common import CONTRACT, DMY, MONEY, PRICE, QTY, SYMBOL, Lines, dec, dmy, fail, mdy, qty

HEADER = "Transaction Type Doc Ref No Description Quantity Price Amount Cumulative Settlement"
HEADER_2 = "Date Balance Date"
BAL = rf"({MONEY})( Cr| Dr)?"
ROW = re.compile(rf"^({DMY}) ([A-Z]{{1,3}}) (\S+) (.+) ({QTY}) ({PRICE}) {BAL} {BAL} ({DMY})$")
EDGE = re.compile(rf"^(Opening|Closing) balance as at (\S+) 0 0\.00 {BAL}$")
SUMMARY_LINE = re.compile(rf"^(.+?) ({MONEY})$")
DESC_SYMBOL = re.compile(rf"^(?:(.+)\()?({SYMBOL})\)?$")  # "HAYCARB PLC(HAYC.N0000)" | "DIST.N0000"

SUMMARY_LAYOUTS = {
    ("Balance B/F", "Total Purchases", "Total Sales", "Total Receipt", "Total Payments", "Total Interest",
     "Total Penalty Wave Off", "Transfers", "Total Journal Transfers (IN)", "Total Journal Transfers (Out)",
     "Total Transaction Cost/Purchases", "Total Transaction Cost/Sales", "Total Transaction Cost",
     "Settlement Balance", "Total Balance"): "2021-05",
    ("Balance B/F", "Total Purchases", "Total Sales", "Total Receipt", "Cancelled Receipts", "Total Payments",
     "Cancelled Payments", "Total Penalty Charged", "Total Penalty Wave Off", "Transfers",
     "Total Journal Transfers (IN)", "Total Journal Transfers (Out)", "Total Other Debits (Dr)",
     "Total Other Credits (Cr)", "Total Transaction Cost/Purchases", "Total Transaction Cost/Sales",
     "Total Transaction Cost", "Settlement Balance", "Total Balance"): "2026-10",
}
# Summary figures we check against the rows; everything else must be zero until
# we've seen (and added support for) a statement where it isn't.
CHECKED = {"Balance B/F", "Total Purchases", "Total Sales", "Total Receipt", "Total Payments", "Total Transaction Cost/Purchases",
           "Total Transaction Cost/Sales", "Total Transaction Cost", "Settlement Balance", "Total Balance"}


def _signed(amount: str, suffix: str | None) -> Decimal:
    """Credit-positive balance convention: 'Cr' is money held for you."""
    return dec(amount) if suffix == " Cr" else -dec(amount)


def _period(start: str, end: str, row_dates: list[date]) -> tuple[date, date, str]:
    """Statement headers use DD/MM/YYYY in older layouts and MM/DD/YYYY in newer ones."""
    options = []
    for name, fn in (("dmy", dmy), ("mdy", mdy)):
        try:
            a, b = fn(start), fn(end)
        except ValueError:
            continue
        if a <= b <= a + timedelta(days=31) and all(a <= d <= b for d in row_dates):
            options.append((a, b, name))
    if len(options) != 1:
        raise ParseError(f"cannot determine statement period date format for {start} to {end}")
    return options[0]


def parse(text: str) -> ParsedDocument:
    lines = Lines(text)
    m = lines.search(r"^Statement of Accounts From (\S+) to (\S+)$", "statement period")
    period_raw = m.groups()
    account = lines.search(r"Client A/C No : (\S+)$", "client account number").group(1)

    items = list(lines)
    start = lines.index_of(HEADER)
    if start is None:
        raise ParseError("no statement table header found")
    if start + 1 >= len(items) or items[start + 1][1] != HEADER_2:
        raise fail("unexpected statement header layout", items[start])

    rows: list[dict] = []
    opening = closing = None
    k = start + 2
    continued = False
    while True:
        if k >= len(items):
            raise ParseError("statement has no closing balance line")
        item = items[k]
        line = item[1]
        if m := EDGE.match(line):
            if m.group(1) == "Opening":
                if opening is not None or rows:
                    raise fail("opening balance must come first, once", item)
                opening = (m.group(2), _signed(m.group(3), m.group(4)))
            else:
                closing = (m.group(2), _signed(m.group(3), m.group(4)))
                break
        elif m := ROW.match(line):
            if opening is None:
                raise fail("row before opening balance", item)
            g = m.groups()
            rows.append({"item": item, "date": dmy(g[0]), "type": g[1], "ref": g[2], "desc": g[3],
                         "qty": qty(g[4]), "price": dec(g[5]), "amount": dec(g[6]),
                         "signed": _signed(g[6], g[7]), "balance": _signed(g[8], g[9]), "settle": dmy(g[10])})
            continued = False
        elif line == HEADER and k + 1 < len(items) and items[k + 1][1] == HEADER_2:
            k += 1  # header repeated on a new page
        elif re.match(rf"{DMY} ", line):
            raise fail("unrecognised statement row", item)
        elif rows and not continued:
            rows[-1]["desc"] += " " + line  # long descriptions wrap onto a second line
            continued = True
        else:
            raise fail("unexpected line in statement table", item)
        k += 1

    period_start, period_end, fmt = _period(*period_raw, [r["date"] for r in rows])
    as_at = dmy if fmt == "dmy" else mdy
    if as_at(opening[0]) != period_start or as_at(closing[0]) != period_end:
        raise ParseError("opening/closing balance dates don't match the statement period")

    trades: list[TradeRecord] = []
    cash: list[CashRecord] = []
    balance = opening[1]
    tx_cost = {"B": Decimal(0), "S": Decimal(0)}
    totals = {"B": Decimal(0), "S": Decimal(0), "R": Decimal(0), "PV": Decimal(0)}
    for r in rows:
        item = r["item"]
        balance += r["signed"]
        if balance != r["balance"]:
            raise fail(f"running balance should be {balance}, statement says {r['balance']}", item)
        if r["settle"] < r["date"]:
            raise fail("settlement date before transaction date", item)
        if r["type"] in ("B", "S"):
            sm = DESC_SYMBOL.match(r["desc"])
            if not (sm and re.fullmatch(CONTRACT, r["ref"])):
                raise fail("can't read symbol / contract number from trade row", item)
            if (r["type"] == "B") != (r["signed"] < 0):
                raise fail("trade amount has the wrong debit/credit side", item)
            t = TradeRecord(
                contract_no=r["ref"], trade_date=r["date"], side="BUY" if r["type"] == "B" else "SELL",
                symbol=sm.group(2), quantity=r["qty"], price=r["price"], gross=cents(r["qty"] * r["price"]),
                net=r["amount"], settlement_date=r["settle"], company=(sm.group(1) or "").strip() or None,
            )
            if problems := t.problems():
                raise fail("; ".join(problems), item)
            trades.append(t)
            tx_cost[r["type"]] += t.fees_total
        elif r["type"] == "R":
            if not (re.fullmatch(r"RCT\d+", r["ref"]) and r["signed"] > 0 and r["qty"] == 0):
                raise fail("unexpected receipt row", item)
            cash.append(CashRecord(r["ref"], r["date"], "DEPOSIT", r["amount"], r["desc"]))
        elif r["type"] == "PV":  # payment voucher: money paid out to your bank account
            if not (re.fullmatch(r"PVN\d+", r["ref"]) and r["signed"] < 0 and r["qty"] == 0):
                raise fail("unexpected payment row", item)
            cash.append(CashRecord(r["ref"], r["date"], "WITHDRAWAL", r["amount"], r["desc"]))
        else:
            raise fail(f"unsupported statement row type {r['type']!r}", item)
        totals[r["type"]] += r["amount"]
    if balance != closing[1]:
        raise ParseError(f"rows end at balance {balance}, closing balance is {closing[1]}")

    # Summary block after the table, up to the IMPORTANT notes.
    end = lines.index_of("IMPORTANT", k)
    if end is None:
        raise ParseError("statement summary not found")
    summary: dict[str, Decimal] = {}
    for item in items[k + 1:end]:
        m = SUMMARY_LINE.match(item[1])
        if not m:
            raise fail("unexpected line in statement summary", item)
        summary[m.group(1)] = dec(m.group(2))
    layout = SUMMARY_LAYOUTS.get(tuple(summary))
    if layout is None:
        raise ParseError(f"unknown statement summary layout: {list(summary)}")
    expected = {
        "Balance B/F": -opening[1], "Total Purchases": totals["B"], "Total Sales": -totals["S"],
        "Total Receipt": -totals["R"], "Total Payments": totals["PV"], "Total Transaction Cost/Purchases": tx_cost["B"],
        "Total Transaction Cost/Sales": tx_cost["S"], "Total Transaction Cost": tx_cost["B"] + tx_cost["S"],
        "Total Balance": -closing[1],
    }
    for label, value in summary.items():
        want = expected.get(label, None if label in CHECKED else Decimal(0))
        if want is not None and value != want:
            raise ParseError(f"summary {label!r} is {value}, expected {want}"
                             + ("" if label in CHECKED else " (not supported yet)"))
    return ParsedDocument("statement", f"statement/{layout}", account, trades=trades, cash=cash)
