"""CSE price fetching against canned responses shaped like the live API."""

import pandas as pd
import pytest
import requests

from smpa import prices
from smpa.analysis import load_prices
from smpa.config import Config
from smpa.db import connect
from smpa.prices import PriceAPIError, fetch_quotes, update_prices

SUMMARY = {"reqTradeSummery": [
    {"symbol": "AAA.N0000", "name": "ALPHA PLC", "price": 12.5, "change": 0.5, "percentageChange": 4.1667,
     "lastTradedTime": 1793782747942},
]}
COMPANY = {"reqSymbolBetaInfo": {}, "reqLogo": None,
           "reqSymbolInfo": {"symbol": "BBB.N0000", "name": "BETA PLC", "lastTradedPrice": 80.0, "change": -1.0,
                             "changePercentage": -1.2346}}


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
                 (prices.COMPANY_URL, "BBB.N0000"): [Resp(200, COMPANY)]})
    assert update_prices(cfg, conn, s) == (2, [])
    assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 2
    pd.DataFrame({"symbol": ["bbb.n0000"], "price": [99.0]}).to_csv(cfg.prices_csv, index=False)
    p = load_prices(conn, cfg.prices_csv).set_index("symbol")
    assert p.loc["AAA.N0000", "price"] == 12.5 and p.loc["AAA.N0000", "source"] == "CSE"
    assert p.loc["BBB.N0000", "price"] == 99.0 and p.loc["BBB.N0000", "source"] == "prices.csv"
