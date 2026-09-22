"""Provider calls for sealed, bounded model assessments."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app.providers.llm_trace import record_llm_call
from backend.app.providers.pricing import log as pricing_log
from backend.app.providers.usage import record_usage, reserve_budget

SYSTEM = (
    "Assess only the supplied physical asset evidence. Return JSON with keys "
    "category, condition, damage, identity_candidates, recommended_action, confidence, "
    "rationale, evidence_refs. damage must be an object "
    '{"present": false, "types": [], "description": null}, never an array. '
    "If target_identity.title is set, that named copy is the only asset to assess. "
    "The photo may show several books or objects; ignore the others. "
    "Put that title in identity_candidates with the supplied evidence_refs. "
    "Judge condition and damage for that copy if it is visible. "
    "Use only evidence_refs supplied in the input. "
    "Do not invent ISBNs, prices, geometry, or physical-copy merges. "
    "If that specific copy is unreadable, request recapture or human_review."
)

ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "category",
        "condition",
        "damage",
        "identity_candidates",
        "recommended_action",
        "confidence",
        "rationale",
        "evidence_refs",
    ],
    "properties": {
        "category": {
            "type": "string",
            "enum": [
                "book", "portrait", "painting", "cup", "furniture",
                "electronics", "other", "unknown",
            ],
        },
        "condition": {
            "type": "string",
            "enum": ["new", "good", "worn", "damaged", "unknown"],
        },
        "damage": {
            "type": "object",
            "additionalProperties": False,
            "required": ["present", "types", "description"],
            "properties": {
                "present": {"type": ["boolean", "null"]},
                "types": {"type": "array", "items": {"type": "string"}},
                "description": {"type": ["string", "null"]},
            },
        },
        "identity_candidates": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "label",
                    "identifier_type",
                    "identifier_value",
                    "confidence",
                    "evidence_refs",
                ],
                "properties": {
                    "label": {"type": "string"},
                    "identifier_type": {"type": ["string", "null"]},
                    "identifier_value": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "recommended_action": {
            "type": "string",
            "enum": [
                "accept_candidate",
                "recapture",
                "alternate_resolver",
                "human_review",
            ],
        },
        "confidence": {"type": "number"},
        "rationale": {"type": ["string", "null"]},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
}


def _text_evidence(package: dict) -> str:
    bounded = {
        **package,
        "media": [
            {"evidence_ref": row["evidence_ref"], "media_type": row["media_type"]}
            for row in package.get("media", [])
        ],
    }
    return (
        "Reply with JSON matching the assessment schema.\n"
        + json.dumps(bounded, sort_keys=True)
    )


def configured_fable_model() -> str:
    return os.getenv("FABLE_MODEL", "claude-fable-5-1").strip() or "claude-fable-5-1"


def configured_astra_model() -> str:
    return os.getenv("ASTRA_MODEL", "gpt-6-astra").strip() or "gpt-6-astra"


def configured_jev_model() -> str:
    return os.getenv("JEV_MODEL", "jev-latest").strip() or "jev-latest"


LIVE_SYSTEM = (
    "You are Astra-live capture assist for a library shelf or Pass C still. "
    "Return JSON with keys quality, provisional_count, unreadable_slots, recapture_hint, "
    "confidence, rationale. quality is an object "
    '{"blur": false, "glare": false, "readable": true, "notes": null}. '
    "provisional_count is visible copies in this frame only. "
    "unreadable_slots is an array of labels such as row_01/slot_2. "
    "This is assist metadata only. It is not Pipeline B and not inventory truth. "
    "Do not invent ISBNs, prices, geometry, merges, or money. "
    "Do not output count or price as inventory fields."
)

LIVE_ASSIST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "quality",
        "provisional_count",
        "unreadable_slots",
        "recapture_hint",
        "confidence",
        "rationale",
    ],
    "properties": {
        "quality": {
            "type": "object",
            "additionalProperties": False,
            "required": ["blur", "glare", "readable", "notes"],
            "properties": {
                "blur": {"type": ["boolean", "null"]},
                "glare": {"type": ["boolean", "null"]},
                "readable": {"type": ["boolean", "null"]},
                "notes": {"type": ["string", "null"]},
            },
        },
        "provisional_count": {"type": ["integer", "null"]},
        "unreadable_slots": {"type": "array", "items": {"type": "string"}},
        "recapture_hint": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "rationale": {"type": ["string", "null"]},
    },
}


def call_fable(evidence_bytes: bytes, *, model: str | None = None) -> tuple[dict, str]:
    model = model or configured_fable_model()
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
    query = {
        "system": SYSTEM,
        "evidence": _text_evidence(package),
        "media": [
            {
                "evidence_ref": row.get("evidence_ref"),
                "media_type": row.get("media_type"),
            }
            for row in package.get("media", [])
        ],
    }
    reason = (
        "Pipeline A: Anthropic Fable assessment of a sealed physical-copy evidence package"
    )
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
    try:
        raw = _http_json(request, model=model, pipeline="fable")
    except Exception as error:
        record_llm_call(
            reason=reason, operation="fable_assessment", provider="anthropic",
            model=model, query=query, status="error", error=str(error),
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    latency_ms = round((time.monotonic() - started) * 1000)
    record_usage(
        provider="anthropic", model=model, operation="fable_assessment", response=raw,
        latency_ms=latency_ms,
    )
    record_llm_call(
        reason=reason, operation="fable_assessment", provider="anthropic",
        model=model, query=query, response=raw, latency_ms=latency_ms,
    )
    blocks = raw.get("content") or []
    content = "".join(item.get("text", "") for item in blocks if item.get("type") == "text")
    return raw, content


def call_astra(evidence_bytes: bytes, *, model: str | None = None) -> tuple[dict, str]:
    model = model or configured_astra_model()
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
        "text": {
            "format": {
                "type": "json_schema",
                "name": "model_assessment",
                "strict": True,
                "schema": ASSESSMENT_SCHEMA,
            }
        },
    }
    query = {
        "instructions": SYSTEM,
        "evidence": _text_evidence(package),
        "media": [
            {
                "evidence_ref": row.get("evidence_ref"),
                "media_type": row.get("media_type"),
            }
            for row in package.get("media", [])
        ],
    }
    reason = (
        "Pipeline B: independent OpenAI Astra replay of the same sealed evidence"
    )
    reserve_budget("1.00")
    started = time.monotonic()
    request = Request(
        "https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        raw = _http_json(request, model=model, pipeline="astra_replay")
    except Exception as error:
        record_llm_call(
            reason=reason, operation="astra_replay", provider="openai",
            model=model, query=query, status="error", error=str(error),
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    latency_ms = round((time.monotonic() - started) * 1000)
    record_usage(
        provider="openai", model=model, operation="astra_replay", response=raw,
        latency_ms=latency_ms,
    )
    record_llm_call(
        reason=reason, operation="astra_replay", provider="openai",
        model=model, query=query, response=raw, latency_ms=latency_ms,
    )
    content = raw.get("output_text") or "".join(
        part.get("text", "")
        for item in raw.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") in {"output_text", "text"}
    )
    return raw, content


def call_astra_live(evidence_bytes: bytes, *, model: str | None = None) -> tuple[dict, str]:
    model = model or configured_astra_model()
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for Astra-live")
    package = json.loads(evidence_bytes)
    content = [{"type": "input_text", "text": _live_text(package)}]
    for media in package.get("media", []):
        content.append({
            "type": "input_image",
            "image_url": f"data:{media['media_type']};base64,{media['base64']}",
        })
    body = {
        "model": model,
        "instructions": LIVE_SYSTEM,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "astra_live_assist",
                "strict": True,
                "schema": LIVE_ASSIST_SCHEMA,
            }
        },
        "max_output_tokens": 500,
    }
    query = {
        "instructions": LIVE_SYSTEM,
        "frame": _live_text(package),
        "media": [
            {
                "evidence_ref": row.get("evidence_ref"),
                "media_type": row.get("media_type"),
            }
            for row in package.get("media", [])
        ],
    }
    reason = (
        "Astra-live capture assist: quality, provisional count, and unreadable slots "
        "for this shelf or Pass C frame (not inventory truth)"
    )
    reserve_budget("0.40")
    started = time.monotonic()
    request = Request(
        "https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        raw = _http_json(request, model=model, pipeline="astra_live")
    except Exception as error:
        record_llm_call(
            reason=reason, operation="astra_live", provider="openai",
            model=model, query=query, status="error", error=str(error),
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    latency_ms = round((time.monotonic() - started) * 1000)
    record_usage(
        provider="openai", model=model, operation="astra_live", response=raw,
        latency_ms=latency_ms,
    )
    record_llm_call(
        reason=reason, operation="astra_live", provider="openai",
        model=model, query=query, response=raw, latency_ms=latency_ms,
    )
    content = raw.get("output_text") or "".join(
        part.get("text", "")
        for item in raw.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") in {"output_text", "text"}
    )
    return raw, content


def _live_text(package: dict) -> str:
    bounded = {
        key: value for key, value in package.items()
        if key != "media"
    }
    bounded["media"] = [
        {"evidence_ref": row["evidence_ref"], "media_type": row["media_type"]}
        for row in package.get("media", [])
    ]
    return (
        "Reply with JSON matching the Astra-live assist schema. "
        "Assist only; not inventory.\n"
        + json.dumps(bounded, sort_keys=True)
    )


def _http_json(request: Request, *, model: str, pipeline: str) -> dict:
    try:
        with urlopen(request, timeout=90) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read()[:400].decode("utf-8", errors="replace").replace("\n", " ")
        pricing_log.warning(
            "model_http_error",
            pipeline=pipeline,
            model=model,
            status=error.code,
            detail=detail,
        )
        raise


def extract_json_object(text: str) -> dict:
    blob = (text or "").strip()
    if blob.startswith("```"):
        blob = blob.strip("`")
        if blob.lower().startswith("json"):
            blob = blob[4:].strip()
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        raise json.JSONDecodeError("assessment JSON object missing", blob, 0)
    parsed = json.loads(blob[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("provider assessment must be an object")
    return parsed
