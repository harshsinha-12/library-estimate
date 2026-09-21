"""Read book names from a shelf or cover JPEG for price search."""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.request import Request, urlopen

from backend.app.providers.pricing.queries import titles_from_ocr
from backend.app.providers.usage import record_usage, reserve_budget

DEFAULT_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def extract_book_titles(jpeg: bytes, *, model: str = DEFAULT_VISION_MODEL) -> list[str]:
    if not jpeg:
        return []
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return []
    payload = _complete_vision(jpeg, model=model, key=key)
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
) -> dict | None:
    encoded = base64.b64encode(jpeg[:400_000]).decode("ascii")
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    system
                    or (
                        "Extract readable physical book titles from the photo. "
                        "Do not invent a title or ISBN. Ignore notebooks, papers, and devices. "
                        'Return JSON {"books": [{"title": "...", "author": null}]}.'
                    )
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user or "List each distinct book cover or spine you can read.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            },
        ],
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
        record_usage(
            provider="openai", model=model, operation="vision_titles",
            response=payload, latency_ms=round((time.monotonic() - started) * 1000),
        )
        return json.loads(payload["choices"][0]["message"]["content"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def titles_from_frame_text(text: str | None) -> list[str]:
    return titles_from_ocr(text)
