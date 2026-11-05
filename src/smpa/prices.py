"""Latest market prices from the Colombo Stock Exchange website's API.

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
from datetime import date, datetime, timezone

import requests

from .config import Config

log = logging.getLogger(__name__)

SUMMARY_URL = "https://www.cse.lk/api/tradeSummary"  # every security traded today, one request
COMPANY_URL = "https://www.cse.lk/api/companyInfoSummery"  # sic; one security, last traded price
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
    if not price or price < 0:  # no trades yet / suspended
        return None
    return Quote(symbol, price, info.get("name"), _num(info.get("change")), _num(info.get("changePercentage")))


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


def held_symbols(cfg: Config, conn: sqlite3.Connection) -> list[str]:
    from .actions import load_actions
    from .analysis import compute, load_transactions

    actions = load_actions(cfg.actions_csv)
    return sorted(compute(load_transactions(conn, cfg.manual_csv, actions), actions=actions).holdings["symbol"])


def update_prices(cfg: Config, conn: sqlite3.Connection, session: requests.Session | None = None,
                  delay: float = 0.3) -> tuple[int, list[str]]:
    """Fetch and store prices for current holdings. Returns (updated, symbols without a price)."""
    quotes, missing = fetch_quotes(held_symbols(cfg, conn), session, delay)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = date.today().isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO prices (symbol, name, price, change, change_pct, traded_at, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(q.symbol, q.name, q.price, q.change, q.change_pct, q.traded_at, now) for q in quotes.values()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO price_history (symbol, date, price) VALUES (?, ?, ?)",
        [(q.symbol, today, q.price) for q in quotes.values()],
    )
    conn.commit()
    for sym in missing:
        log.warning("no CSE price for %s (unknown, delisted or not traded yet)", sym)
    log.info("prices updated for %d symbols", len(quotes))
    return len(quotes), missing
