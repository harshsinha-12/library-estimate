"""Astra-live capture assist. Not Pipeline B and not inventory truth."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

from backend.app.domain.repository import SurveyRepository
from backend.app.providers.models.astra import parse_astra_live_response
from backend.app.providers.models.remote import call_astra_live, extract_json_object
from backend.app.providers.usage import BudgetExceededError
from backend.app.utils.clocks import utc_now
from backend.app.utils.json_codec import canonical_json_bytes

ASTRA_LIVE_MIN_INTERVAL_S = 2
ASTRA_LIVE_MAX_BYTES = 400_000
ASSIST_LIST_KEY = "astra_live"


def _list_key(repository: SurveyRepository, survey_id: UUID) -> str:
    return f"{repository.key_prefix}:survey:{survey_id}:{ASSIST_LIST_KEY}"


def list_astra_live(repository: SurveyRepository, survey_id: UUID) -> dict:
    repository.get(survey_id)
    rows = [
        json.loads(raw) for raw in repository.redis.lrange(_list_key(repository, survey_id), 0, -1)
    ]
    return {
        "survey_id": str(survey_id),
        "assists": rows,
        "authority": "assist_metadata",
        "pipeline": "astra_live",
        "note": "Astra-live is capture UX only. It is not Pipeline B and not inventory.",
    }


def record_astra_live(
    repository: SurveyRepository,
    survey_id: UUID,
    payload: dict,
    *,
    astra_call: Callable = call_astra_live,
) -> dict:
    repository.get(survey_id)
    inventory_before = deepcopy(repository.get_json(survey_id, "inventory"))
    existing = list_astra_live(repository, survey_id)["assists"]
    if _too_soon(existing):
        return _skip(survey_id, "sample_interval")
    image_b64 = str(payload.get("image_base64") or "").strip()
    if not image_b64:
        return _skip(survey_id, "no_frame", review="human")
    try:
        jpeg = base64.b64decode(image_b64, validate=False)
    except (ValueError, TypeError):
        return _skip(survey_id, "no_frame", review="human")
    if len(jpeg) > ASTRA_LIVE_MAX_BYTES:
        return _skip(survey_id, "frame_too_large")
    capture_pass = payload.get("capture_pass") or "B"
    if capture_pass not in {"B", "C"}:
        capture_pass = "B"
    package = {
        "schema_version": "1.0.0",
        "pipeline": "astra_live",
        "authority": "assist_metadata",
        "capture_pass": capture_pass,
        "quality_messages": list(payload.get("quality_messages") or [])[:12],
        "on_device_provisional_count": payload.get("provisional_count"),
        "unreadable_slots": list(payload.get("unreadable_slots") or [])[:20],
        "recapture_rows": list(payload.get("recapture_rows") or [])[:12],
        "task": (
            "Assist only: quality, provisional count in this frame, unreadable slots. "
            "Do not write inventory, ISBN, geometry, merge, or money."
        ),
        "media": [{
            "evidence_ref": f"live/{capture_pass}/frame.jpg",
            "media_type": "image/jpeg",
            "base64": base64.b64encode(jpeg).decode("ascii"),
        }],
    }
    assist_id = uuid4()
    try:
        raw, content = astra_call(canonical_json_bytes(package))
        parsed = extract_json_object(content)
        assist = parse_astra_live_response({
            **parsed,
            "schema_version": "1.0.0",
            "assist_id": str(assist_id),
            "survey_id": str(survey_id),
            "pipeline": "astra_live",
            "provider": "openai",
            "model": str(raw.get("model") or "gpt-6-astra"),
            "authority": "assist_metadata",
            "capture_pass": capture_pass,
        })
    except BudgetExceededError:
        return _skip(survey_id, "budget_cap", review="human")
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        return _skip(
            survey_id, "provider_unavailable", review="human",
            detail=error.__class__.__name__,
        )
    record = {
        "status": "assist",
        "pipeline": "astra_live",
        "authority": "assist_metadata",
        "invented": False,
        "assist": assist.model_dump(mode="json"),
        "created_at": utc_now().isoformat(),
    }
    repository.put_bytes(
        survey_id,
        f"derived/astra-live/{assist_id}-raw.json",
        canonical_json_bytes(raw),
        "application/json",
    )
    repository.put_bytes(
        survey_id,
        f"derived/astra-live/{assist_id}.json",
        canonical_json_bytes(record),
        "application/json",
    )
    repository.redis.rpush(_list_key(repository, survey_id), json.dumps(record, sort_keys=True))
    inventory_after = repository.get_json(survey_id, "inventory")
    if inventory_after != inventory_before and inventory_before is not None:
        repository.save_json(survey_id, "inventory", inventory_before)
        return _skip(survey_id, "refused_inventory_write", review="human")
    return record


def _too_soon(rows: list[dict]) -> bool:
    if not rows:
        return False
    stamp = rows[-1].get("created_at")
    if not stamp:
        return False
    try:
        previous = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (utc_now() - previous).total_seconds() < ASTRA_LIVE_MIN_INTERVAL_S


def _skip(
    survey_id: UUID, reason: str, *, review: str | None = None, detail: str | None = None,
) -> dict:
    payload = {
        "status": "skipped",
        "reason": reason,
        "pipeline": "astra_live",
        "authority": "assist_metadata",
        "assessment": None,
        "assist": None,
        "invented": False,
        "survey_id": str(survey_id),
        "review": review,
        "created_at": utc_now().isoformat(),
    }
    if detail:
        payload["detail"] = detail
    if reason in {"provider_unavailable", "budget_cap"}:
        payload["review"] = "human"
    return payload
