"""Shelf photos go straight to a language model.

Each JPEG is one request. The model identifies readable books, estimates how
many books are in the picture, looks up physical prices with web search, and
returns the shelf total. This path does not run the spine counter.
"""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from backend.app.domain.models import (
    AssetCopy,
    InventoryResult,
    Measurement,
    Observation,
    ShelfFaceDataSize,
    Track,
)
from backend.app.domain.repository import SurveyRepository
from backend.app.providers.llm_trace import record_llm_call
from backend.app.providers.pricing import log as pricing_log
from backend.app.providers.pricing.targets import object_key
from backend.app.providers.pricing.web_search import DEFAULT_WEB_SEARCH_MODEL, _parse_output
from backend.app.providers.usage import record_usage, reserve_budget
from backend.app.utils.clocks import utc_now
from backend.app.utils.hashing import sha256_bytes
from backend.app.utils.json_codec import canonical_json_bytes

PIPELINE_VERSION = "shelf-photo-llm-v1"
MANIFEST_PATH = "shelf_photos/manifest.json"
MAX_IMAGE_BYTES = 2_000_000
MAX_COPIES_PER_PHOTO = 250
MAX_COPIES_PER_TITLE = 80

SHELF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "estimated_book_count",
        "unidentified_count",
        "currency",
        "shelf_total",
        "calculation_note",
        "books",
    ],
    "properties": {
        "estimated_book_count": {"type": "integer"},
        "unidentified_count": {"type": "integer"},
        "currency": {"type": "string"},
        "shelf_total": {"type": ["number", "null"]},
        "calculation_note": {"type": "string"},
        "books": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "author", "count", "unit_price", "listing_url"],
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": ["string", "null"]},
                    "count": {"type": "integer"},
                    "unit_price": {"type": ["number", "null"]},
                    "listing_url": {"type": ["string", "null"]},
                },
            },
        },
    },
}


def read_shelf_photo(
    jpeg: bytes,
    *,
    geography: dict,
    shelf_label: str,
) -> dict | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    model = DEFAULT_WEB_SEARCH_MODEL
    reason = (
        "Shelf photo: identify visible books, estimate the count, "
        "look up physical prices, and calculate the shelf total"
    )
    query = {
        "shelf": shelf_label,
        "image_jpeg_bytes": len(jpeg),
        "country": geography.get("country_code"),
        "currency": geography.get("currency"),
    }
    if not key:
        record_llm_call(
            reason=reason, operation="shelf_photo", provider="openai", model=model,
            query=query, status="skipped", error="missing_openai_key",
        )
        return None
    if not jpeg or len(jpeg) > MAX_IMAGE_BYTES:
        record_llm_call(
            reason=reason, operation="shelf_photo", provider="openai", model=model,
            query=query, status="skipped", error="image_missing_or_too_large",
        )
        return None
    prompt = _prompt(geography, shelf_label)
    encoded = base64.b64encode(jpeg).decode("ascii")
    body = {
        "model": model,
        "tool_choice": "auto",
        "tools": [
            {
                "type": "web_search",
                "user_location": {
                    "type": "approximate",
                    "country": str(geography.get("country_code") or "IN"),
                    "city": str(geography.get("city") or "") or None,
                    "region": str(geography.get("region") or "") or None,
                },
            }
        ],
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "shelf_photo",
                "strict": True,
                "schema": SHELF_SCHEMA,
            }
        },
    }
    location = body["tools"][0]["user_location"]
    body["tools"][0]["user_location"] = {
        name: value for name, value in location.items() if value
    }
    reserve_budget("0.40")
    started = time.monotonic()
    try:
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=120) as response:
            payload = json.load(response)
        latency_ms = round((time.monotonic() - started) * 1000)
        record_usage(
            provider="openai", model=model, operation="shelf_photo",
            response=payload, latency_ms=latency_ms,
        )
        record_llm_call(
            reason=reason, operation="shelf_photo", provider="openai", model=model,
            query={**query, "prompt": prompt}, response=payload, latency_ms=latency_ms,
        )
        pricing_log.info("shelf_photo http_ok", model=model, shelf=shelf_label)
        return _parse_output(payload)
    except HTTPError as error:
        detail = error.read()[:300].decode("utf-8", errors="replace")
        record_llm_call(
            reason=reason, operation="shelf_photo", provider="openai", model=model,
            query=query, status="error",
            error=f"http_{error.code}: {detail.replace(chr(10), ' ')}",
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        record_llm_call(
            reason=reason, operation="shelf_photo", provider="openai", model=model,
            query=query, status="error", error=error.__class__.__name__,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return None


def interpret_shelf_reading(raw: dict | None, *, currency: str) -> dict:
    """Turn one model payload into counted, priced lines. Junk is dropped."""
    fallback_currency = _currency(currency, "INR")
    if not isinstance(raw, dict):
        return {
            "estimated_book_count": 0,
            "unidentified_count": 0,
            "currency": fallback_currency,
            "model_shelf_total": None,
            "line_total": 0.0,
            "calculation_note": "The model did not return a shelf reading.",
            "books": [],
            "error": "unreadable_model_response",
        }
    money = _currency(raw.get("currency"), fallback_currency)
    books: list[dict] = []
    identified = 0
    line_total = 0.0
    for item in raw.get("books") or []:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        if len(title) < 2:
            continue
        count = _count(item.get("count"), MAX_COPIES_PER_TITLE)
        if count < 1:
            continue
        if identified + count > MAX_COPIES_PER_PHOTO:
            count = MAX_COPIES_PER_PHOTO - identified
        if count < 1:
            break
        price = _price(item.get("unit_price"))
        author = " ".join(str(item.get("author") or "").split()) or None
        url = str(item.get("listing_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            url = None
        books.append(
            {
                "title": title[:180],
                "author": author[:120] if author else None,
                "count": count,
                "unit_price": price,
                "currency": money,
                "listing_url": url,
            }
        )
        identified += count
        if price is not None:
            line_total += price * count
    unidentified = _count(raw.get("unidentified_count"), MAX_COPIES_PER_PHOTO)
    estimated = _count(raw.get("estimated_book_count"), MAX_COPIES_PER_PHOTO)
    if identified + unidentified > MAX_COPIES_PER_PHOTO:
        unidentified = MAX_COPIES_PER_PHOTO - identified
    if estimated < identified + unidentified:
        estimated = identified + unidentified
    if estimated > identified + unidentified:
        room = MAX_COPIES_PER_PHOTO - identified - unidentified
        extra = min(estimated - identified - unidentified, room)
        unidentified += extra
    note = " ".join(str(raw.get("calculation_note") or "").split())[:500]
    model_total = _price(raw.get("shelf_total"))
    return {
        "estimated_book_count": identified + unidentified,
        "unidentified_count": unidentified,
        "currency": money,
        "model_shelf_total": model_total,
        "line_total": round(line_total, 2),
        "calculation_note": note or "No calculation note.",
        "books": books,
        "error": None,
    }


class ShelfPhotoWorker:
    def __init__(self, reader=None) -> None:
        self.reader = reader or read_shelf_photo

    def process(self, repository: SurveyRepository, survey_id: UUID) -> InventoryResult | None:
        if not repository.exists_bytes(survey_id, MANIFEST_PATH):
            return None
        raw_manifest = repository.get_bytes(survey_id, MANIFEST_PATH)
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            manifest = None
        photo_bytes = _photo_bytes(repository, survey_id, manifest)
        digest = sha256_bytes(raw_manifest + b"\0" + canonical_json_bytes(
            {path: sha256_bytes(blob) for path, blob in photo_bytes.items()}
        ))
        job_key = f"{survey_id}:shelf-photo:{digest}:{PIPELINE_VERSION}"
        existing = repository.get_job(job_key)
        if existing is not None:
            return _restore(repository, survey_id, existing["result"])
        geography = repository.get(survey_id).geography.model_dump(mode="json")
        built = _build(survey_id, manifest, photo_bytes, geography, self.reader)
        repository.save_job(job_key, {
            "job_key": job_key,
            "survey_id": str(survey_id),
            "stage": "shelf_photo",
            "status": built["inventory"].status,
            "input_hash": digest,
            "pipeline_version": PIPELINE_VERSION,
            "result": built["stored"],
        })
        return _restore(repository, survey_id, built["stored"])


def _build(
    survey_id: UUID, manifest, photo_bytes: dict[str, bytes], geography: dict, reader,
) -> dict:
    run_id = str(uuid4())
    currency = str(geography.get("currency") or "INR")
    observations: list[Observation] = []
    copies: list[AssetCopy] = []
    tracks: list[Track] = []
    faces: list[ShelfFaceDataSize] = []
    identities: list[dict] = []
    price_rows: list[dict] = []
    live: list[dict] = []
    shelf_rows: list[dict] = []
    recapture: list[str] = []
    seen_live: set[str] = set()

    shelves = manifest.get("shelves") if isinstance(manifest, dict) else None
    if not isinstance(shelves, list) or not shelves:
        recapture.append("Add a photo of at least one shelf.")
        shelves = []

    for index, shelf in enumerate(shelves, start=1):
        if not isinstance(shelf, dict):
            continue
        shelf_id = str(shelf.get("shelf_id") or f"shelf_{index:02d}")
        label = str(shelf.get("label") or f"Shelf {index}").strip() or f"Shelf {index}"
        paths = [path for path in shelf.get("photos") or [] if isinstance(path, str)]
        photo_reports = []
        shelf_obs: list[str] = []
        shelf_count = 0
        shelf_unidentified = 0
        shelf_bytes = 0
        for path in paths:
            blob = photo_bytes.get(path)
            if not blob:
                recapture.append(f"Missing shelf photo {path}")
                photo_reports.append({"path": path, "error": "missing"})
                continue
            shelf_bytes += len(blob)
            reading = interpret_shelf_reading(
                reader(blob, geography=geography, shelf_label=label),
                currency=currency,
            )
            photo_reports.append({"path": path, **reading})
            if reading["error"]:
                recapture.append(f"Could not read {path}")
            made = _copies_for_photo(
                survey_id=survey_id,
                run_id=run_id,
                shelf_id=shelf_id,
                label=label,
                path=path,
                reading=reading,
                geography=geography,
            )
            observations.extend(made["observations"])
            copies.extend(made["copies"])
            identities.extend(made["identities"])
            price_rows.extend(made["prices"])
            shelf_obs.extend(item.observation_id for item in made["observations"])
            shelf_count += reading["estimated_book_count"]
            shelf_unidentified += reading["unidentified_count"]
            for book in reading["books"]:
                key = book["title"].lower()
                if key in seen_live or book["unit_price"] is None:
                    continue
                seen_live.add(key)
                live.append(
                    {
                        "title": book["title"],
                        "query": book["title"],
                        "query_kind": "name",
                        "category": "book",
                        "amount": book["unit_price"],
                        "currency": book["currency"],
                        "listing_url": book["listing_url"],
                        "status": "draft",
                        "reason": "Language model shelf photo",
                    }
                )
        if shelf_obs:
            tracks.append(
                Track(
                    track_id=f"track_{shelf_id}",
                    observation_refs=shelf_obs,
                    status="needs_review" if shelf_unidentified else "ok",
                    face_id=shelf_id,
                    row_id="row_01",
                )
            )
        faces.append(
            _face(
                shelf_id, shelf_count, shelf_unidentified, shelf_bytes, paths, run_id,
                status="needs_review" if shelf_unidentified or not shelf_count else "ok",
            )
        )
        shelf_rows.append(
            {
                "shelf_id": shelf_id,
                "label": label,
                "estimated_book_count": shelf_count,
                "unidentified_count": shelf_unidentified,
                "photos": photo_reports,
            }
        )

    if not copies and "Add a photo of at least one shelf." not in recapture:
        recapture.append("No books were recorded from the shelf photos.")
    status = "failed" if not copies else ("needs_review" if recapture else "ok")
    if copies and any(row["unidentified_count"] for row in shelf_rows):
        status = "needs_review"
    inventory = InventoryResult(
        survey_id=survey_id,
        status=status,  # type: ignore[arg-type]
        pipeline_version=PIPELINE_VERSION,
        run_id=run_id,
        observations=observations,
        tracks=tracks,
        asset_copies=copies,
        shelf_face_data_sizes=faces,
        recapture=recapture,
    )
    line_total = round(
        sum(
            float(row["unit_price"]) * int(row["count"])
            for shelf in shelf_rows
            for photo in shelf["photos"]
            for row in photo.get("books") or []
            if row.get("unit_price") is not None
        ),
        2,
    )
    model_totals = [
        float(photo["model_shelf_total"])
        for shelf in shelf_rows
        for photo in shelf["photos"]
        if isinstance(photo.get("model_shelf_total"), int | float)
    ]
    shelf_llm = {
        "schema_version": PIPELINE_VERSION,
        "currency": currency,
        "estimated_book_count": sum(row["estimated_book_count"] for row in shelf_rows),
        "unidentified_count": sum(row["unidentified_count"] for row in shelf_rows),
        "line_total": line_total,
        "model_total": round(sum(model_totals), 2) if model_totals else None,
        "shelves": shelf_rows,
        "note": (
            "Each photo is counted on its own. "
            "The line total is the sum of identified copies times the looked-up unit price. "
            "Unidentified books are counted and left unpriced."
        ),
    }
    stored = {
        "inventory": inventory.model_dump(mode="json"),
        "stage3": {
            "schema_version": PIPELINE_VERSION,
            "source": "llm_shelf_photo",
            "speech_status": "absent",
            "assets": [],
            "identities": identities,
            "notes": [],
            "queue": [],
            "damage": [],
        },
        "pricing": _pricing_document(geography, price_rows, live),
        "shelf_llm": shelf_llm,
    }
    return {"inventory": inventory, "stored": stored}


def _copies_for_photo(
    *,
    survey_id: UUID,
    run_id: str,
    shelf_id: str,
    label: str,
    path: str,
    reading: dict,
    geography: dict,
) -> dict:
    observations: list[Observation] = []
    copies: list[AssetCopy] = []
    identities: list[dict] = []
    prices: list[dict] = []
    slot = 0
    lines = list(reading["books"])
    if reading["unidentified_count"]:
        lines.append(
            {
                "title": f"Unidentified books on {label}",
                "author": None,
                "count": reading["unidentified_count"],
                "unit_price": None,
                "currency": reading["currency"],
                "listing_url": None,
            }
        )
    for book in lines:
        for _ in range(int(book["count"])):
            slot += 1
            asset_id = f"copy_{shelf_id}_{slot}_{uuid4().hex[:8]}"
            observation_id = f"obs_{asset_id}"
            observations.append(
                Observation(
                    observation_id=observation_id,
                    evidence_ref=path,
                    captured_at=utc_now(),
                    category="book",
                    confidence=0.35 if book["unit_price"] is None else 0.6,
                    asset_copy_id=asset_id,
                    shelf_id=shelf_id,
                    face_id=shelf_id,
                    row_id="row_01",
                    slot=slot,
                    readable=book["unit_price"] is not None,
                )
            )
            copies.append(
                AssetCopy(
                    asset_copy_id=asset_id,
                    category="book",
                    observation_refs=[observation_id],
                    shelf_id=shelf_id,
                    face_id=shelf_id,
                    row_id="row_01",
                    slot=slot,
                )
            )
            identities.append(
                {
                    "asset_copy_id": asset_id,
                    "title": book["title"],
                    "authors": [book["author"]] if book.get("author") else [],
                    "usable_for_isbn_price_query": False,
                    "valid": False,
                }
            )
            if book["unit_price"] is None:
                continue
            edition = object_key(
                kind="name", title=book["title"], category="book", isbn=None,
            )
            prices.append(
                {
                    "schema_version": "1.0.0",
                    "price_observation_id": str(uuid4()),
                    "book_edition_id": edition,
                    "asset_copy_id": asset_id,
                    "search_id": None,
                    "query": book["title"],
                    "query_kind": "name",
                    "market": geography.get("market"),
                    "currency": book["currency"],
                    "source_url": book.get("listing_url") or "",
                    "listing_url": book.get("listing_url"),
                    "observed_at": utc_now().isoformat(),
                    "amount": book["unit_price"],
                    "parsed_amount": book["unit_price"],
                    "shipping": None,
                    "condition": None,
                    "format": None,
                    "offer_type": "physical",
                    "review_status": "accepted",
                    "title": book["title"],
                    "snippet": reading["calculation_note"],
                    "evidence_hash": sha256_bytes(
                        f"{survey_id}|{path}|{book['title']}|{book['unit_price']}".encode()
                    ),
                    "reason": (
                        "Language model estimate from the shelf photo and a web price lookup"
                    ),
                    "parser": "llm_shelf_photo",
                }
            )
    return {
        "observations": observations,
        "copies": copies,
        "identities": identities,
        "prices": prices,
    }


def _face(
    shelf_id: str,
    count: int,
    unidentified: int,
    evidence_bytes: int,
    paths: list[str],
    run_id: str,
    *,
    status: str,
) -> ShelfFaceDataSize:
    def measure(value: float, unit: str, method: str, row_status: str = status) -> Measurement:
        return Measurement(
            value=value,
            unit=unit,
            status=row_status,  # type: ignore[arg-type]
            confidence=0.6 if count else 0.2,
            method=method,
            evidence_refs=paths,
            run_id=run_id,
        )

    return ShelfFaceDataSize(
        shelf_face_id=shelf_id,
        occupied_length=measure(0, "m", "not-measured", "partial"),
        capacity_length=measure(0, "m", "not-measured", "partial"),
        fill_ratio=measure(0, "ratio", "not-measured", "partial"),
        copy_count=measure(count, "count", "llm-shelf-photo"),
        unresolved_count=measure(unidentified, "count", "llm-shelf-photo"),
        evidence_bytes=measure(evidence_bytes, "byte", "shelf-photo"),
        rows=[{"row_id": "row_01", "copy_count": count, "unidentified_count": unidentified}],
    )


def _pricing_document(geography: dict, observations: list[dict], live: list[dict]) -> dict:
    return {
        "schema_version": "stage4-v1",
        "pipeline_version": "stage4-price-v1",
        "source": "llm_shelf_photo",
        "observations": observations,
        "searches": [],
        "no_comparable": {},
        "live_searches": live,
        "found_prices": [],
        "search_attempts": {},
        "log": [],
        "ledger": {"currency": "USD", "lines": []},
        "market": geography.get("market"),
    }


def _restore(repository: SurveyRepository, survey_id: UUID, stored: dict) -> InventoryResult:
    inventory = InventoryResult.model_validate(stored["inventory"])
    repository.save_json(survey_id, "inventory", inventory.model_dump(mode="json"))
    repository.save_json(survey_id, "stage3", stored["stage3"])
    repository.save_json(survey_id, "pricing", stored["pricing"])
    repository.save_json(survey_id, "shelf_llm", stored["shelf_llm"])
    dumped = inventory.model_dump(mode="json")
    repository.save_json(
        survey_id,
        "ir",
        {
            "schema_version": "1.0.0",
            "survey_id": str(survey_id),
            "source": "llm_shelf_photo",
            "observations": dumped["observations"],
            "tracks": dumped["tracks"],
            "asset_copies": dumped["asset_copies"],
            "shelf_face_data_sizes": dumped["shelf_face_data_sizes"],
        },
    )
    return inventory


def _photo_bytes(repository: SurveyRepository, survey_id: UUID, manifest) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    shelves = manifest.get("shelves") if isinstance(manifest, dict) else None
    if not isinstance(shelves, list):
        return found
    for shelf in shelves:
        if not isinstance(shelf, dict):
            continue
        for path in shelf.get("photos") or []:
            if (
                isinstance(path, str)
                and path.startswith("shelf_photos/")
                and ".." not in path
                and repository.exists_bytes(survey_id, path)
            ):
                found[path] = repository.get_bytes(survey_id, path)
    return found


def _prompt(geography: dict, shelf_label: str) -> str:
    return (
        "This is one photograph of a library shelf. "
        f"Shelf label: {shelf_label}. "
        f"City: {geography.get('city') or ''}. "
        f"Region: {geography.get('region') or ''}. "
        f"Country: {geography.get('country_code') or 'IN'}. "
        f"Currency: {geography.get('currency') or 'INR'}. "
        "Do all of the following from this image alone. "
        "Estimate how many physical books are visible, including books you cannot name. "
        "Identify each distinct title you can read or confidently recognize. "
        "Do not invent a title, author, or ISBN. "
        "Count the visible copies of each identified title in this photo. "
        "Use web search to find a current physical paperback or hardcover purchase price "
        "in this city and currency for each identified title. "
        "Exclude Kindle, eBook, and rental. "
        "If you cannot find a physical price, set unit_price to null. "
        "Leave books you cannot name in unidentified_count with no price. "
        "shelf_total is the sum of count times unit_price for priced titles only. "
        "calculation_note shows that arithmetic in one sentence. "
        "Return JSON matching the schema only."
    )


def _count(value: object, limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, min(limit, int(value)))


def _price(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    amount = float(value)
    if amount <= 0 or amount > 1_000_000:
        return None
    return round(amount, 2)


def _currency(value: object, fallback: str) -> str:
    text = str(value or "").strip().upper()
    if len(text) == 3 and text.isalpha():
        return text
    return fallback
