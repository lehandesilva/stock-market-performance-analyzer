"""Local web server for the dashboard (standard library only).

Binds to 127.0.0.1. Requests whose Host header isn't this server are refused
(protects against DNS rebinding), and the one state-changing endpoint needs a
custom header that other websites can't send (protects against CSRF).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import webbrowser
from collections import OrderedDict
from contextlib import closing
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ..config import Config
from ..db import connect
from ..errors import PipelineStop
from ..parsers.ndb.common import SYMBOL
from .api import build_payload, document_text, export_csv, ticker_payload

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")
FILES = {"/": ("index.html", "text/html"), "/static/app.css": ("app.css", "text/css"),
         "/static/app.js": ("app.js", "text/javascript"), "/static/theme.js": ("theme.js", "text/javascript"),
         "/static/lib.js": ("lib.js", "text/javascript"), "/static/ticker.js": ("ticker.js", "text/javascript"),
         "/static/views.js": ("views.js", "text/javascript"), "/static/habits.js": ("habits.js", "text/javascript")}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _fingerprint(cfg: Config) -> tuple:
    """What every payload is built from: the database (SQLite's file change counter, which every commit bumps,
    and its size) and each data/ CSV file (its bytes, hashed: they're small, and a timestamp can miss two quick
    edits). Plus today's date, since a few figures look back from today."""
    out: list = [date.today().isoformat()]
    try:
        with cfg.db_path.open("rb") as f:
            out.append((f.read(100)[24:28], cfg.db_path.stat().st_size))
    except FileNotFoundError:
        out.append(None)
    wal = cfg.db_path.with_name(cfg.db_path.name + "-wal")
    out.append(wal.stat().st_mtime_ns if wal.exists() else None)
    for p in (cfg.actions_csv, cfg.manual_csv, cfg.prices_csv, cfg.tbill_csv, cfg.ccpi_csv, cfg.fx_csv,
              cfg.indices_csv, cfg.sectors_csv, cfg.journal_csv):
        out.append(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None)
    return tuple(out)


class _Cache:
    """The last few payloads as JSON, reused while nothing they're built from has changed (docs/06 §8): the
    per-stock page replays the ledger several times, and every page asks again on each visit."""

    def __init__(self, size: int = 32):
        self.size, self.items, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key, fingerprint: tuple, build) -> bytes:
        with self.lock:
            hit = self.items.get(key)
            if hit and hit[0] == fingerprint:
                self.items.move_to_end(key)
                return hit[1]
        body = build()
        with self.lock:
            self.items[key] = (fingerprint, body)
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)
        return body


def _performance():
    from .report import performance_payload

    return performance_payload


def _habits():
    from .report import habits_payload

    return habits_payload


VIEWS = {"/api/performance": _performance, "/api/habits": _habits}  # path -> (function building that page's payload)


def make_handler(cfg: Config, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    cache = _Cache()

    def payload(key, build) -> bytes:
        """JSON for a page, from the cache while the data hasn't changed."""
        def make():
            with closing(connect(cfg.db_path)) as conn:
                return json.dumps(build(conn), allow_nan=False).encode()
        return cache.get(key, _fingerprint(cfg), make)

    class Handler(BaseHTTPRequestHandler):
        server_version = "smpa"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep the terminal quiet; requests go to the log file
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                for k, v in SECURITY_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The browser gave up on the request (a reload, or a favicon it stopped waiting for).
                log.debug("client went away before the reply to %s", self.path)
                self.close_connection = True

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, allow_nan=False).encode(), "application/json")

        def _host_ok(self) -> bool:
            if self.headers.get("Host") in allowed_hosts:
                return True
            self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            try:
                self._get()
            except Exception as exc:  # a bug shouldn't drop the connection without a word
                log.exception("GET %s failed", self.path)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc} (see the log)"})

        def _get(self):
            url = urlsplit(self.path)
            if url.path in FILES:
                name, ctype = FILES[url.path]
                return self._send(HTTPStatus.OK, (STATIC / name).read_bytes(), ctype)
            if url.path == "/api/portfolio":
                as_of = parse_qs(url.query).get("as_of", [None])[0]
                if as_of is not None and not DATE.match(as_of):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "as_of must be YYYY-MM-DD"})
                try:
                    body = payload(("portfolio", as_of), lambda conn: build_payload(cfg, conn, as_of))
                    return self._send(HTTPStatus.OK, body, "application/json")
                except PipelineStop as exc:  # e.g. a mistake in data/corporate_actions.csv
                    log.error("dashboard data: %s", exc)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            if url.path in VIEWS:
                try:
                    body = payload((url.path,), lambda conn: VIEWS[url.path]()(cfg, conn))
                    return self._send(HTTPStatus.OK, body, "application/json")
                except PipelineStop as exc:  # e.g. a mistake in one of the CSV files in data/
                    log.error("dashboard data: %s", exc)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            if m := re.fullmatch(r"/api/ticker/([^/]+)", url.path):
                symbol = unquote(m.group(1))
                sold_on = parse_qs(url.query).get("sold_on", [None])[0]
                if not re.fullmatch(SYMBOL, symbol):
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "not a symbol"})
                if sold_on is not None and not DATE.match(sold_on):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "sold_on must be YYYY-MM-DD"})
                try:
                    body = payload(("ticker", symbol, sold_on), lambda conn: ticker_payload(cfg, conn, symbol, sold_on))
                except PipelineStop as exc:
                    log.error("dashboard data: %s", exc)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                if body == b"null":
                    return self._json(HTTPStatus.NOT_FOUND, {"error": f"{symbol} was never traded"})
                return self._send(HTTPStatus.OK, body, "application/json")
            if m := re.fullmatch(r"/export/(yearly|transactions)\.csv|/export/stock/([^/]+)\.csv", url.path):
                symbol = unquote(m.group(2)) if m.group(2) else None
                if symbol is not None and not re.fullmatch(SYMBOL, symbol):
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "not a symbol"})
                try:
                    with closing(connect(cfg.db_path)) as conn:
                        body = export_csv(cfg, conn, m.group(1) or "stock", symbol)
                except PipelineStop as exc:
                    log.error("export: %s", exc)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                if body is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": f"{symbol} was never traded"})
                name = f"smpa-{m.group(1) or symbol}.csv"
                return self._send(HTTPStatus.OK, body, "text/csv", {"Content-Disposition": f'attachment; filename="{name}"'})
            if m := re.fullmatch(r"/api/documents/(\d+)/text", url.path):
                with closing(connect(cfg.db_path)) as conn:
                    text = document_text(cfg, conn, int(m.group(1)))
                if text is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "no extracted text"})
                return self._send(HTTPStatus.OK, text.encode(), "text/plain")
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return
            if self.headers.get("X-SMPA") != "1":
                return self._json(HTTPStatus.FORBIDDEN, {"error": "missing X-SMPA header"})
            if urlsplit(self.path).path != "/api/prices":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            from ..prices import PriceAPIError, update_history, update_prices

            try:
                with closing(connect(cfg.db_path)) as conn:
                    n, missing = update_prices(cfg, conn)
                    update_history(cfg, conn)  # quick unless a week has passed
            except PriceAPIError as exc:
                log.error("price refresh failed: %s", exc)
                return self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            except PipelineStop as exc:  # e.g. a mistake in data/corporate_actions.csv
                log.error("price refresh failed: %s", exc)
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            self._json(HTTPStatus.OK, {"updated": n, "missing": missing})

    return Handler


def serve(cfg: Config, port: int = 8501, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, port))
    url = f"http://localhost:{port}/"
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
