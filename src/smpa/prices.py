"""Market prices and price history from the Colombo Stock Exchange website's API.

The API is unofficial (reverse-engineered from www.cse.lk; see
github.com/GH0STH4CKER/Colombo-Stock-Exchange-CSE-API-Documentation), so the
response is checked: if its shape changes, PriceAPIError says so instead of
storing a wrong price.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from .config import Config

log = logging.getLogger(__name__)

SUMMARY_URL = "https://www.cse.lk/api/tradeSummary"  # every security traded today, one request
COMPANY_URL = "https://www.cse.lk/api/companyInfoSummery"  # sic; one security, last traded price
ASPI_URL = "https://www.cse.lk/api/aspiData"  # All Share Price Index, now
INDEX_CHART_URL = "https://www.cse.lk/api/chartData"  # chartId=1: ASPI; period=5: daily, last year
STOCK_CHART_URL = "https://www.cse.lk/api/companyChartDataByStock"  # stockId (from companyInfoSummery), period=5
YEAR_PERIOD = "5"  # the longest period the chart endpoints answer; longer ones come back empty
HISTORY_MAX_AGE = 7  # days before a series' year of history is downloaded again (fills days not run)
ASPI = "ASPI"
COLOMBO = timezone(timedelta(hours=5, minutes=30))  # CSE timestamps are instants; trading days are local
HEADERS = {"User-Agent": "smpa/0.1 (personal portfolio tracker)", "Accept": "application/json"}
TRANSIENT = {429, 500, 502, 503, 504}


class PriceAPIError(Exception):
    pass


@dataclass
class Quote:
    symbol: str
    price: float
    name: str | None = None
    change: float | None = None
    change_pct: float | None = None
    traded_at: str | None = None  # ISO time of the last trade, when known


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def parse_trade_summary(payload) -> dict[str, Quote]:
    """Quotes from a tradeSummary response (only securities that traded today)."""
    rows = payload.get("reqTradeSummery") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) and "symbol" in r and "price" in r for r in rows):
        raise PriceAPIError("unexpected CSE tradeSummary response; the API may have changed")
    quotes = {}
    for r in rows:
        price = _num(r["price"])
        if price and price > 0:
            ts = _num(r.get("lastTradedTime"))
            traded_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(timespec="seconds") if ts else None
            quotes[r["symbol"]] = Quote(r["symbol"], price, r.get("name"), _num(r.get("change")),
                                        _num(r.get("percentageChange")), traded_at)
    return quotes


def parse_quote(symbol: str, payload) -> Quote | None:
    """Quote from a companyInfoSummery response; None if the CSE has no price for it."""
    if payload is None or payload == {}:  # HTTP 404: unknown or delisted symbol
        return None
    if not isinstance(payload, dict) or "reqSymbolInfo" not in payload:
        keys = list(payload)[:10] if isinstance(payload, dict) else type(payload).__name__
        raise PriceAPIError(f"unexpected CSE API response for {symbol} (got {keys}); the API may have changed")
    info = payload["reqSymbolInfo"]
    if not info:  # unknown or delisted symbol
        return None
    if not isinstance(info, dict) or "lastTradedPrice" not in info:
        raise PriceAPIError(f"CSE API response for {symbol} has no lastTradedPrice; the API may have changed")
    if info.get("symbol") not in (None, symbol):
        raise PriceAPIError(f"asked the CSE API for {symbol} but got {info.get('symbol')}")
    price = _num(info["lastTradedPrice"])
    if price is None and info.get("lastTradedPrice") is None:
        price = _num(info.get("previousClose"))  # before today's first trade the CSE reports null
    if not price or price < 0:  # never traded / suspended
        return None
    return Quote(symbol, price, info.get("name"), _num(info.get("change")), _num(info.get("changePercentage")))


def _day(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=COLOMBO).date().isoformat()


def trading_day(iso: str) -> str:
    """The Colombo date of an ISO timestamp (CSE timestamps are instants)."""
    return datetime.fromisoformat(iso).astimezone(COLOMBO).date().isoformat()


def parse_aspi(payload) -> tuple[str, float]:
    """(trading day, level) from an aspiData response."""
    if not isinstance(payload, dict) or _num(payload.get("value")) is None or _num(payload.get("timestamp")) is None:
        raise PriceAPIError("unexpected CSE aspiData response; the API may have changed")
    return _day(payload["timestamp"]), float(payload["value"])


def _series(rows, t: str, v: str, what: str) -> dict[str, float]:
    if not isinstance(rows, list) or not all(isinstance(r, dict) and t in r and v in r for r in rows):
        raise PriceAPIError(f"unexpected CSE chart response for {what}; the API may have changed")
    out = {}
    for r in rows:  # several points on one day: the last one is the close
        ts, val = _num(r[t]), _num(r[v])
        if ts and val and val > 0:
            out[_day(ts)] = val
    return out


def parse_index_chart(payload) -> dict[str, float]:
    """{day: level} from chartData: [{"d": ms, "v": level, "pc": %}, ...]."""
    return _series(payload, "d", "v", "the ASPI")


def parse_stock_chart(symbol: str, payload) -> dict[str, float]:
    """{day: closing price} from companyChartDataByStock: {"chartData": [{"t": ms, "p": price, ...}], "id": n}."""
    if not isinstance(payload, dict) or "chartData" not in payload:
        raise PriceAPIError(f"unexpected CSE chart response for {symbol}; the API may have changed")
    return _series(payload["chartData"], "t", "p", symbol)


def _post(session: requests.Session, url: str, data: dict, what: str, retries: int = 3):
    """POST to the CSE API. Returns parsed JSON, or None for HTTP 404."""
    for attempt in range(retries):
        try:
            r = session.post(url, data=data, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code in TRANSIENT and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == retries - 1:
                raise PriceAPIError(f"could not reach the CSE API ({what}): {exc}") from exc
            time.sleep(2 ** attempt)
        except requests.HTTPError as exc:
            raise PriceAPIError(f"CSE API returned HTTP {exc.response.status_code} ({what})") from exc
        except ValueError as exc:  # body isn't JSON
            raise PriceAPIError(f"CSE API response ({what}) is not JSON; the API may have changed") from exc
    raise PriceAPIError(f"CSE API kept failing ({what})")


def fetch_quotes(symbols: list[str], session: requests.Session | None = None,
                 delay: float = 0.3) -> tuple[dict[str, Quote], list[str]]:
    """Returns (quotes, symbols without a price). Raises PriceAPIError on API problems.

    One tradeSummary request covers everything that traded today; the rest are
    looked up one by one (their last traded price may be from an earlier day).
    """
    session = session or requests.Session()
    if not symbols:
        return {}, []
    today = parse_trade_summary(_post(session, SUMMARY_URL, {}, "tradeSummary"))
    quotes = {s: today[s] for s in symbols if s in today}
    missing = []
    for i, sym in enumerate(s for s in symbols if s not in today):
        if i and delay:
            time.sleep(delay)  # be gentle with the CSE website
        q = parse_quote(sym, _post(session, COMPANY_URL, {"symbol": sym}, sym))
        if q:
            quotes[sym] = q
        else:
            missing.append(sym)
    return quotes, missing


def held_symbols(cfg: Config, conn: sqlite3.Connection, since: date | None = None) -> list[str]:
    """Symbols held now, or (with `since`) at any time from that day on."""
    from .actions import load_actions
    from .analysis import compute, held_between, load_transactions

    actions = load_actions(cfg.actions_csv)
    tx = load_transactions(conn, cfg.manual_csv, actions)
    if since is not None:
        return held_between(tx, actions, since)
    return sorted(compute(tx, actions=actions).holdings["symbol"])


def update_prices(cfg: Config, conn: sqlite3.Connection, session: requests.Session | None = None,
                  delay: float = 0.3) -> tuple[int, list[str]]:
    """Fetch and store prices for current holdings. Returns (updated, symbols without a price)."""
    quotes, missing = fetch_quotes(held_symbols(cfg, conn), session, delay)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR REPLACE INTO prices (symbol, name, price, change, change_pct, traded_at, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(q.symbol, q.name, q.price, q.change, q.change_pct, q.traded_at, now) for q in quotes.values()],
    )
    # One close per stock per trading day, dated by its last trade. Stocks looked up one by one haven't
    # traded today, so their price is an older close (the weekly download has it); storing it under
    # today's date would invent a trading day, e.g. on a weekend or a Poya day.
    conn.executemany(
        "INSERT OR REPLACE INTO price_history (symbol, date, price) VALUES (?, ?, ?)",
        [(q.symbol, trading_day(q.traded_at), q.price) for q in quotes.values() if q.traded_at],
    )
    try:  # the benchmark is extra: a problem with it shouldn't block your own prices
        day, level = parse_aspi(_post(session or requests.Session(), ASPI_URL, {}, "aspiData"))
        conn.execute("INSERT OR REPLACE INTO index_history (name, date, value) VALUES (?, ?, ?)", (ASPI, day, level))
    except PriceAPIError as exc:
        log.warning("ASPI not updated: %s", exc)
    conn.commit()
    for sym in missing:
        log.warning("no CSE price for %s (unknown, delisted or not traded yet)", sym)
    log.info("prices updated for %d symbols", len(quotes))
    return len(quotes), missing


def update_history(cfg: Config, conn: sqlite3.Connection, session: requests.Session | None = None,
                   delay: float = 0.3, today: date | None = None) -> tuple[int, list[str]]:
    """Download a year of daily closes for the ASPI and every stock held during that year.

    Each series is downloaded again after HISTORY_MAX_AGE days, which fills in days `smpa prices`
    wasn't run. Returns (series downloaded, problems). Problems are logged, never fatal: history only
    feeds the value chart and the ASPI comparison.
    """
    session = session or requests.Session()
    today = today or date.today()
    fresh = {r[0] for r in conn.execute("SELECT series FROM history_fetches WHERE fetched_on > ?",
                                        ((today - timedelta(days=HISTORY_MAX_AGE)).isoformat(),))}
    wanted = [f"index:{ASPI}"] + held_symbols(cfg, conn, since=today - timedelta(days=366))
    done, problems = 0, []
    for i, series in enumerate(s for s in wanted if s not in fresh):
        if i and delay:
            time.sleep(delay)
        try:
            if series.startswith("index:"):
                points = parse_index_chart(_post(session, INDEX_CHART_URL,
                                                 {"symbol": "", "chartId": "1", "period": YEAR_PERIOD}, "ASPI chart"))
                conn.executemany("INSERT OR REPLACE INTO index_history (name, date, value) VALUES (?, ?, ?)",
                                 [(ASPI, d, v) for d, v in points.items()])
            else:
                info = _post(session, COMPANY_URL, {"symbol": series}, series)
                sec_id = ((info or {}).get("reqSymbolInfo") or {}).get("id")
                if not isinstance(sec_id, int):
                    problems.append(f"{series}: the CSE doesn't know this symbol (delisted?)")
                    continue
                time.sleep(delay)
                points = parse_stock_chart(series, _post(session, STOCK_CHART_URL,
                                                         {"stockId": str(sec_id), "period": YEAR_PERIOD}, series))
                conn.executemany("INSERT OR REPLACE INTO price_history (symbol, date, price) VALUES (?, ?, ?)",
                                 [(series, d, v) for d, v in points.items()])
        except PriceAPIError as exc:
            problems.append(f"{series}: {exc}")
            continue
        conn.execute("INSERT OR REPLACE INTO history_fetches (series, fetched_on) VALUES (?, ?)",
                     (series, today.isoformat()))
        conn.commit()
        done += 1
    for p in problems:
        log.warning("price history: %s", p)
    return done, problems
