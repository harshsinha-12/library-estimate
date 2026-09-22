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
from backend.app.providers.models.remote import call_astra, call_fable, extract_json_object
from backend.app.providers.usage import usage_for_run
from backend.app.rl.transitions import record_decision
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
    parsed = extract_json_object(content)
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
    if asset.get("requires_appraisal") or asset.get("high_value"):
        return {"action": "human_review", "reason": "high_value_veto", "disagreement": None}
    if asset.get("category") in {"ebook", "e_book", "digital_book"}:
        return {"action": "human_review", "reason": "ebook_physical_veto", "disagreement": None}
    if asset.get("merge_basis") == "isbn_only":
        return {"action": "human_review", "reason": "isbn_only_merge_veto", "disagreement": None}
    if a is None or b is None:
        return {"action": "human_review", "reason": "model_unavailable", "disagreement": None}
    disagreement = any(a[field] != b[field] for field in ("category", "condition", "damage"))
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
    if not survey.package_hash:
        raise ModelReplayError("survey must be sealed before model replay")
    if survey.status not in {"geometry", "partial", "ingest_validation"}:
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
    refs = {row["evidence_ref"] for row in observations if row.get("evidence_ref")}
    if asset.get("evidence_ref"):
        refs.add(str(asset["evidence_ref"]))
    crop = _crop_ref(repository, survey_id, asset)
    if crop:
        refs.add(crop)
    refs = sorted(refs, key=lambda path: (0 if "/crops/" in path else 1, path))
    if not refs:
        raise ModelReplayError("asset has no evidence references")
    target = _target_identity(repository, survey_id, asset)
    named = dict(asset)
    if target["title"]:
        named["title"] = target["title"]
        named["label"] = named.get("label") or target["title"]
    package = {
        "schema_version": "1.0.0", "survey_id": str(survey_id),
        "sealed_package_hash": survey.package_hash, "asset_copy_id": asset_copy_id,
        "asset": named, "observations": observations, "evidence_refs": refs,
        "target_identity": target,
        "task": target["instruction"],
    }
    media = []
    for ref in refs:
        if len(media) >= 2 or not ref.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        if not repository.exists_bytes(survey_id, ref):
            continue
        content = repository.get_bytes(survey_id, ref)
        if len(content) > 2_000_000:
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
    transition = record_decision(
        repository, survey_id, action=action, action_source="policy",
        policy_id="route_v0_log_only",
        state={
            "asset_copy_id": asset_copy_id, "category": asset["category"],
            "evidence_package_hash": result["evidence_package_hash"],
            "disagreement": decision["disagreement"],
            "appraisal_required": bool(asset.get("requires_appraisal")),
            "policy_reason": decision["reason"],
        },
        fable=assessments.get("fable"), astra=assessments.get("astra_replay"),
        jev=jev, cost_usd=float(Decimal(usage["estimated_cost_usd"])),
        elapsed_ms=round((time.monotonic() - started) * 1000),
        next_state_id=str(uuid4()) if action == "recapture" else None,
    )
    if action == "accept":
        repository.redis.rpush(
            f"{repository.key_prefix}:survey:{survey_id}:auto_accept_audit",
            json.dumps({
                "transition_id": transition["transition_id"], "asset_copy_id": asset_copy_id,
                "evidence_hash": result["evidence_package_hash"],
                "fable": assessments.get("fable"), "astra": assessments.get("astra_replay"),
                "jev": jev, "policy_reason": decision["reason"],
                "overturn_by": "human_reviewer", "created_at": transition["created_at"],
            }, sort_keys=True),
        )
    result["transition_id"] = transition["transition_id"]
    repository.save_json(survey_id, f"model-run:{run_id}", result)
    repository.redis.sadd(
        f"{repository.key_prefix}:survey:{survey_id}:model_runs", str(run_id)
    )
    return result


def _target_identity(repository: SurveyRepository, survey_id: UUID, asset: dict) -> dict:
    asset_id = asset["asset_copy_id"]
    stage3 = repository.get_json(survey_id, "stage3") or {}
    identity = next(
        (
            item
            for item in stage3.get("identities") or []
            if item.get("asset_copy_id") == asset_id
        ),
        {},
    )
    labeled = next(
        (
            item
            for item in stage3.get("assets") or []
            if item.get("asset_copy_id") == asset_id
        ),
        {},
    )
    catalog = identity.get("catalog") if isinstance(identity.get("catalog"), dict) else {}
    title = (
        str(
            identity.get("title")
            or catalog.get("title")
            or labeled.get("label")
            or asset.get("title")
            or asset.get("label")
            or ""
        ).strip()
        or None
    )
    if title is None:
        pricing = repository.get_json(survey_id, "pricing") or {}
        for item in pricing.get("live_searches") or []:
            if item.get("asset_copy_id") == asset_id:
                title = str(item.get("title") or item.get("query") or "").strip() or None
                break
    isbn = str(
        identity.get("normalized")
        or identity.get("isbn")
        or catalog.get("isbn")
        or asset.get("isbn")
        or ""
    ).strip() or None
    category = str(asset.get("category") or labeled.get("category") or "unknown")
    if title:
        instruction = (
            f"This sealed copy is specifically {title}. "
            "The photo may show several books or objects. "
            "Assess only this copy: name it in identity_candidates, "
            "judge its condition and damage, and ignore other titles."
        )
    else:
        instruction = (
            "Assess only this one physical copy. "
            "If several objects are visible, do not merge them."
        )
    return {
        "title": title,
        "isbn": isbn,
        "category": category,
        "instruction": instruction,
    }


def _crop_ref(repository: SurveyRepository, survey_id: UUID, asset: dict) -> str | None:
    row_id = asset.get("row_id")
    slot = asset.get("slot")
    if row_id is None or slot is None:
        return None
    path = f"shelf_scans/crops/{row_id}_slot{slot}.jpg"
    if repository.exists_bytes(survey_id, path):
        return path
    return None
