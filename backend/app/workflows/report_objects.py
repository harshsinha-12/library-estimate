"""Luna-normalized object prices for the survey PDF.

Redis live_searches and found_prices stay the amount source. The small model
only names, categorizes, and deduplicates those records.
"""

from __future__ import annotations

import json
import os
import re

from backend.app.providers.pricing.schema import DEFAULT_SMALL_MODEL
from backend.app.providers.pricing.small_model import complete_json
from backend.app.utils.hashing import sha256_bytes
from backend.app.utils.json_codec import canonical_json_bytes

OBJECT_CATEGORIES = (
    "painting",
    "portrait",
    "sculpture",
    "computer",
    "monitor",
    "printer",
    "furniture",
    "shelf",
    "appliance",
    "decorative_object",
    "other",
)
REPORT_OBJECTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["objects"],
    "properties": {
        "objects": {
            "type": "array",
            "maxItems": 24,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "category",
                    "amount",
                    "currency",
                    "status",
                    "listing_url",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": list(OBJECT_CATEGORIES)},
                    "amount": {"type": ["number", "null"]},
                    "currency": {"type": ["string", "null"]},
                    "status": {
                        "type": "string",
                        "enum": ["draft", "unresolved", "confirmed"],
                    },
                    "listing_url": {"type": ["string", "null"]},
                },
            },
        }
    },
}
_SKIP_CATEGORIES = {"book", "serial", "cup"}
_BOOK_KINDS = {"isbn", "book"}
_CATEGORY_ALIASES = {
    "home_appliance": "appliance",
    "home appliance": "appliance",
    "air_conditioner": "appliance",
    "ac": "appliance",
    "computer_accessory": "monitor",
    "computer accessory": "monitor",
    "laptop": "computer",
    "wardrobe": "furniture",
    "cupboard": "furniture",
    "table": "furniture",
    "bed": "furniture",
}


def structure_report_objects(
    records: list[dict],
    *,
    model: str | None = None,
    complete=None,
) -> list[dict]:
    compact = [_compact(item) for item in records]
    compact = [item for item in compact if item]
    if not compact:
        return []
    parsed = None
    complete = complete or complete_json
    try:
        parsed = complete(
            _prompt(compact),
            schema=REPORT_OBJECTS_SCHEMA,
            schema_name="report_objects",
            model=model or os.getenv("OPENAI_SMALL_MODEL", "").strip() or DEFAULT_SMALL_MODEL,
            reason=(
                "Structure Redis object prices into unique PDF rows "
                "without inventing amounts"
            ),
        )
    except (OSError, TypeError, ValueError):
        parsed = None
    structured = _from_model(parsed, compact) if isinstance(parsed, dict) else []
    return structured or _from_records(compact)


def compact_price_records(valuation: dict) -> list[dict]:
    rows: list[dict] = []
    for item in valuation.get("live_searches") or []:
        rows.append({**item, "source": "live_search"})
    for item in valuation.get("found_prices") or []:
        rows.append({**item, "source": "found_price"})
    for item in valuation.get("copies") or []:
        if item.get("category") in _SKIP_CATEGORIES:
            continue
        rows.append({**item, "source": "copy"})
    return [_compact(item) for item in rows if _compact(item)]


def records_fingerprint(records: list[dict]) -> str:
    return sha256_bytes(canonical_json_bytes(records))


def _prompt(records: list[dict]) -> str:
    return (
        "Normalize these Redis replacement-cost records into unique physical objects "
        "for an insurance PDF. Drop books, serials, mugs, and cups. Deduplicate the "
        "same object even if the name is worded twice. Do not invent an amount, "
        "currency, or listing URL; copy those from the matching record. Map messy "
        "labels onto the category enum. Status is confirmed only for quoted or manual "
        "records, draft when an amount exists, otherwise unresolved.\n"
        f"records:\n{json.dumps(records, ensure_ascii=False, default=str)[:8000]}"
    )


def _compact(item: dict) -> dict | None:
    name = str(
        item.get("name")
        or item.get("title")
        or item.get("label")
        or item.get("query")
        or ""
    ).strip()
    if len(name) < 3:
        return None
    kind = str(item.get("query_kind") or "").lower()
    category = _category(item)
    if kind in _BOOK_KINDS or category in _SKIP_CATEGORIES:
        return None
    if kind == "name" and category == "other":
        return None
    if name.lower().startswith(("unidentified book", "books on the")):
        return None
    amount = item.get("amount")
    if amount is None:
        amount = ((item.get("valuation") or {}).get("amount") or {}).get("value")
    currency = item.get("currency") or (item.get("valuation") or {}).get("currency")
    status = str(item.get("status") or item.get("valuation_status") or "").lower()
    if status in {"quoted", "manual"}:
        status = "confirmed"
    elif amount is not None:
        status = "draft"
    else:
        status = "unresolved"
    return {
        "name": name[:160],
        "category": category,
        "query_kind": kind or "object",
        "amount": _number(amount),
        "currency": str(currency).upper()[:3] if currency else None,
        "status": status,
        "listing_url": item.get("listing_url") or item.get("url"),
        "source": item.get("source") or "redis",
    }


def _category(item: dict) -> str:
    raw = str(item.get("category") or "").strip().lower().replace("-", "_")
    raw = _CATEGORY_ALIASES.get(raw, raw)
    if raw in OBJECT_CATEGORIES:
        return raw
    title = str(item.get("title") or item.get("label") or item.get("query") or "").lower()
    if any(token in title for token in ("macbook", "ipad", "laptop", "computer")):
        return "computer"
    if "monitor" in title:
        return "monitor"
    if any(token in title for token in ("air conditioner", "air-conditioner")) or (
        " ac" in f" {title} "
    ):
        return "appliance"
    if any(token in title for token in ("bed", "cupboard", "wardrobe", "table")):
        return "furniture"
    if str(item.get("query_kind") or "") in _BOOK_KINDS:
        return "book"
    return "other"


def _from_model(parsed: dict, records: list[dict]) -> list[dict]:
    rows = parsed.get("objects") if isinstance(parsed.get("objects"), list) else []
    out: list[dict] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if len(name) < 3 or name.lower() in seen:
            continue
        source = _match_record(name, records)
        if source is None:
            continue
        category = (
            item.get("category")
            if item.get("category") in OBJECT_CATEGORIES
            else source["category"]
        )
        if category in _SKIP_CATEGORIES:
            continue
        seen.add(name.lower())
        status = item.get("status")
        if status not in {"draft", "unresolved", "confirmed"}:
            status = source["status"]
        out.append(_display_row(
            name=name,
            category=category,
            amount=source.get("amount"),
            currency=source.get("currency") or item.get("currency"),
            status=status,
            listing_url=source.get("listing_url") or item.get("listing_url"),
        ))
    return out


def _from_records(records: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in records:
        name = item["name"]
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(_display_row(
            name=name,
            category=item["category"],
            amount=item.get("amount"),
            currency=item.get("currency"),
            status=item["status"],
            listing_url=item.get("listing_url"),
        ))
    return out


def _display_row(
    *,
    name: str,
    category: str,
    amount: object,
    currency: str | None,
    status: str,
    listing_url: str | None,
) -> dict:
    valuation_status = {
        "confirmed": "quoted",
        "draft": "price_pending",
        "unresolved": "unresolved",
    }.get(status, "price_pending")
    return {
        "asset_copy_id": f"object-{len(name)}",
        "title": name,
        "label": name,
        "category": category,
        "query_kind": "object",
        "valuation_status": valuation_status,
        "draft_count": 1 if amount is not None else 0,
        "listing_url": listing_url,
        "valuation": None
        if amount is None
        else {"amount": {"value": amount}, "currency": currency},
    }


def _match_record(name: str, records: list[dict]) -> dict | None:
    lowered = name.lower()
    exact = next((item for item in records if item["name"].lower() == lowered), None)
    if exact:
        return exact
    contained = next(
        (
            item
            for item in records
            if lowered in item["name"].lower() or item["name"].lower() in lowered
        ),
        None,
    )
    if contained:
        return contained
    tokens = {part for part in re.split(r"\W+", lowered) if len(part) >= 4}
    best = None
    score = 0
    for item in records:
        other = {part for part in re.split(r"\W+", item["name"].lower()) if len(part) >= 4}
        overlap = len(tokens & other)
        if overlap > score:
            best, score = item, overlap
    return best if score else None


def _number(value: object) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if abs(number - round(number)) < 1e-6:
        return int(round(number))
    return number
