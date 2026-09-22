"""OpenAI Responses API web_search for physical-copy prices."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app.providers.llm_trace import record_llm_call
from backend.app.providers.pricing import log as pricing_log
from backend.app.providers.pricing.parse import is_shop_url, parse_prices
from backend.app.providers.pricing.schema import (
    BATCH_PRICE_SCHEMA,
    BATCH_SCHEMA_NAME,
    BATCH_SIZE,
    MAX_LISTING_URLS,
    OFFER_TYPES,
    PRICE_SCHEMA,
    PRICE_SCHEMA_NAME,
)
from backend.app.providers.usage import record_usage, reserve_budget
from backend.app.utils.hashing import sha256_bytes

DEFAULT_WEB_SEARCH_MODEL = (
    os.getenv("OPENAI_WEB_SEARCH_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
)
WEB_SEARCH_MODELS = tuple(
    dict.fromkeys(
        [
            os.getenv("OPENAI_WEB_SEARCH_MODEL", "").strip() or None,
            DEFAULT_WEB_SEARCH_MODEL,
        ]
    )
)


def search_book_price(
    title: str,
    *,
    geography: dict,
    isbn: str | None = None,
    kind: str = "book",
    description: str | None = None,
) -> dict | None:
    rows = search_price_batch(
        [
            {
                "title": title,
                "isbn": isbn,
                "kind": kind,
                "description": description,
            }
        ],
        geography=geography,
    )
    return rows[0] if rows else None


def search_price_batch(items: list[dict], *, geography: dict) -> list[dict | None]:
    pending = [
        item for item in items if str(item.get("title") or item.get("description") or "").strip()
    ]
    if not pending:
        return [None] * len(items)
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        pricing_log.warning("web_search skipped", reason="missing_openai_key")
        record_llm_call(
            reason=(
                "Web search: find a current physical-copy purchase price for unpriced catalog items"
            ),
            operation="web_search", provider="openai", model=DEFAULT_WEB_SEARCH_MODEL,
            query={
                "items": [
                    str(item.get("title") or item.get("description") or "")
                    for item in pending
                ]
            },
            status="skipped", error="missing_openai_key",
        )
        return [None] * len(items)
    found: list[dict | None] = []
    for offset in range(0, len(pending), BATCH_SIZE):
        found.extend(
            _batch_chunk(
                pending[offset : offset + BATCH_SIZE],
                geography=geography,
                key=key,
            )
        )
    by_name = {
        str(row.get("query") or "").lower(): row for row in found if isinstance(row, dict)
    }
    mapped: list[dict | None] = []
    for item in items:
        name = str(item.get("title") or item.get("description") or "").strip()
        mapped.append(by_name.get(name.lower()))
    if len(found) == len(items):
        return found
    return mapped


def _batch_chunk(items: list[dict], *, geography: dict, key: str) -> list[dict | None]:
    country = str(geography.get("country_code") or "IN")
    city = str(geography.get("city") or "")
    region = str(geography.get("region") or "")
    currency = str(geography.get("currency") or "INR")
    prompt = _batch_prompt(
        items, country=country, city=city, region=region, currency=currency
    )
    payload = None
    model_used = DEFAULT_WEB_SEARCH_MODEL
    schema_name = BATCH_SCHEMA_NAME if len(items) > 1 else PRICE_SCHEMA_NAME
    schema = BATCH_PRICE_SCHEMA if len(items) > 1 else PRICE_SCHEMA
    for model in [item for item in WEB_SEARCH_MODELS if item]:
        payload = _responses(
            prompt,
            geography=geography,
            model=model,
            key=key,
            schema_name=schema_name,
            schema=schema,
        )
        model_used = model
        if payload is not None:
            break
    if payload is None:
        return [None] * len(items)
    parsed = _parse_output(payload)
    rows = _rows_from_parsed(parsed, items)
    results: list[dict | None] = []
    for item, row in zip(items, rows, strict=False):
        name = str(item.get("title") or item.get("description") or "").strip()
        citations = citations_from_schema(row, currency) if row else []
        listing_urls = [citation["url"] for citation in citations if citation.get("url")]
        pricing_log.info(
            "web_search ok",
            model=model_used,
            title=name[:80],
            citations=len(citations),
            priced=sum(1 for citation in citations if citation.get("parsed_amount")),
            batch=len(items),
        )
        blob = json.dumps(payload, sort_keys=True, default=str)
        results.append(
            {
                "query": name,
                "market": geography.get("market"),
                "listing_url": listing_urls[0] if listing_urls else "",
                "citations": citations,
                "listing_urls": listing_urls[:MAX_LISTING_URLS],
                "html_sha256": sha256_bytes(blob.encode()),
                "excerpt_chars": 0,
                "excerpt_truncated": False,
                "listing_count": len(listing_urls[:MAX_LISTING_URLS]),
                "parser": "openai_web_search",
                "small_model": model_used,
            }
        )
    while len(results) < len(items):
        results.append(None)
    return results[: len(items)]


def _batch_prompt(
    items: list[dict],
    *,
    country: str,
    city: str,
    region: str,
    currency: str,
) -> str:
    lines = []
    for index, item in enumerate(items, start=1):
        kind = str(item.get("kind") or "book")
        target = "physical paperback or hardcover book" if kind != "object" else "physical item"
        lines.append(
            f"{index}. kind={target}; name={str(item.get('title') or '').strip()}; "
            f"isbn={item.get('isbn') or ''}; "
            f"description={str(item.get('description') or '').strip()[:240]}"
        )
    return (
        "Search the live web for a current purchase price of each numbered item. "
        "These are distinct objects; do not reuse one price for another. "
        "Prefer Amazon, Flipkart, Bookswagon, Crossword, IKEA, Croma, or other retailers. "
        "Do not invent a price or ISBN. If you cannot find a physical offer, "
        "return amount null for that item. Exclude Kindle, eBook, and rental.\n"
        f"city={city}\nregion={region}\ncountry={country}\ncurrency={currency}\n"
        "items:\n"
        + "\n".join(lines)
        + "\nReturn JSON matching the schema only."
    )


def _rows_from_parsed(parsed: dict | None, items: list[dict]) -> list[dict]:
    if not parsed:
        return [{} for _ in items]
    if "results" in parsed and isinstance(parsed["results"], list):
        rows = parsed["results"]
        aligned: list[dict] = []
        for index, item in enumerate(items):
            name = str(item.get("title") or "").strip().lower()
            match = next(
                (
                    row
                    for row in rows
                    if isinstance(row, dict) and str(row.get("name") or "").strip().lower() == name
                ),
                None,
            )
            if match is None and index < len(rows) and isinstance(rows[index], dict):
                match = rows[index]
            aligned.append(match if isinstance(match, dict) else {})
        return aligned
    return [parsed for _ in items] if len(items) == 1 else [{} for _ in items]


def _responses(
    prompt: str,
    *,
    geography: dict,
    model: str,
    key: str,
    schema_name: str,
    schema: dict,
) -> dict | None:
    body = {
        "model": model,
        "tool_choice": "required",
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
        "include": ["web_search_call.action.sources"],
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    location = body["tools"][0]["user_location"]
    body["tools"][0]["user_location"] = {
        key_name: value for key_name, value in location.items() if value
    }
    reserve_budget("0.25")
    started = time.monotonic()
    reason = (
        "Web search: find a current physical-copy purchase price for unpriced catalog items"
    )
    query = {
        "prompt": prompt,
        "schema_name": schema_name,
        "country": str(geography.get("country_code") or "IN"),
        "city": str(geography.get("city") or ""),
        "region": str(geography.get("region") or ""),
    }
    try:
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=90) as response:
            payload = json.load(response)
        latency_ms = round((time.monotonic() - started) * 1000)
        record_usage(
            provider="openai", model=model, operation="web_search",
            response=payload, latency_ms=latency_ms,
        )
        pricing_log.info("web_search http_ok", model=model, schema=schema_name)
        record_llm_call(
            reason=reason, operation="web_search", provider="openai", model=model,
            query=query, response=payload, latency_ms=latency_ms,
        )
        return payload
    except HTTPError as error:
        detail = error.read()[:300].decode("utf-8", errors="replace")
        pricing_log.warning(
            "web_search http_error",
            model=model,
            status=error.code,
            detail=detail.replace("\n", " "),
        )
        record_llm_call(
            reason=reason, operation="web_search", provider="openai", model=model,
            query=query, status="error",
            error=f"http_{error.code}: {detail.replace(chr(10), ' ')}",
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        pricing_log.warning(
            "web_search failed",
            model=model,
            error=error.__class__.__name__,
        )
        record_llm_call(
            reason=reason, operation="web_search", provider="openai", model=model,
            query=query, status="error", error=error.__class__.__name__,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return None


def _parse_output(payload: dict) -> dict | None:
    text = str(payload.get("output_text") or "").strip()
    if not text:
        chunks: list[str] = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    chunks.append(str(part.get("text") or ""))
        text = "\n".join(chunks).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        parsed = json.loads(text[start : end + 1])
    return parsed if isinstance(parsed, dict) else None


def citations_from_schema(payload: dict, currency: str) -> list[dict]:
    rows = payload.get("citations") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    citations = []
    for item in rows[:MAX_LISTING_URLS]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        snippet = str(item.get("snippet") or "").strip()
        offer_type = item.get("offer_type") if item.get("offer_type") in OFFER_TYPES else "unknown"
        parsed_amount = _coerce_amount(item.get("amount"), snippet, currency)
        parsed_currency = item.get("currency") or currency
        if isinstance(parsed_currency, str):
            parsed_currency = parsed_currency.upper()
            if len(parsed_currency) != 3:
                parsed_currency = currency
        text = f"{title} {snippet}".lower()
        if (
            offer_type == "unknown"
            and parsed_amount
            and is_shop_url(url)
            and not any(term in text for term in ("kindle", "ebook", "e-book"))
        ):
            offer_type = "physical"
        citations.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "offer_type": offer_type,
                "parsed_amount": parsed_amount,
                "parsed_currency": parsed_currency if parsed_amount else None,
                "format": item.get("format"),
                "condition": item.get("condition"),
                "comparable": offer_type == "physical" and parsed_amount is not None,
            }
        )
    return citations


def _coerce_amount(value: object, snippet: str, currency: str) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value > 0:
        return str(value)
    blob = " ".join(
        part for part in (str(value).strip() if value not in (None, "") else "", snippet) if part
    )
    prices = parse_prices(blob, default_currency=currency)
    if prices:
        return str(prices[0][0])
    return None
