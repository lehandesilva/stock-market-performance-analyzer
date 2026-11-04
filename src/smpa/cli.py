"""Command line entry point: `smpa <command>`."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .config import load_config
from .db import connect


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="smpa", description="Broker statements -> dashboard")
    ap.add_argument("-c", "--config", help="path to config.toml (default: ./config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="authorize Gmail access (opens a browser)")
    sub.add_parser("fetch", help="download new PDF statements from Gmail")
    imp = sub.add_parser("import", help="import PDFs from a local folder")
    imp.add_argument("folder", type=Path)
    for name, hlp in (("extract", "PDF -> text (OCR when needed)"), ("parse", "text -> transactions")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--force", action="store_true", help="redo already-processed documents")
    run = sub.add_parser("run", help="fetch + extract + parse")
    run.add_argument("--no-fetch", action="store_true", help="skip Gmail, only process local data")
    sub.add_parser("dashboard", help="start the Streamlit dashboard")
    args = ap.parse_args(argv)

    if args.config:
        os.environ["SMPA_CONFIG"] = str(Path(args.config).resolve())
    cfg = load_config()
    cfg.ensure_dirs()

    if args.cmd == "dashboard":
        app = Path(__file__).with_name("dashboard.py")
        sys.exit(subprocess.call([
            sys.executable, "-m", "streamlit", "run", str(app),
            "--server.address", "localhost",  # private data: don't expose on the LAN
            "--browser.gatherUsageStats", "false",
            "--client.toolbarMode", "minimal",
        ]))

    conn = connect(cfg.db_path)

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

        ok, failed = extract_all(cfg, conn, force=getattr(args, "force", False))
        print(f"extract: {ok} documents extracted, {failed} failed")
    if args.cmd in ("parse", "run"):
        from .parsers import parse_all

        docs, txs = parse_all(cfg, conn, force=getattr(args, "force", False))
        print(f"parse:   {txs} transactions from {docs} documents")


if __name__ == "__main__":
    main()
