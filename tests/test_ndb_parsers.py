"""NDB parsers on synthetic fixtures (same layouts as real documents, fake data)."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from smpa.parsers.models import ParseError
from smpa.parsers.ndb import classify, parse_document

FIX = Path(__file__).parent / "fixtures" / "ndb"


def text(name: str) -> str:
    return (FIX / name).read_text()


def test_contract_note_v1():
    d = parse_document(text("contract_note_2021-05_bought.txt"))
    assert (d.doc_type, d.layout, d.account) == ("contract_note", "contract_note/2021-05", "NBS-00000-XX/00")
    t = d.trades[0]
    assert (t.contract_no, t.trade_date, t.settlement_date, t.side) == (
        "2021900001", date(2021, 3, 15), date(2021, 3, 22), "BUY")
    assert (t.symbol, t.quantity, t.price, t.gross, t.net) == ("AAA.N0000", 300, D("12.50"), D("3750.00"), D("3792.00"))
    assert t.fees["cds"] == D("0.90")  # printed as ".90"
    assert t.fees["clearing"] == 0 and t.fees_total == D("42.00")


def test_contract_note_2026_09_sold_subtracts_fees():
    [t] = parse_document(text("contract_note_2026-09_sold.txt")).trades
    assert t.side == "SELL" and t.net == t.gross - t.fees_total
    assert t.fees["clearing"] == D("0.18")


def test_contract_note_2025_03_foreign_brokerage_without_clearing():
    d = parse_document(text("contract_note_2025-03_bought.txt"))
    assert d.layout == "contract_note/2025-03" and len(d.trades) == 2
    t = d.trades[0]
    assert (t.quantity, t.gross, t.fees["clearing"], t.fees["foreign_brokerage"]) == (1500, D("12300.00"), 0, 0)
    assert t.net == t.gross + t.fees_total


def test_contract_note_quantity_without_thousands_separator():
    [t] = parse_document(text("contract_note_2021-05_sold.txt")).trades
    assert (t.side, t.quantity, t.price, t.gross) == ("SELL", 2000, D("0.45"), D("900.00"))  # "2000", ".45"


def test_trade_confirmation_v1_and_v2():
    v1 = parse_document(text("trade_confirmation_2021-05.txt"))
    assert v1.layout == "trade_confirmation/2021-05" and len(v1.trades) == 3
    assert (v1.trades[1].isin, v1.trades[1].company) == ("LK0000N00002", "BETA HOLDINGS PLC")
    v2 = parse_document(text("trade_confirmation_2026-09.txt"))
    assert v2.layout == "trade_confirmation/2026-09"
    assert [(t.side, t.symbol, t.quantity) for t in v2.trades] == [("BUY", "CCC.N0000", 1000), ("SELL", "AAA.N0000", 100)]
    assert v2.trades[0].settlement_date == date(2026, 9, 4)


def test_trade_confirmation_2025_03_foreign_brokerage_without_clearing():
    d = parse_document(text("trade_confirmation_2025-03.txt"))
    assert d.layout == "trade_confirmation/2025-03"
    assert [(t.side, t.quantity) for t in d.trades] == [("BUY", 1500), ("SELL", 40)]
    assert d.trades[0].settlement_date == date(2025, 3, 6) and d.trades[0].fees["clearing"] == 0


def test_trade_confirmation_2025_05_exchange_column_and_negative_settlement():
    d = parse_document(text("trade_confirmation_2025-05.txt"))
    assert d.layout == "trade_confirmation/2025-05"
    assert [(t.side, t.quantity) for t in d.trades] == [("SELL", 3000), ("SELL", 2000)]


def test_trade_confirmation_blank_isin():
    t = text("trade_confirmation_2021-07.txt").replace("( AAA.N0000 / LK0000N00001 )", "( AAA.N0000 / )")
    [tr] = parse_document(t).trades
    assert (tr.symbol, tr.isin) == ("AAA.N0000", None)


def test_trade_confirmation_2021_07_per_row_dates():
    d = parse_document(text("trade_confirmation_2021-07.txt"))
    assert d.layout == "trade_confirmation/2021-07"
    [t] = d.trades
    assert (t.side, t.symbol, t.quantity, t.gross, t.settlement_date) == ("SELL", "AAA.N0000", 60, D("1200.00"),
                                                                        date(2021, 7, 29))
    assert t.net == t.gross - t.fees_total and t.fees["clearing"] == 0


def test_statement_v1_wrapped_description_and_deposit():
    d = parse_document(text("statement_2021-05.txt"))
    assert d.layout == "statement/2021-05"
    assert [t.symbol for t in d.trades] == ["AAA.N0000", "BBB.N0000", "BBB.N0000"]
    assert d.trades[1].company == "BETA HOLDINGS PLC" and d.trades[1].fees is None
    [c] = d.cash
    assert (c.ref, c.date, c.amount) == ("RCT202103000001", date(2021, 3, 10), D("10000.00"))


def test_statement_v2_month_first_header():
    d = parse_document(text("statement_2026-10.txt"))
    assert d.layout == "statement/2026-10"
    assert d.trades[0].trade_date == date(2026, 10, 6) and d.cash[0].date == date(2026, 10, 2)


def test_statement_2025_03_summary_without_other_debits_credits():
    t = text("statement_2026-10.txt")
    t = t.replace("Total Other Debits (Dr) 0.00\n", "").replace("Total Other Credits (Cr) 0.00\n", "")
    assert parse_document(t).layout == "statement/2025-03"


def test_receipt():
    [c] = parse_document(text("receipt.txt")).cash
    assert (c.ref, c.date, c.amount, c.kind) == ("RCT202611000001", date(2026, 11, 4), D("20000.00"), "DEPOSIT")


@pytest.mark.parametrize("fixture, old, new, message", [
    # arithmetic: a net amount that doesn't equal gross + fees
    ("contract_note_2021-05_bought.txt", "3,792.00", "3,793.00", r"gross \+ fees"),
    # a new column layout the parser has never seen
    ("contract_note_2021-05_bought.txt", "SEC STL Amount", "SEC STL WHT Amount", "unknown Contract Note column layout"),
    # an unexpected line inside a table
    ("trade_confirmation_2021-05.txt", "Purchase of BETA", "Some new note\nPurchase of BETA", "unexpected line"),
    # section total that doesn't match its rows
    ("trade_confirmation_2026-09.txt", "Sales Total", "Sales Total 1.00\nX", "Sales Total"),
    # running balance broken
    ("statement_2021-05.txt", "10,000.00 Cr 10,000.00 Cr", "10,000.00 Cr 10,001.00 Cr", "running balance"),
    # row type we don't support yet
    ("statement_2026-10.txt", "06/10/2026 B", "06/10/2026 J", "unsupported statement row type"),
    # a dated line that isn't a valid row is reported as such, not as a wrapped description
    ("statement_2026-10.txt", "06/10/2026 B 2026900020", "06/10/2026 B2B 2026900020 X", "unrecognised statement row"),
    # a summary figure we don't handle yet
    ("statement_2026-10.txt", "Total Penalty Charged 0.00", "Total Penalty Charged 5.00", "not supported yet"),
    ("receipt.txt", "TOTAL SETOFF: 0.00", "TOTAL SETOFF: 10.00", "set-off"),
])
def test_bad_documents_raise(fixture, old, new, message):
    t = text(fixture)
    assert old in t
    with pytest.raises(ParseError, match=message):
        parse_document(t.replace(old, new, 1))


def test_unknown_documents_raise():
    with pytest.raises(ParseError, match="not an NDB"):
        classify("Some other broker\nStatement")
    with pytest.raises(ParseError, match="unrecognised NDB document type"):
        classify("NDB SECURITIES (PVT) LTD.\nMarket research report")


def test_statement_with_sales():
    # The summary shows sales (like receipts) as negative: they are credits.
    d = parse_document(text("statement_2021-05_sales.txt"))
    sale, buy = d.trades
    assert (sale.side, sale.symbol, sale.quantity, sale.gross) == ("SELL", "AAA.N0000", 300, D("3900.00"))
    assert sale.net == sale.gross - sale.fees_total and buy.side == "BUY"
    # Payment to the client's bank (PV row), with a description that wraps onto a second line.
    [w] = d.cash
    assert (w.kind, w.ref, w.amount) == ("WITHDRAWAL", "PVN202104000001", D("300.00"))
    assert w.description.endswith("00 SL-000001")
