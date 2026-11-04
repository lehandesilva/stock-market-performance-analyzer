from datetime import date

from smpa.parsers import ParseContext
from smpa.parsers.generic import parse

CTX = ParseContext(dayfirst=True, fallback_date=date(2024, 4, 1))


def test_full_row():
    [t] = parse("12/03/2024  BUY  JKH.N0000  100  150.00  15,000.00  15,168.00", CTX)
    assert (t.trade_date, t.side, t.symbol) == (date(2024, 3, 12), "BUY", "JKH.N0000")
    assert (t.quantity, t.price, t.gross_amount, t.fees) == (100, 150.0, 15000.0, 168.0)
    assert t.net_amount == 15168.0


def test_sell_with_fees_and_iso_date():
    [t] = parse("2024-03-05 | Sold | AAPL | 10 | 170.25 | 1,702.50 | 1,700.00", CTX)
    assert (t.trade_date, t.side, t.symbol, t.fees) == (date(2024, 3, 5), "SELL", "AAPL", 2.5)
    assert t.net_amount == 1700.0


def test_price_before_quantity_and_company_name():
    [t] = parse("05-Jan-2024 Purchase Commercial Bank PLC COMB.N0000 92.50 2000 185,000.00", CTX)
    assert (t.symbol, t.quantity, t.price) == ("COMB.N0000", 2000, 92.5)


def test_header_date_and_section_side():
    text = "Trade Date: 07/02/2024\nPURCHASES\nHNB.N0000 50 200.00 10,000.00\nSALES\nSAMP.N0000 10 55.00 550.00"
    a, b = parse(text, CTX)
    assert (a.trade_date, a.side, a.symbol) == (date(2024, 2, 7), "BUY", "HNB.N0000")
    assert (b.side, b.symbol, b.gross_amount) == ("SELL", "SAMP.N0000", 550.0)


def test_ignores_noise():
    text = "\n".join([
        "Total Buy 25,000.00",
        "Brokerage 0.64% 15,000.00 96.00",
        "Balance brought forward 1,234.00",
        "BUY JKH 100 150.00 99,999.00",  # quantity x price doesn't match amount
    ])
    assert parse(text, CTX) == []


def test_ocr_artifacts():
    [t] = parse("20/03/2024 SELL JKH.NOQ00 200 205.00 41,000.00 40 , 737.60", CTX)
    assert (t.symbol, t.gross_amount, t.fees) == ("JKH.N0000", 41000.0, 262.4)
