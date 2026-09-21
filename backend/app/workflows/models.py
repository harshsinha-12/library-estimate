"""Sealed evidence replay and guarded A/B routing."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from decimal import Decimal
from uuid import UUID, uuid4

from backend.app.domain.repository import SurveyRepository
from backend.app.providers.models.astra import parse_astra_response
from backend.app.providers.models.contracts import ModelAssessment
from backend.app.providers.models.fable import parse_fable_response
from backend.app.providers.models.jev import parse_jev_response, propose_route
from backend.app.providers.models.remote import call_astra, call_fable
from backend.app.providers.usage import usage_for_run
from backend.app.utils.clocks import utc_now
from backend.app.utils.hashing import sha256_bytes
from backend.app.utils.json_codec import canonical_json_bytes


class ModelReplayError(ValueError):
    pass


def _assess(
    repository: SurveyRepository, survey_id: UUID, run_id: UUID,
    pipeline: str, evidence: bytes, call: Callable,
) -> dict:
    raw, content = call(evidence)
    raw_path = f"derived/model-runs/{run_id}/{pipeline}-raw.json"
    repository.put_bytes(survey_id, raw_path, canonical_json_bytes(raw), "application/json")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ModelReplayError("provider assessment must be an object")
    package = json.loads(evidence)
    allowed_refs = set(package["evidence_refs"])
    supplied = set(parsed.get("evidence_refs") or [])
    if not supplied or not supplied <= allowed_refs:
        raise ModelReplayError("assessment cites missing evidence")
    for candidate in parsed.get("identity_candidates") or []:
        if not set(candidate.get("evidence_refs") or []) <= allowed_refs:
            raise ModelReplayError("identity candidate cites missing evidence")
    model = str(raw.get("model") or "")
    payload = {
        **parsed,
        "schema_version": "1.0.0", "assessment_id": str(uuid4()),
        "survey_id": str(survey_id), "asset_copy_id": package["asset_copy_id"],
        "pipeline": pipeline, "provider": "anthropic" if pipeline == "fable" else "openai",
        "model": model, "evidence_package_hash": sha256_bytes(evidence),
    }
    assessment: ModelAssessment = (
        parse_fable_response(payload) if pipeline == "fable" else parse_astra_response(payload)
    )
    normalized = assessment.model_dump(mode="json")
    repository.put_bytes(
        survey_id, f"derived/model-runs/{run_id}/{pipeline}-assessment.json",
        canonical_json_bytes(normalized), "application/json",
    )
    return normalized


def _route(a: dict | None, b: dict | None, asset: dict, jev: dict | None) -> dict:
    if a is None or b is None:
        return {"action": "human_review", "reason": "model_unavailable", "disagreement": None}
    disagreement = any(a[field] != b[field] for field in ("category", "condition", "damage"))
    if asset.get("requires_appraisal"):
        return {"action": "human_review", "reason": "appraisal_veto", "disagreement": disagreement}
    if a["category"] != asset["category"] or b["category"] != asset["category"]:
        return {
            "action": "human_review", "reason": "deterministic_category_veto",
            "disagreement": disagreement,
        }
    if disagreement:
        return {"action": "human_review", "reason": "model_disagreement", "disagreement": True}
    if min(a["confidence"], b["confidence"]) < 0.85:
        return {"action": "human_review", "reason": "low_confidence", "disagreement": False}
    if jev is None:
        return {"action": "human_review", "reason": "jev_unavailable", "disagreement": False}
    if jev["confidence"] < 0.8:
        return {"action": "human_review", "reason": "jev_low_confidence", "disagreement": False}
    proposed = jev["choice"]
    if a["recommended_action"] == b["recommended_action"] == "accept_candidate":
        if proposed == "accept_candidate":
            return {"action": proposed, "reason": "agreement", "disagreement": False}
        return {"action": proposed, "reason": "jev_proposal", "disagreement": False}
    action = (
        a["recommended_action"]
        if a["recommended_action"] == b["recommended_action"]
        else "human_review"
    )
    if action == "human_review" or proposed == "accept_candidate":
        return {"action": "human_review", "reason": "action_conflict", "disagreement": False}
    return {"action": proposed, "reason": "jev_proposal", "disagreement": False}


def replay_asset(
    repository: SurveyRepository, survey_id: UUID, asset_copy_id: str,
    *, run_id: UUID | None = None, fable_call: Callable = call_fable,
    astra_call: Callable = call_astra, jev_call: Callable = propose_route,
) -> dict:
    survey = repository.get(survey_id)
    if not survey.package_hash or survey.status not in {"geometry", "partial"}:
        raise ModelReplayError("survey must be sealed before model replay")
    inventory = repository.get_json(survey_id, "inventory") or {}
    asset = next(
        (row for row in inventory.get("asset_copies", []) if row["asset_copy_id"] == asset_copy_id),
        None,
    )
    if asset is None:
        raise ModelReplayError("asset copy not found")
    run_id = run_id or uuid4()
    started = time.monotonic()
    observations = [
        row for row in inventory.get("observations", [])
        if row.get("observation_id") in asset.get("observation_refs", [])
    ]
    refs = sorted({row["evidence_ref"] for row in observations if row.get("evidence_ref")})
    if not refs:
        raise ModelReplayError("asset has no evidence references")
    package = {
        "schema_version": "1.0.0", "survey_id": str(survey_id),
        "sealed_package_hash": survey.package_hash, "asset_copy_id": asset_copy_id,
        "asset": asset, "observations": observations, "evidence_refs": refs,
        "task": "assess category, condition, damage and identity candidates",
    }
    media = []
    for ref in refs:
        if len(media) >= 2 or not ref.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        if not repository.exists_bytes(survey_id, ref):
            continue
        content = repository.get_bytes(survey_id, ref)
        if len(content) > 400_000:
            continue
        media.append({
            "evidence_ref": ref,
            "media_type": "image/png" if ref.lower().endswith(".png") else "image/jpeg",
            "base64": base64.b64encode(content).decode("ascii"),
        })
    package["media"] = media
    evidence = canonical_json_bytes(package)
    evidence_path = f"derived/model-runs/{run_id}/evidence.json"
    repository.put_bytes(survey_id, evidence_path, evidence, "application/json")
    assessments = {}
    failures = {}
    for pipeline, call in (("fable", fable_call), ("astra_replay", astra_call)):
        try:
            assessments[pipeline] = _assess(repository, survey_id, run_id, pipeline, evidence, call)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            failures[pipeline] = error.__class__.__name__
    jev = None
    if len(assessments) == 2:
        try:
            raw_jev = jev_call({
                "asset": {
                    "category": asset["category"],
                    "requires_appraisal": asset.get("requires_appraisal"),
                },
                "fable": assessments["fable"], "astra": assessments["astra_replay"],
            })
            repository.put_bytes(
                survey_id, f"derived/model-runs/{run_id}/jev-raw.json",
                canonical_json_bytes(raw_jev), "application/json",
            )
            jev = parse_jev_response(raw_jev)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            failures["jev"] = error.__class__.__name__
    decision = _route(assessments.get("fable"), assessments.get("astra_replay"), asset, jev)
    result = {
        "run_id": str(run_id), "survey_id": str(survey_id), "asset_copy_id": asset_copy_id,
        "evidence_path": evidence_path, "evidence_package_hash": sha256_bytes(evidence),
        "assessments": assessments, "jev": jev, "failures": failures, "decision": decision,
        "created_at": utc_now().isoformat(),
    }
    usage = usage_for_run(repository, survey_id, run_id)
    action = "accept" if decision["action"] == "accept_candidate" else decision["action"]
    transition = {
        "schema_version": "1.0.0", "transition_id": str(uuid4()),
        "survey_id": str(survey_id), "policy_id": "route_v0_log_only",
        "state": {
            "asset_copy_id": asset_copy_id, "category": asset["category"],
            "evidence_package_hash": result["evidence_package_hash"],
            "disagreement": decision["disagreement"],
            "appraisal_required": bool(asset.get("requires_appraisal")),
        },
        "action": action, "action_source": "policy",
        "fable": assessments.get("fable"), "astra": assessments.get("astra_replay"),
        "jev": jev, "human_truth": None, "independent_outcome": None,
        "reward": None, "next_state_id": None,
        "cost_usd": float(Decimal(usage["estimated_cost_usd"])),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    repository.redis.rpush(
        f"{repository.key_prefix}:survey:{survey_id}:rl_transitions",
        json.dumps(transition, sort_keys=True),
    )
    result["transition_id"] = transition["transition_id"]
    repository.save_json(survey_id, f"model-run:{run_id}", result)
    repository.redis.sadd(
        f"{repository.key_prefix}:survey:{survey_id}:model_runs", str(run_id)
    )
    return result
