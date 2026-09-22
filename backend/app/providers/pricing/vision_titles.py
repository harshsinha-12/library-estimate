"""Read book names from a shelf or cover JPEG for price search."""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.request import Request, urlopen

from backend.app.providers.llm_trace import record_llm_call
from backend.app.providers.pricing.queries import titles_from_ocr
from backend.app.providers.usage import record_usage, reserve_budget

DEFAULT_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def extract_book_titles(jpeg: bytes, *, model: str = DEFAULT_VISION_MODEL) -> list[str]:
    if not jpeg:
        return []
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        record_llm_call(
            reason="Vision: read book titles from a shelf or cover JPEG so price search has a name",
            operation="vision_titles", provider="openai", model=model,
            query={"image_jpeg_bytes": len(jpeg)},
            status="skipped", error="missing_openai_key",
        )
        return []
    payload = _complete_vision(
        jpeg,
        model=model,
        key=key,
        reason="Vision: read book titles from a shelf or cover JPEG so price search has a name",
    )
    if not isinstance(payload, dict):
        return []
    titles: list[str] = []
    rows = payload.get("books")
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if len(title) >= 8 and title.lower() not in {value.lower() for value in titles}:
                titles.append(title[:160])
    return titles[:8]


def describe_object(
    jpeg: bytes,
    *,
    spoken: str | None = None,
    category: str | None = None,
    model: str = DEFAULT_VISION_MODEL,
) -> dict:
    if not jpeg:
        return {}
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        record_llm_call(
            reason="Vision: name a non-book physical object for a local replacement-cost search",
            operation="vision_object", provider="openai", model=model,
            query={"spoken": spoken, "category": category, "image_jpeg_bytes": len(jpeg)},
            status="skipped", error="missing_openai_key",
        )
        return {}
    payload = _complete_vision(
        jpeg,
        model=model,
        key=key,
        system=(
            "Identify the physical object in the photo for a local replacement-cost search. "
            "Do not invent a brand if you cannot read one. "
            'Return JSON {"label": "...", "details": "..."}.'
        ),
        user=(
            f"Category hint: {category or 'unknown'}. "
            f"Spoken note: {spoken or 'none'}. "
            "Name the object a shopper would search for."
        ),
        reason=(
            "Vision: name a non-book physical object for a local replacement-cost search"
        ),
        operation="vision_object",
    )
    if not isinstance(payload, dict):
        return {}
    label = str(payload.get("label") or "").strip()
    details = str(payload.get("details") or "").strip()
    return {"label": label[:160], "details": details[:240]}


def _complete_vision(
    jpeg: bytes,
    *,
    model: str,
    key: str,
    system: str | None = None,
    user: str | None = None,
    reason: str = "Vision: extract readable text from a capture JPEG",
    operation: str = "vision_titles",
) -> dict | None:
    system_text = system or (
        "Extract readable physical book titles from the photo. "
        "Do not invent a title or ISBN. Ignore notebooks, papers, and devices. "
        'Return JSON {"books": [{"title": "...", "author": null}]}.'
    )
    user_text = user or "List each distinct book cover or spine you can read."
    encoded = base64.b64encode(jpeg[:400_000]).decode("ascii")
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_text},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            },
        ],
    }
    query = {
        "system": system_text,
        "user": user_text,
        "image_jpeg_bytes": min(len(jpeg), 400_000),
    }
    reserve_budget("0.10")
    started = time.monotonic()
    try:
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=45) as response:
            payload = json.load(response)
        latency_ms = round((time.monotonic() - started) * 1000)
        record_usage(
            provider="openai", model=model, operation=operation,
            response=payload, latency_ms=latency_ms,
        )
        parsed = json.loads(payload["choices"][0]["message"]["content"])
        record_llm_call(
            reason=reason, operation=operation, provider="openai", model=model,
            query=query, response=payload, latency_ms=latency_ms,
        )
        return parsed
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        record_llm_call(
            reason=reason, operation=operation, provider="openai", model=model,
            query=query, status="error", error=error.__class__.__name__,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return None


def titles_from_frame_text(text: str | None) -> list[str]:
    return titles_from_ocr(text)
