"""Store downloaded/imported PDFs in data/raw and register them in the DB."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .config import Config


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "attachment"
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def store_pdf(
    cfg: Config,
    conn: sqlite3.Connection,
    data: bytes,
    *,
    source: str,
    message_id: str,
    attachment_name: str,
    email_date: date | None,
    subject: str = "",
    sender: str = "",
) -> bool:
    """Save PDF bytes and insert a documents row. Returns False if already known."""
    exists = conn.execute(
        "SELECT 1 FROM documents WHERE message_id = ? AND attachment_name = ?",
        (message_id, attachment_name),
    ).fetchone()
    if exists:
        return False

    sha = hashlib.sha256(data).hexdigest()
    # Identical file already stored (e.g. re-sent email): reuse it on disk.
    # Transactions are de-duplicated across documents during analysis.
    same = conn.execute("SELECT path FROM documents WHERE sha256 = ?", (sha,)).fetchone()
    if same:
        rel = same["path"]
    else:
        prefix = email_date.isoformat() if email_date else "undated"
        fname = f"{prefix}_{sha[:8]}_{_safe_name(attachment_name)}"
        dest = cfg.raw_dir / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        rel = str(dest.relative_to(cfg.data_dir))

    conn.execute(
        """INSERT INTO documents (source, message_id, attachment_name, email_date, subject,
                                  sender, path, sha256, downloaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source,
            message_id,
            attachment_name,
            email_date.isoformat() if email_date else None,
            subject,
            sender,
            rel,
            sha,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return True


def import_local(cfg: Config, conn: sqlite3.Connection, folder: Path) -> int:
    """Import PDFs from a local folder (e.g. statements you downloaded manually)."""
    count = 0
    for pdf in sorted(Path(folder).rglob("*")):
        if not (pdf.is_file() and pdf.suffix.lower() == ".pdf"):
            continue
        data = pdf.read_bytes()
        mtime = datetime.fromtimestamp(pdf.stat().st_mtime).date()
        if store_pdf(
            cfg,
            conn,
            data,
            source="local",
            message_id=f"local:{hashlib.sha256(data).hexdigest()}",
            attachment_name=pdf.name,
            email_date=mtime,
        ):
            count += 1
    return count
