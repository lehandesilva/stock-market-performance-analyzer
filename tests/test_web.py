"""Dashboard API payload and local server (routing and security checks)."""

import http.client
import json
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from smpa.config import Config
from smpa.db import connect
from smpa.parsers import parse_all
from smpa.web import server
from smpa.web.api import build_payload

FIX = Path(__file__).parent / "fixtures" / "ndb"
ALL = ["contract_note_2021-05_bought.txt", "trade_confirmation_2021-05.txt", "statement_2021-05.txt",
       "contract_note_2026-09_sold.txt", "trade_confirmation_2026-09.txt", "statement_2026-10.txt", "receipt.txt"]


@pytest.fixture
def cfg(tmp_path):
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    for i, name in enumerate(ALL):
        (cfg.text_dir / name).write_text((FIX / name).read_text())
        conn.execute(
            """INSERT INTO documents (source, message_id, attachment_name, email_date, path, sha256, downloaded_at,
                                      text_path, extracted_at) VALUES ('local', ?, ?, '2026-01-01', ?, ?, 'x', ?, 'x')""",
            (f"m{i}", name, f"raw/{name}", f"s{i}", f"text/{name}"),
        )
    conn.commit()
    parse_all(cfg, conn)
    conn.close()
    return cfg


def test_payload_is_strict_json_and_complete(cfg):
    with connect(cfg.db_path) as conn:
        p = build_payload(cfg, conn)
    json.dumps(p, allow_nan=False)  # no NaN/Infinity leaks to the browser
    assert p["meta"]["account"] == "NBS-•••••-XX/00"
    assert (p["meta"]["first_trade"], p["meta"]["last_trade"], p["meta"]["as_of"]) == ("2021-03-15", "2026-10-06",
                                                                                     "2026-10-06")
    assert {h["symbol"] for h in p["holdings"]} == {"AAA.N0000", "BBB.N0000", "CCC.N0000", "DDD.N0000"}
    assert abs(sum(h["weight"] for h in p["holdings"]) - 1) < 1e-9
    assert p["readouts"]["priced"] == 0 and p["readouts"]["market_value"] is None
    assert [f["kind"] for f in p["flags"]].count("document") == 2
    assert len(p["documents"]) == 7 and len(p["transactions"]) == 6
    assert p["capital"][0]["date"] == "2021-03-15"


def test_as_of_filters_and_clamps(cfg):
    with connect(cfg.db_path) as conn:
        early = build_payload(cfg, conn, "2022-01-01")
        before = build_payload(cfg, conn, "1999-01-01")
    assert early["meta"]["as_of"] == "2022-01-01" and len(early["transactions"]) == 3
    assert before["meta"]["as_of"] == "2021-03-15"


def test_corporate_actions_and_returns_in_payload(cfg):
    cfg.actions_csv.write_text("date,symbol,action,per_share,ratio\n2022-01-10,AAA.N0000,DIVIDEND,1.50,\n"
                               "2022-02-01,BBB.N0000,BONUS,,1:10\n")
    with connect(cfg.db_path) as conn:
        p = build_payload(cfg, conn)
        early = build_payload(cfg, conn, "2022-01-05")
    r = p["readouts"]
    assert r["dividends"] == 450.0 and r["dividend_count"] == 1  # 300 AAA held on the ex-date
    assert {h["symbol"]: h["quantity"] for h in p["holdings"]}["BBB.N0000"] == 66
    assert [a["action"] for a in p["actions"]] == ["BONUS", "DIVIDEND"]
    assert r["total_gain"] == pytest.approx(r["realized"] + 450.0)  # nothing priced: unrealized unknown
    assert isinstance(r["xirr"], float) and r["return_since"] == "2021-03-15" and r["return_days"] > 365
    assert early["readouts"]["dividends"] == 0 and early["actions"] == []


def test_value_history_and_aspi_benchmark(cfg):
    with connect(cfg.db_path) as conn:
        for day, f in (("2026-10-01", 1.0), ("2026-10-06", 1.1)):
            conn.executemany("INSERT INTO price_history (symbol, date, price) VALUES (?, ?, ?)",
                             [(s, day, p * f) for s, p in (("AAA.N0000", 14), ("BBB.N0000", 90), ("CCC.N0000", 10),
                                                            ("DDD.N0000", 24))])
            conn.execute("INSERT INTO index_history (name, date, value) VALUES ('ASPI', ?, ?)", (day, 20000 * f))
        conn.commit()
        p = build_payload(cfg, conn)
        early = build_payload(cfg, conn, "2026-10-02")
    assert [h["date"] for h in p["history"]] == ["2026-10-01", "2026-10-06"]
    b = p["benchmark"]
    assert (b["start"], b["end"], b["days"]) == ("2026-10-01", "2026-10-06", 5)
    # everything rose 10%; the 4,854.34 DDD buy on 10-06 goes into the ASPI at that day's level
    start, end = p["history"]
    assert end["benchmark"] == pytest.approx(start["value"] * 1.1 + 4854.34)
    assert end["value"] == pytest.approx(start["value"] * 1.1 + 200 * 24 * 1.1)  # DDD closed above the price paid
    assert b["period_return"] > b["index_period_return"]
    assert [h["date"] for h in early["history"]] == ["2026-10-01", "2026-10-02"]


def test_delisted_holding_leaves_the_dashboard(cfg):
    with connect(cfg.db_path) as conn:
        before = build_payload(cfg, conn)
        cfg.actions_csv.write_text("date,symbol,action,price\n2026-10-20,AAA.N0000,DELIST,15\n")
        after = build_payload(cfg, conn)
    assert "AAA.N0000" in {h["symbol"] for h in before["holdings"]}
    assert "AAA.N0000" not in {h["symbol"] for h in after["holdings"]}  # so not in the network or index
    assert after["readouts"]["positions"] == before["readouts"]["positions"] - 1
    assert after["realized"][0]["symbol"] == "AAA.N0000" and after["actions"][0]["action"] == "DELIST"
    assert after["readouts"]["turnover"] == before["readouts"]["turnover"]  # a buyout isn't market turnover
    assert after["meta"]["last_date"] == "2026-10-20"
    no_price = [f["message"] for f in before["flags"] if "no price from the CSE" in f["message"]]
    assert len(no_price) == 4 and "DELIST" in no_price[0]  # nothing is priced in this test database


@pytest.fixture
def live(cfg):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler(cfg, port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, host=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, headers={"Host": host or f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_pages_and_api(live):
    status, headers, body = request(live, "GET", "/")
    assert status == 200 and b"Portfolio Monitor" in body and "default-src 'self'" in headers["Content-Security-Policy"]
    assert request(live, "GET", "/static/app.js")[0] == 200
    assert request(live, "GET", "/static/theme.js")[0] == 200
    status, _, body = request(live, "GET", "/api/portfolio?as_of=2022-01-01")
    assert status == 200 and json.loads(body)["meta"]["as_of"] == "2022-01-01"
    assert request(live, "GET", "/api/portfolio?as_of=bad")[0] == 400
    status, _, body = request(live, "GET", "/api/documents/1/text")
    assert status == 200 and b"BOUGHT" in body
    assert request(live, "GET", "/api/documents/999/text")[0] == 404


def test_bad_actions_file_is_reported(cfg, live):
    cfg.actions_csv.write_text("date,symbol,action\n2022-01-10,AAA.N0000,MERGER\n")
    status, _, body = request(live, "GET", "/api/portfolio")
    assert status == 500 and "line 2: unknown action" in json.loads(body)["error"]
    status, _, body = request(live, "POST", "/api/prices", headers={"X-SMPA": "1"})
    assert status == 500 and "line 2: unknown action" in json.loads(body)["error"]


def test_empty_database_with_as_of(tmp_path):
    cfg = Config(data_dir=tmp_path)
    with connect(cfg.db_path) as conn:
        p = build_payload(cfg, conn, "2024-01-01")
    assert p["history"] == [] and p["benchmark"] is None and p["readouts"]["xirr"] is None


@pytest.mark.parametrize("path", ["/static/../server.py", "/static/api.py", "/../pyproject.toml"])
def test_only_known_files_are_served(live, path):
    assert request(live, "GET", path)[0] == 404


def test_foreign_host_is_refused(live):
    # DNS rebinding: a page on another domain resolving to 127.0.0.1 still sends its own Host header.
    assert request(live, "GET", "/api/portfolio", host="evil.example:80")[0] == 403


def test_price_refresh_needs_custom_header(live, monkeypatch):
    import smpa.prices

    monkeypatch.setattr(smpa.prices, "update_prices", lambda cfg, conn: (3, ["X.N0000"]))
    assert request(live, "POST", "/api/prices")[0] == 403  # what a cross-site form post would look like
    status, _, body = request(live, "POST", "/api/prices", headers={"X-SMPA": "1"})
    assert status == 200 and json.loads(body) == {"updated": 3, "missing": ["X.N0000"]}


def test_client_hanging_up_is_not_an_error(cfg, caplog):
    """A browser that disconnects before the reply arrives used to print a BrokenPipeError traceback."""
    class Gone:
        def write(self, _):
            raise BrokenPipeError(32, "Broken pipe")

    h = object.__new__(server.make_handler(cfg, 8501))
    h.wfile, h.client_address, h.request_version = Gone(), ("127.0.0.1", 1), "HTTP/1.1"
    h.command, h.path, h.requestline = "GET", "/favicon.ico", "GET /favicon.ico HTTP/1.1"
    h._json(404, {"error": "not found"})  # must not raise
    assert h.close_connection


def test_past_as_of_is_valued_at_that_days_closes(cfg):
    with connect(cfg.db_path) as conn:
        conn.executemany("INSERT INTO price_history (symbol, date, price) VALUES (?, ?, ?)",
                         [("AAA.N0000", "2021-12-30", 14.0), ("BBB.N0000", "2021-11-01", 90.0),
                          ("AAA.N0000", "2026-11-04", 15.0)])
        conn.execute("""INSERT INTO prices (symbol, price, traded_at, fetched_at)
                        VALUES ('AAA.N0000', 100, '2026-11-05T08:00:00+00:00', '2026-11-05T10:00:00+00:00')""")
        conn.commit()
        cfg.prices_csv.write_text("symbol,price\nBBB.N0000,95\n")
        past = build_payload(cfg, conn, "2022-01-03")
        now = build_payload(cfg, conn)
        again = build_payload(cfg, conn, now["meta"]["max_date"])
    h = {x["symbol"]: x for x in past["holdings"]}
    assert (h["AAA.N0000"]["price"], h["AAA.N0000"]["price_at"]) == (14.0, "2021-12-30")  # not today's 100
    assert h["BBB.N0000"]["price"] is None  # its last close is too old then, and prices.csv is today's price
    assert past["meta"]["past"] and past["meta"]["valued"] == "2022-01-03"
    assert past["readouts"]["return_days"] == 294  # 2021-03-15 to the as-of date, not to today
    assert not any("no price from the CSE" in f["message"] for f in past["flags"])
    # Today: a date after the last trade can be picked, and latest prices (and overrides) apply
    assert (now["meta"]["max_date"], now["meta"]["as_of"], now["meta"]["last_date"]) == ("2026-11-05", "2026-11-05",
                                                                                         "2026-10-06")
    assert {x["symbol"]: x["price"] for x in now["holdings"] if x["priced"]} == {"AAA.N0000": 100, "BBB.N0000": 95}
    assert again == now  # as of the newest date is the same as no as-of


def test_value_if_sold_and_break_even(cfg):
    with connect(cfg.db_path) as conn:
        conn.execute("""INSERT INTO prices (symbol, price, traded_at, fetched_at)
                        VALUES ('AAA.N0000', 100, NULL, '2026-11-05T10:00:00+00:00')""")
        conn.commit()
        p = build_payload(cfg, conn)
    fee = p["meta"]["fee_rate"]
    # the three 2026 trades (the 2021 ones are too old to say what fees are now)
    assert fee["trades"] == 3 and fee["from"] == "2026-09-02" and fee["rate"] == pytest.approx(0.0113204, abs=1e-7)
    aaa = next(x for x in p["holdings"] if x["symbol"] == "AAA.N0000")
    assert aaa["liquidation_value"] == pytest.approx(200 * 100 * (1 - fee["rate"]))
    assert aaa["break_even"] == pytest.approx(aaa["avg_cost"] / (1 - fee["rate"]))
    r = p["readouts"]
    assert r["liquidation_value"] == pytest.approx(aaa["liquidation_value"])  # the only priced holding
    assert r["gain_if_sold"] == pytest.approx(r["total_gain"] - 200 * 100 * fee["rate"])
    assert r["xirr_if_sold"] < r["xirr"]


def test_closes_the_cse_changed_are_flagged(cfg):
    with connect(cfg.db_path) as conn:
        conn.executemany("INSERT INTO history_changes VALUES ('AAA.N0000', ?, ?, ?, date('now'))",
                         [("2026-01-05", 100.0, 20.0), ("2026-01-06", 101.0, 20.2)])
        conn.commit()
        [flag] = [f for f in build_payload(cfg, conn)["flags"] if f["kind"] == "price"]
    assert "AAA.N0000" in flag["message"] and "2 stored closes (2026-01-05 … 2026-01-06)" in flag["message"]
    assert "by up to 80%" in flag["message"]
