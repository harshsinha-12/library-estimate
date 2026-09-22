"""Local JSON log of every LLM call: why it ran, the query, and the response.

Writes to logs/llm_calls.json in this repo. Never stores API keys or image bytes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.config.settings import REPO_ROOT
from backend.app.providers.usage import current_usage_ids
from backend.app.utils.clocks import utc_now

LOGGER = logging.getLogger("library_survey.llm")
DEFAULT_PATH = REPO_ROOT / "logs" / "llm_calls.json"
MAX_STRING = 32_000
_SECRET_KEYS = re.compile(
    r"(api[_-]?key|authorization|token|secret|password|x-api-key)$", re.I
)
_BASE64_CHARS = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_file_lock = threading.Lock()


def configure() -> None:
    if LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = True


def tracing_enabled() -> bool:
    flag = os.getenv("LLM_TRACE_DISABLED", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return False
    if os.getenv("LLM_TRACE_PATH", "").strip():
        return True
    return not os.getenv("PYTEST_CURRENT_TEST")


def trace_path() -> Path:
    override = os.getenv("LLM_TRACE_PATH", "").strip()
    return Path(override) if override else DEFAULT_PATH


def record_llm_call(
    *,
    reason: str,
    operation: str,
    provider: str,
    model: str,
    query: Any = None,
    response: Any = None,
    status: str = "ok",
    error: str | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any] | None:
    """Log why an LLM call ran and append query + response to logs/llm_calls.json."""
    configure()
    survey_id, run_id = current_usage_ids()
    record = {
        "id": str(uuid4()),
        "recorded_at": utc_now().isoformat(),
        "reason": reason,
        "operation": operation,
        "provider": provider,
        "model": model,
        "status": status,
        "survey_id": survey_id,
        "run_id": run_id,
        "query": sanitize(query),
        "response_text": _response_text(response),
        "response": sanitize(response),
        "error": error[:800] if error else None,
        "latency_ms": latency_ms,
    }
    extra = " ".join(
        f"{key}={value}"
        for key, value in (
            ("operation", operation),
            ("provider", provider),
            ("model", model),
            ("status", status),
            ("latency_ms", latency_ms),
            ("survey_id", record["survey_id"]),
        )
        if value is not None
    )
    LOGGER.info("LLM call: %s | %s", reason, extra)
    if not tracing_enabled():
        return record
    try:
        _append_record(trace_path(), record)
    except OSError:
        LOGGER.warning("llm_trace_write_failed path=%s", trace_path())
    return record


def sanitize(value: Any, *, _key: str = "") -> Any:
    """Drop secrets and bulky image payloads so the JSON file stays readable."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, bytes):
        return {"omitted_bytes": len(value)}
    if isinstance(value, str):
        return _sanitize_string(value, key=_key)
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEYS.search(name):
                cleaned[name] = "[redacted]"
            else:
                cleaned[name] = sanitize(item, _key=name)
        return cleaned
    if isinstance(value, list | tuple):
        return [sanitize(item, _key=_key) for item in value]
    return str(value)[:MAX_STRING]


def _response_text(response: Any) -> str | None:
    if response is None:
        return None
    if isinstance(response, str):
        return response[:MAX_STRING]
    if isinstance(response, bytes):
        return f"[omitted bytes {len(response)}]"
    if not isinstance(response, dict):
        return None
    text = response.get("output_text")
    if isinstance(text, str) and text.strip():
        return text[:MAX_STRING]
    chunks: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                chunks.append(str(part.get("text") or ""))
    if chunks:
        return "\n".join(chunks)[:MAX_STRING]
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        content = (first.get("message") or {}).get("content")
        if isinstance(content, str):
            return content[:MAX_STRING]
    blocks = response.get("content")
    if isinstance(blocks, list):
        joined = "".join(
            item.get("text", "")
            for item in blocks
            if isinstance(item, dict) and item.get("type") == "text"
        )
        if joined:
            return joined[:MAX_STRING]
    if "answers" in response:
        return json.dumps(response.get("answers"), default=str)[:MAX_STRING]
    if isinstance(response.get("text"), str):
        return response["text"][:MAX_STRING]
    return None


def _sanitize_string(value: str, *, key: str) -> str:
    if value.startswith("data:image"):
        return f"[omitted data URI {len(value)} chars]"
    if key.lower() in {"data", "base64", "image", "b64_json"} and len(value) > 80:
        return f"[omitted base64 {len(value)} chars]"
    compact = value.replace("\n", "").replace(" ", "")
    if len(compact) > 400 and _BASE64_CHARS.fullmatch(compact):
        return f"[omitted base64 {len(value)} chars]"
    if len(value) > MAX_STRING:
        return value[:MAX_STRING] + f"... [truncated {len(value) - MAX_STRING} chars]"
    return value


def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, ensure_ascii=False, default=str)
    with _file_lock:
        existing: list[Any] = []
        if path.exists() and path.stat().st_size:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = loaded
                elif isinstance(loaded, dict):
                    existing = [loaded]
            except json.JSONDecodeError:
                existing = []
        existing.append(json.loads(payload))
        serialized = json.dumps(existing, indent=2, ensure_ascii=False, default=str) + "\n"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(path)
