"""Command line entry point: `smpa <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import Config, load_config
from .db import connect
from .errors import PipelineStop

log = logging.getLogger("smpa")


def _setup_logging(cfg: Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file = logging.FileHandler(cfg.log_path)
    file.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setLevel(logging.ERROR)  # warnings go to the log file and the dashboard
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[file, console])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="smpa", description="Broker statements -> dashboard")
    ap.add_argument("-c", "--config", help="path to config.toml (default: ./config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="authorize Gmail access (opens a browser)")
    sub.add_parser("fetch", help="download new PDF statements from Gmail")
    imp = sub.add_parser("import", help="import PDFs from a local folder")
    imp.add_argument("folder", type=Path)
    for name, hlp in (("extract", "PDF -> text"), ("parse", "text -> trades and deposits, then reconcile")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--force", action="store_true", help="redo already-processed documents")
    run = sub.add_parser("run", help="fetch + extract + parse")
    run.add_argument("--no-fetch", action="store_true", help="skip Gmail, only process local data")
    run.add_argument("--no-prices", action="store_true", help="skip fetching market prices")
    sub.add_parser("prices", help="fetch latest prices for current holdings from the CSE website")
    sub.add_parser("dashboard", help="start the Streamlit dashboard")
    args = ap.parse_args(argv)

    if args.config:
        os.environ["SMPA_CONFIG"] = str(Path(args.config).resolve())
    cfg = load_config()
    cfg.ensure_dirs()
    _setup_logging(cfg)

    if args.cmd == "dashboard":
        app = Path(__file__).with_name("dashboard.py")
        sys.exit(subprocess.call([
            sys.executable, "-m", "streamlit", "run", str(app),
            "--server.address", "localhost",  # private data: don't expose on the LAN
            "--browser.gatherUsageStats", "false",
            "--client.toolbarMode", "minimal",
        ]))

    conn = connect(cfg.db_path)
    try:
        _run(args, cfg, conn)
    except PipelineStop as exc:
        log.error("STOPPED: %s", exc)
        print(f"Details are in {cfg.log_path}", file=sys.stderr)
        sys.exit(1)


def _run(args: argparse.Namespace, cfg: Config, conn) -> None:
    if args.cmd == "auth":
        from .gmail import get_service

        get_service(cfg.secrets_dir)
        print(f"Authorized. Token saved in {cfg.secrets_dir}")
    if args.cmd == "fetch" or (args.cmd == "run" and not args.no_fetch):
        from .gmail import build_query, fetch

        print(f"Searching Gmail: {build_query(cfg)}")
        scanned, saved = fetch(cfg, conn)
        print(f"fetch:   {scanned} new emails, {saved} PDFs saved to {cfg.raw_dir}")
    if args.cmd == "import":
        from .ingest import import_local

        print(f"import:  {import_local(cfg, conn, args.folder)} PDFs added")
    if args.cmd in ("extract", "run"):
        from .extract import extract_all

        print(f"extract: {extract_all(cfg, conn, force=getattr(args, 'force', False))} documents extracted")
    if args.cmd in ("parse", "run"):
        from .parsers import parse_all

        docs, warnings = parse_all(cfg, conn, force=getattr(args, "force", False))
        n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        n_cash = conn.execute("SELECT COUNT(*) FROM cash_movements").fetchone()[0]
        print(f"parse:   {docs} documents parsed; {n_trades} trades, {n_cash} cash movements in total; "
              f"{len(warnings)} warnings (see {cfg.log_path})")
    if args.cmd == "prices" or (args.cmd == "run" and not args.no_prices):
        from .prices import PriceAPIError, update_prices

        try:
            n, missing = update_prices(cfg, conn)
        except PriceAPIError as exc:
            raise PipelineStop(f"Could not get prices from the CSE website: {exc}\n"
                               "The API is unofficial; data/prices.csv (symbol,price) can be used meanwhile.") from exc
        print(f"prices:  {n} holdings priced from the CSE" + (f"; no price for {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
