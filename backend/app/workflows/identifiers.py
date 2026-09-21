"""Deterministic identifier typing. Raw OCR is never promoted by a guess."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TypedIdentifier:
    raw: str
    normalized: str
    kind: str
    valid: bool
    reason: str | None = None


def type_identifier(raw: str, declared_kind: str | None = None) -> TypedIdentifier:
    value = re.sub(r"[\s-]", "", raw).upper()
    kind = (declared_kind or "auto").lower()
    if kind == "library_barcode":
        return TypedIdentifier(raw, value, kind, bool(value), None if value else "empty barcode")
    if kind == "issn" or (kind == "auto" and len(value) == 8):
        valid = len(value) == 8 and bool(re.fullmatch(r"[0-9]{7}[0-9X]", value))
        if valid:
            valid = (
                sum((8 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(value)) % 11 == 0
            )
        return TypedIdentifier(
            raw, value, "issn", valid, None if valid else "invalid ISSN checksum"
        )
    if kind == "isbn10" or (kind in {"isbn", "auto"} and len(value) == 10):
        valid = bool(re.fullmatch(r"[0-9]{9}[0-9X]", value))
        if valid:
            valid = (
                sum((10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(value)) % 11 == 0
            )
        return TypedIdentifier(
            raw, value, "isbn10", valid, None if valid else "invalid ISBN-10 checksum"
        )
    # Strip a five digit EAN supplement only when it is visibly separated.
    match = re.fullmatch(r"\s*([0-9\s-]{13,})\s+[0-9]{5}\s*", raw)
    if match:
        value = re.sub(r"[\s-]", "", match.group(1))
    if kind in {"ean13", "isbn13", "isbn", "auto"} and len(value) == 13:
        numeric = bool(value.isdigit())
        checksum = numeric and (
            sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(value)) % 10 == 0
        )
        if kind == "ean13" and not value.startswith(("978", "979")):
            return TypedIdentifier(
                raw, value, "ean13", checksum, None if checksum else "invalid EAN checksum"
            )
        valid = checksum and value.startswith(("978", "979"))
        return TypedIdentifier(
            raw,
            value,
            "isbn13",
            valid,
            None if valid else "invalid ISBN-13 checksum or non-book prefix",
        )
    return TypedIdentifier(raw, value, kind, False, "unrecognized identifier format")
