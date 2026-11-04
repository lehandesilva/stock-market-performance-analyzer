"""Parsers for NDB Securities (Sri Lanka) documents.

Each document type has a fixed set of known layouts. Anything that doesn't
match one exactly - or fails its arithmetic checks - raises ParseError so the
pipeline stops instead of storing doubtful numbers.
"""

from __future__ import annotations

import re

from ..models import ParsedDocument, ParseError
from . import contract_note, receipt, statement, trade_confirmation
from .common import BROKER_MARKER


def classify(text: str) -> str:
    if BROKER_MARKER not in text:
        raise ParseError("not an NDB Securities document")
    if re.search(r"\b(BOUGHT|SOLD) ?Note\b", text):
        return "contract_note"
    if "TRADE CONFIRMATION" in text:
        return "trade_confirmation"
    if "Statement of Accounts From" in text:
        return "statement"
    if "Receipt No" in text and "Received with thanks" in text:
        return "receipt"
    raise ParseError("unrecognised NDB document type")


PARSERS = {
    "contract_note": contract_note.parse,
    "trade_confirmation": trade_confirmation.parse,
    "statement": statement.parse,
    "receipt": receipt.parse,
}


def parse_document(text: str) -> ParsedDocument:
    return PARSERS[classify(text)](text)
