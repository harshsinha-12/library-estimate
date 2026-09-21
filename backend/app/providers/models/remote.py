"""Provider calls for sealed, bounded model assessments."""

from __future__ import annotations

import json
import os
import time
from urllib.request import Request, urlopen

from backend.app.providers.usage import record_usage, reserve_budget

SYSTEM = (
    "Assess only the supplied physical asset evidence. Return a JSON object with keys "
    "category, condition, damage, identity_candidates, recommended_action, confidence, "
    "rationale, evidence_refs. Use only evidence_refs supplied in the input. "
    "Do not invent ISBNs, prices, geometry, or physical-copy merges. "
    "If evidence is insufficient, request recapture or human_review."
)


def _text_evidence(package: dict) -> str:
    bounded = {
        **package,
        "media": [
            {"evidence_ref": row["evidence_ref"], "media_type": row["media_type"]}
            for row in package.get("media", [])
        ],
    }
    return json.dumps(bounded, sort_keys=True)


def call_fable(evidence_bytes: bytes, *, model: str | None = None) -> tuple[dict, str]:
    model = model or os.getenv("FABLE_MODEL", "claude-fable-5-1")
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for Fable")
    package = json.loads(evidence_bytes)
    content = [{"type": "text", "text": _text_evidence(package)}]
    for media in package.get("media", []):
        content.append({
            "type": "image", "source": {
                "type": "base64", "media_type": media["media_type"], "data": media["base64"],
            },
        })
    body = {
        "model": model, "max_tokens": 1300, "system": SYSTEM,
        "messages": [{"role": "user", "content": content}],
    }
    reserve_budget("0.50")
    started = time.monotonic()
    request = Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urlopen(request, timeout=90) as response:
        raw = json.load(response)
    record_usage(
        provider="anthropic", model=model, operation="fable_assessment", response=raw,
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    blocks = raw.get("content") or []
    content = "".join(item.get("text", "") for item in blocks if item.get("type") == "text")
    return raw, content


def call_astra(evidence_bytes: bytes, *, model: str | None = None) -> tuple[dict, str]:
    model = model or os.getenv("ASTRA_MODEL", "gpt-6-astra")
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for Astra")
    package = json.loads(evidence_bytes)
    content = [{"type": "input_text", "text": _text_evidence(package)}]
    for media in package.get("media", []):
        content.append({
            "type": "input_image",
            "image_url": f"data:{media['media_type']};base64,{media['base64']}",
        })
    body = {
        "model": model,
        "instructions": SYSTEM,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_object"}},
    }
    reserve_budget("1.00")
    started = time.monotonic()
    request = Request(
        "https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=90) as response:
        raw = json.load(response)
    record_usage(
        provider="openai", model=model, operation="astra_replay", response=raw,
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    content = raw.get("output_text") or "".join(
        part.get("text", "")
        for item in raw.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") in {"output_text", "text"}
    )
    return raw, content
