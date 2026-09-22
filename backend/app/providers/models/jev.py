"""TypeSafe Jev typed route proposal; deterministic policy still owns final action."""

from __future__ import annotations

import json
import os
import time
from urllib.request import Request, urlopen

from backend.app.providers.models.remote import configured_jev_model
from backend.app.providers.usage import record_usage, reserve_budget

CRITERIA = {
    "accept_candidate": "Both assessments are supported, agree, and require no further evidence",
    "recapture": "A specific missing or unreadable image would resolve the uncertainty",
    "alternate_resolver": "Barcode, catalog, or another deterministic resolver should run next",
    "human_review": "A person must decide because evidence conflicts or risk is material",
}


def propose_route(state: dict) -> dict:
    key = os.getenv("JEV_API_KEY", "").strip()
    if not key:
        raise RuntimeError("JEV_API_KEY is required for Jev routing")
    model = configured_jev_model()
    body = {
        "model": model,
        "state": state,
        "questions": {
            "route": {
                "type": "choice",
                "instructions": "What should happen next for this physical asset assessment?",
                "criteria": CRITERIA,
            },
        },
    }
    reserve_budget("0.05")
    started = time.monotonic()
    request = Request(
        "https://api.typesafe.ai/v1/systemone", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        raw = json.load(response)
    record_usage(
        provider="typesafe", model=model, operation="jev_route", response=raw,
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    return raw


def parse_jev_response(raw: dict, *, requested_model: str = "jev-latest") -> dict:
    answer = (raw.get("answers") or {}).get("route") or {}
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    if choice not in CRITERIA or not isinstance(probabilities, dict):
        raise ValueError("Jev response lacks a valid route choice")
    values = {name: float(probabilities.get(name, 0)) for name in CRITERIA}
    invalid = any(value < 0 or value > 1 for value in values.values())
    if invalid or abs(sum(values.values()) - 1) > 0.02:
        raise ValueError("Jev response probabilities are invalid")
    # Jev may only propose a route. Count, price, geometry, and ISBN are dropped.
    return {
        "model": str(raw.get("model") or requested_model),
        "choice": choice,
        "confidence": float(answer.get("confidence", values[choice])),
        "probabilities": values,
    }
