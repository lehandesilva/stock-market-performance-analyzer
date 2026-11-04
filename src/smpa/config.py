"""Configuration loading (config.toml + env overrides)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    data_dir: Path
    gmail_query: str = "has:attachment filename:pdf"
    gmail_after: str = ""
    pdf_passwords: list[str] = field(default_factory=list)
    ocr_mode: str = "never"
    ocr_language: str = "eng"
    ocr_dpi: int = 300
    ocr_psm: int = 6
    parser: str = "ndb"
    account: str = ""
    ignore_attachments: list[str] = field(default_factory=list)
    currency: str = "LKR"

    # Derived paths -------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "smpa.db"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "smpa.log"

    @property
    def prices_csv(self) -> Path:
        return self.data_dir / "prices.csv"

    @property
    def manual_csv(self) -> Path:
        return self.data_dir / "manual_transactions.csv"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.text_dir, self.secrets_dir, self.log_path.parent):
            d.mkdir(parents=True, exist_ok=True)
        # Secrets should not be world-readable.
        self.secrets_dir.chmod(0o700)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from `path`, $SMPA_CONFIG, or ./config.toml (in that order)."""
    path = Path(path or os.environ.get("SMPA_CONFIG") or "config.toml")
    raw: dict = {}
    if path.exists():
        raw = tomllib.loads(path.read_text())
    base = path.resolve().parent

    paths = raw.get("paths", {})
    gmail = raw.get("gmail", {})
    pdf = raw.get("pdf", {})
    ocr = raw.get("ocr", {})
    parser = raw.get("parser", {})

    data_dir = Path(paths.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = base / data_dir

    passwords = list(pdf.get("passwords", []))
    env_pw = os.environ.get("SMPA_PDF_PASSWORDS")
    if env_pw:
        passwords += [p for p in env_pw.split(",") if p]

    return Config(
        data_dir=data_dir,
        gmail_query=gmail.get("query", Config.gmail_query),
        gmail_after=gmail.get("after", ""),
        pdf_passwords=passwords,
        ocr_mode=ocr.get("mode", "never"),
        ocr_language=ocr.get("language", "eng"),
        ocr_dpi=int(ocr.get("dpi", 300)),
        ocr_psm=int(ocr.get("psm", 6)),
        parser=parser.get("name", "ndb"),
        account=parser.get("account", ""),
        ignore_attachments=list(parser.get("ignore_attachments", [])),
        currency=parser.get("currency", "LKR"),
    )
