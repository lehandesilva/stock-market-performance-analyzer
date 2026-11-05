"""Local web server for the dashboard (standard library only).

Binds to 127.0.0.1. Requests whose Host header isn't this server are refused
(protects against DNS rebinding), and the one state-changing endpoint needs a
custom header that other websites can't send (protects against CSRF).
"""

from __future__ import annotations

import json
from contextlib import closing
import logging
import re
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..config import Config
from ..db import connect
from ..errors import PipelineStop
from .api import build_payload, document_text

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")
FILES = {"/": ("index.html", "text/html"), "/static/app.css": ("app.css", "text/css"),
         "/static/app.js": ("app.js", "text/javascript"), "/static/theme.js": ("theme.js", "text/javascript")}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def make_handler(cfg: Config, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "smpa"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep the terminal quiet; requests go to the log file
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

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
            url = urlsplit(self.path)
            if url.path in FILES:
                name, ctype = FILES[url.path]
                return self._send(HTTPStatus.OK, (STATIC / name).read_bytes(), ctype)
            if url.path == "/api/portfolio":
                as_of = parse_qs(url.query).get("as_of", [None])[0]
                if as_of is not None and not DATE.match(as_of):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "as_of must be YYYY-MM-DD"})
                try:
                    with closing(connect(cfg.db_path)) as conn:
                        return self._json(HTTPStatus.OK, build_payload(cfg, conn, as_of))
                except PipelineStop as exc:  # e.g. a mistake in data/corporate_actions.csv
                    log.error("dashboard data: %s", exc)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
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
            from ..prices import PriceAPIError, update_prices

            try:
                with closing(connect(cfg.db_path)) as conn:
                    n, missing = update_prices(cfg, conn)
            except PriceAPIError as exc:
                log.error("price refresh failed: %s", exc)
                return self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
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
