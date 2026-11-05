"""CSE price fetching against canned responses shaped like the live API."""

import pandas as pd
import pytest
import requests

from smpa import prices
from smpa.analysis import load_prices
from smpa.config import Config
from smpa.db import connect
from smpa.prices import (PriceAPIError, fetch_quotes, parse_index_chart, parse_stock_chart, update_history,
                         update_prices)

SUMMARY = {"reqTradeSummery": [
    {"symbol": "AAA.N0000", "name": "ALPHA PLC", "price": 12.5, "change": 0.5, "percentageChange": 4.1667,
     "lastTradedTime": 1793782747942},
]}
COMPANY = {"reqSymbolBetaInfo": {}, "reqLogo": None,
           "reqSymbolInfo": {"symbol": "BBB.N0000", "name": "BETA PLC", "lastTradedPrice": 80.0, "change": -1.0,
                             "changePercentage": -1.2346}}


# Shapes as returned by the live API (values made up).
ASPI_NOW = {"id": 37260520, "value": 20541.12, "lowValue": 20541.12, "highValue": 20541.12, "change": 0.0,
            "percentage": 0.0, "sectorId": 1.0, "timestamp": 1793846760959}
ASPI_CHART = [{"d": 1762852700000, "v": 23582.16, "pc": 0.34}, {"d": 1762939098000, "v": 23659.7, "pc": 0.33}]
STOCK_CHART = {"id": 297, "chartData": [
    {"h": 21.9, "l": 21.6, "o": None, "s": 98018697259, "q": 19723740, "p": 21.8, "c": None, "pc": None,
     "t": 1762367400000, "n": None, "id": 297},
    {"h": 22.1, "l": 21.8, "o": None, "s": 98018697259, "q": 1000, "p": 22.0, "c": None, "pc": None,
     "t": 1762453800000, "n": None, "id": 297}]}


class Resp:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code}")
            err.response = self
            raise err


class Session:
    """Answers POSTs from a {(url, symbol): [responses...]} script."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def post(self, url, data=None, **kw):
        key = (url, (data or {}).get("symbol"))
        self.calls.append(key)
        return self.script[key].pop(0)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(prices.time, "sleep", lambda s: None)


def test_bulk_summary_then_per_symbol_fallback():
    s = Session({(prices.SUMMARY_URL, None): [Resp(200, SUMMARY)],
                 (prices.COMPANY_URL, "BBB.N0000"): [Resp(200, COMPANY)],
                 (prices.COMPANY_URL, "OLD.N0000"): [Resp(404, {})]})
    quotes, missing = fetch_quotes(["AAA.N0000", "BBB.N0000", "OLD.N0000"], s)
    assert {k: q.price for k, q in quotes.items()} == {"AAA.N0000": 12.5, "BBB.N0000": 80.0}
    assert quotes["AAA.N0000"].traded_at.startswith("2026-11-04T")
    assert missing == ["OLD.N0000"]
    assert (prices.COMPANY_URL, "AAA.N0000") not in s.calls  # already in the bulk summary


def test_transient_errors_are_retried():
    s = Session({(prices.SUMMARY_URL, None): [Resp(503, {}), Resp(200, SUMMARY)]})
    assert "AAA.N0000" in fetch_quotes(["AAA.N0000"], s)[0]


@pytest.mark.parametrize("body, message", [
    ({"something": []}, "tradeSummary response"),
    (ValueError("not json"), "not JSON"),
])
def test_changed_api_raises(body, message):
    s = Session({(prices.SUMMARY_URL, None): [Resp(200, body)]})
    with pytest.raises(PriceAPIError, match=message):
        fetch_quotes(["AAA.N0000"], s)


def test_company_response_without_price_field_raises():
    s = Session({(prices.SUMMARY_URL, None): [Resp(200, {"reqTradeSummery": []})],
                 (prices.COMPANY_URL, "BBB.N0000"): [Resp(200, {"reqSymbolInfo": {"symbol": "BBB.N0000"}})]})
    with pytest.raises(PriceAPIError, match="lastTradedPrice"):
        fetch_quotes(["BBB.N0000"], s)


def test_update_prices_stores_latest_and_history_and_csv_overrides(tmp_path):
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    conn.execute("""INSERT INTO trades (contract_no, trade_date, side, symbol, quantity, price, gross, fees_total,
                                        net, sources) VALUES ('1', '2024-01-01', 'BUY', 'AAA.N0000', 10, 10, 100, 1,
                                        101, 'contract_note'),
                                       ('2', '2024-01-01', 'BUY', 'BBB.N0000', 5, 70, 350, 4, 354, 'contract_note')""")
    conn.commit()
    s = Session({(prices.SUMMARY_URL, None): [Resp(200, SUMMARY)],
                 (prices.COMPANY_URL, "BBB.N0000"): [Resp(200, COMPANY)],
                 (prices.ASPI_URL, None): [Resp(200, ASPI_NOW)]})
    assert update_prices(cfg, conn, s) == (2, [])
    assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 2
    assert [tuple(r) for r in conn.execute("SELECT * FROM index_history")] == [("ASPI", "2026-11-05", 20541.12)]
    pd.DataFrame({"symbol": ["bbb.n0000"], "price": [99.0]}).to_csv(cfg.prices_csv, index=False)
    p = load_prices(conn, cfg.prices_csv).set_index("symbol")
    assert p.loc["AAA.N0000", "price"] == 12.5 and p.loc["AAA.N0000", "source"] == "CSE"
    assert p.loc["BBB.N0000", "price"] == 99.0 and p.loc["BBB.N0000", "source"] == "prices.csv"


def test_chart_days_are_colombo_trading_days():
    # 1762367400000 is 2025-11-05 18:30 UTC, which is midnight on the 6th in Colombo
    assert parse_stock_chart("JKH.N0000", STOCK_CHART) == {"2025-11-06": 21.8, "2025-11-07": 22.0}
    assert parse_index_chart(ASPI_CHART) == {"2025-11-11": 23582.16, "2025-11-12": 23659.7}
    with pytest.raises(PriceAPIError, match="chart response for JKH"):
        parse_stock_chart("JKH.N0000", {"data": []})


def test_aspi_problem_does_not_block_prices(tmp_path):
    cfg = Config(data_dir=tmp_path)
    conn = connect(cfg.db_path)
    conn.execute("""INSERT INTO trades (contract_no, trade_date, side, symbol, quantity, price, gross, fees_total,
                    net, sources) VALUES ('1', '2024-01-01', 'BUY', 'AAA.N0000', 10, 10, 100, 1, 101, 'x')""")
    s = Session({(prices.SUMMARY_URL, None): [Resp(200, SUMMARY)], (prices.ASPI_URL, None): [Resp(200, {"x": 1})]})
    assert update_prices(cfg, conn, s) == (1, [])
    assert conn.execute("SELECT COUNT(*) FROM index_history").fetchone()[0] == 0


def test_history_downloads_a_year_then_waits_a_week(tmp_path):
    from datetime import date

    cfg = Config(data_dir=tmp_path)
    conn = connect(cfg.db_path)
    conn.execute("""INSERT INTO trades (contract_no, trade_date, side, symbol, quantity, price, gross, fees_total, net,
                    sources) VALUES ('1', '2025-01-01', 'BUY', 'JKH.N0000', 10, 20, 200, 2, 202, 'x'),
                                    ('2', '2025-01-01', 'BUY', 'OLD.N0000', 10, 5, 50, 1, 51, 'x'),
                                    ('3', '2026-01-02', 'SELL', 'OLD.N0000', 10, 6, 60, 1, 59, 'x'),
                                    ('4', '2025-01-01', 'BUY', 'GONE.N0000', 10, 5, 50, 1, 51, 'x'),
                                    ('5', '2025-06-01', 'SELL', 'GONE.N0000', 10, 6, 60, 1, 59, 'x')""")
    s = Session({(prices.INDEX_CHART_URL, ""): [Resp(200, ASPI_CHART)],
                 (prices.COMPANY_URL, "JKH.N0000"): [Resp(200, {"reqSymbolInfo": {"id": 297, "symbol": "JKH.N0000"}})],
                 (prices.COMPANY_URL, "OLD.N0000"): [Resp(404, {})],
                 (prices.STOCK_CHART_URL, None): [Resp(200, STOCK_CHART)]})
    done, problems = update_history(cfg, conn, s, today=date(2026, 11, 5))
    # GONE was sold more than a year ago, so it isn't needed; OLD was held this year but is delisted
    assert done == 2 and len(problems) == 1 and "OLD.N0000" in problems[0]
    assert (prices.COMPANY_URL, "GONE.N0000") not in s.calls
    assert conn.execute("SELECT COUNT(*) FROM price_history WHERE symbol = 'JKH.N0000'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM index_history").fetchone()[0] == 2
    s.calls.clear()
    s.script[(prices.COMPANY_URL, "OLD.N0000")] = [Resp(404, {})]
    assert update_history(cfg, conn, s, today=date(2026, 11, 9))[0] == 0  # fresh: only the failed one retried
    assert s.calls == [(prices.COMPANY_URL, "OLD.N0000")]


def test_before_the_first_trade_of_the_day_the_previous_close_is_used():
    pre_open = {"reqSymbolInfo": {"symbol": "BBB.N0000", "lastTradedPrice": None, "previousClose": 18.2,
                                  "change": None, "changePercentage": None}}
    s = Session({(prices.SUMMARY_URL, None): [Resp(200, {"reqTradeSummery": []})],
                 (prices.COMPANY_URL, "BBB.N0000"): [Resp(200, pre_open)]})
    assert fetch_quotes(["BBB.N0000"], s)[0]["BBB.N0000"].price == 18.2
