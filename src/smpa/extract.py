"""Turn statement PDFs into plain text: embedded text layer first, OCR as fallback."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from .config import Config

# Pages with fewer characters than this are treated as scanned images and OCR'd.
MIN_TEXT_CHARS = 25
PAGE_BREAK = "\f"


class PasswordError(Exception):
    pass


def _is_password_error(exc: BaseException) -> bool:
    return isinstance(exc, PDFPasswordIncorrect) or any(
        isinstance(a, PDFPasswordIncorrect) for a in getattr(exc, "args", ())
    )


def find_password(path: Path, passwords: list[str]) -> str:
    """Return the first password that opens the PDF ('' if unencrypted)."""
    for pw in ["", *passwords]:
        try:
            with pdfplumber.open(path, password=pw) as pdf:
                _ = pdf.pages
            return pw
        except Exception as exc:
            if not _is_password_error(exc):
                raise
    raise PasswordError(
        "PDF is encrypted and none of the configured passwords work "
        "(set [pdf].passwords in config.toml)"
    )


def ocr_page(path: Path, page_no: int, password: str, cfg: Config) -> str:
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(
        str(path),
        dpi=cfg.ocr_dpi,
        first_page=page_no,
        last_page=page_no,
        userpw=password or None,
    )
    return "\n".join(
        pytesseract.image_to_string(img, lang=cfg.ocr_language, config=f"--psm {cfg.ocr_psm}")
        for img in images
    )


def extract_pdf(path: Path, cfg: Config) -> tuple[str, str]:
    """Return (text, method). Pages are separated by form feeds."""
    password = find_password(path, cfg.pdf_passwords)
    pages: list[str] = []
    methods: set[str] = set()
    with pdfplumber.open(path, password=password) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = "" if cfg.ocr_mode == "always" else (page.extract_text() or "")
            if len(text.strip()) < MIN_TEXT_CHARS:
                text = ocr_page(path, i, password, cfg)
                methods.add("ocr")
            else:
                methods.add("text")
            pages.append(text)
    method = "mixed" if len(methods) > 1 else (methods.pop() if methods else "empty")
    return PAGE_BREAK.join(pages), method


def extract_all(cfg: Config, conn: sqlite3.Connection, force: bool = False) -> tuple[int, int]:
    """Extract text for documents that don't have it yet. Returns (ok, failed)."""
    where = "" if force else "WHERE extracted_at IS NULL OR extract_error IS NOT NULL"
    rows = conn.execute(f"SELECT id, path FROM documents {where} ORDER BY id").fetchall()
    ok = failed = 0
    for row in rows:
        pdf_path = cfg.data_dir / row["path"]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            text, method = extract_pdf(pdf_path, cfg)
        except Exception as exc:  # keep going; error is shown in the dashboard
            conn.execute(
                "UPDATE documents SET extract_error = ?, extracted_at = ? WHERE id = ?",
                (f"{type(exc).__name__}: {exc}", now, row["id"]),
            )
            conn.commit()
            failed += 1
            continue
        txt_path = cfg.text_dir / (Path(row["path"]).stem + f"_{row['id']}.txt")
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(text)
        conn.execute(
            """UPDATE documents
               SET text_path = ?, extract_method = ?, extracted_at = ?, extract_error = NULL,
                   parsed_at = NULL
               WHERE id = ?""",
            (str(txt_path.relative_to(cfg.data_dir)), method, now, row["id"]),
        )
        conn.commit()
        ok += 1
    return ok, failed
