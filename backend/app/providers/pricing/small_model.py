"""Small-model JSON completion for pricing. Secrets stay in the process environment."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app.providers.pricing import log as pricing_log
from backend.app.providers.usage import record_usage, reserve_budget


def completion_body(
    model: str,
    *,
    messages: list[dict],
    response_format: dict,
    temperature: float = 0,
) -> dict:
    """Luna/GPT-5 reject temperature=0; omit the field and use the model default."""
    body: dict = {
        "model": model,
        "messages": messages,
        "response_format": response_format,
    }
    name = model.lower()
    if "luna" not in name and not name.startswith("gpt-5"):
        body["temperature"] = temperature
    return body


def complete_json(
    prompt: str,
    *,
    schema: dict,
    schema_name: str,
    model: str,
) -> dict | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        pricing_log.warning("small_model skipped", model=model, reason="missing_openai_key")
        return None
    formats = (
        {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
        {"type": "json_object"},
    )
    last_error = None
    for response_format in formats:
        format_name = response_format.get("type")
        reserve_budget("0.10")
        started = time.monotonic()
        try:
            request = Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(
                    completion_body(
                        model,
                        response_format=response_format,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Return JSON that matches the schema. Do not invent an ISBN. "
                                    "Do not treat an unconfirmed snippet as a final physical-copy "
                                    "price. Never include secrets."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                    )
                ).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            record_usage(
                provider="openai", model=model, operation=f"small_model:{schema_name}",
                response=payload, latency_ms=round((time.monotonic() - started) * 1000),
            )
            parsed = json.loads(payload["choices"][0]["message"]["content"])
            usage = payload.get("usage") or {}
            pricing_log.info(
                "small_model ok",
                model=model,
                schema=schema_name,
                format=format_name,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
            return parsed
        except HTTPError as error:
            detail = error.read()[:240].decode("utf-8", errors="replace")
            last_error = f"http_{error.code}"
            pricing_log.warning(
                "small_model http_error",
                model=model,
                format=format_name,
                status=error.code,
                detail=detail.replace("\n", " "),
            )
            continue
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = error.__class__.__name__
            pricing_log.warning(
                "small_model failed",
                model=model,
                format=format_name,
                error=last_error,
            )
            continue
    pricing_log.warning("small_model gave_up", model=model, error=last_error)
    return None
