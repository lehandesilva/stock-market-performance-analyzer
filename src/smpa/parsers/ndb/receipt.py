"""NDB payment receipts: money you deposited with the broker."""

from __future__ import annotations

import re
from decimal import Decimal

from ..models import CashRecord, ParsedDocument, ParseError
from .common import CONTRACT, DMY, MONEY, Lines, dec, dmy, fail

SETOFF_HEADER = "Contract No Value Contract No Value Contract No Value Contract No Value"
PAIR = rf"({CONTRACT}) ({MONEY})"


def parse(text: str) -> ParsedDocument:
    lines = Lines(text)
    ref = lines.search(r"Receipt No : (RCT\d+)$", "receipt number").group(1)
    account = lines.search(r"Account Code : (\S+)$", "account code").group(1)
    when = dmy(lines.search(rf"Date : ({DMY})$", "receipt date").group(1))
    amount = dec(lines.search(rf"a sum of Rs\. ({MONEY}) being the", "receipt amount").group(1))
    m = lines.search(rf"^Mode of payment : (.+) TOTAL SETOFF: ({MONEY})$", "payment mode / set-off")
    mode, setoff = m.group(1), dec(m.group(2))
    remarks = lines.search(r"^Remarks: ?(.*)$", "remarks").group(1)

    # Optional table of contracts this payment was set off against.
    items = list(lines)
    start = lines.index_of(SETOFF_HEADER)
    if start is None:
        raise ParseError("receipt set-off table header not found")
    setoff_rows = Decimal(0)
    for item in items[start + 1:]:
        if item[1].startswith("Mode of payment :"):
            break
        if not re.fullmatch(rf"{PAIR}( {PAIR})*", item[1]):
            raise fail("unexpected line in receipt set-off table", item)
        setoff_rows += sum((dec(v) for _, v in re.findall(PAIR, item[1])), Decimal(0))
    if setoff_rows != setoff:
        raise ParseError(f"set-off rows add up to {setoff_rows}, TOTAL SETOFF says {setoff}")
    if amount <= 0:
        raise ParseError(f"receipt amount must be positive, got {amount}")

    desc = f"{mode}: {remarks}".strip(": ")
    return ParsedDocument("receipt", "receipt/v1", account, cash=[CashRecord(ref, when, "DEPOSIT", amount, desc)])
