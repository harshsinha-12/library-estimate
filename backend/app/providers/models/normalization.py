from __future__ import annotations

from typing import Any

from backend.app.providers.models.contracts import AssessmentPipeline, ModelAssessment

CATEGORIES = {
    "book",
    "portrait",
    "painting",
    "cup",
    "furniture",
    "electronics",
    "other",
    "unknown",
}
CATEGORY_ALIASES = {
    "computer": "electronics",
    "monitor": "electronics",
    "appliance": "electronics",
    "laptop": "electronics",
    "ipad": "electronics",
    "serial": "electronics",
    "shelf": "furniture",
    "table": "furniture",
    "bed": "furniture",
    "cupboard": "furniture",
    "wardrobe": "furniture",
}
CONDITIONS = {"new", "good", "worn", "damaged", "unknown"}
ACTIONS = {
    "accept_candidate",
    "recapture",
    "alternate_resolver",
    "human_review",
}


class ProviderResponseError(ValueError):
    pass


def normalize_assessment(
    payload: dict[str, Any],
    *,
    expected_pipeline: AssessmentPipeline,
) -> ModelAssessment:
    """Validate untrusted model JSON before it enters the domain workflow."""
    assessment = ModelAssessment.model_validate(_coerce_assessment(payload))
    if assessment.pipeline is not expected_pipeline:
        raise ProviderResponseError(
            f"expected {expected_pipeline.value} response, got {assessment.pipeline.value}"
        )
    return assessment


def _coerce_assessment(payload: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(payload)
    coerced["category"] = _category(coerced.get("category"))
    coerced["condition"] = _enum(coerced.get("condition"), CONDITIONS, "unknown")
    coerced["recommended_action"] = _enum(
        coerced.get("recommended_action"), ACTIONS, "human_review"
    )
    coerced["damage"] = _damage(coerced.get("damage"))
    refs = [
        str(item) for item in (coerced.get("evidence_refs") or []) if str(item).strip()
    ]
    coerced["evidence_refs"] = refs
    candidates = []
    for item in coerced.get("identity_candidates") or []:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate_refs = [
            str(ref) for ref in (candidate.get("evidence_refs") or []) if str(ref).strip()
        ] or refs[:1]
        if not candidate_refs or not str(candidate.get("label") or "").strip():
            continue
        candidate["evidence_refs"] = candidate_refs
        candidates.append(candidate)
    coerced["identity_candidates"] = candidates[:5]
    rationale = coerced.get("rationale")
    if isinstance(rationale, str) and len(rationale) > 1000:
        coerced["rationale"] = rationale[:1000]
    return coerced


def _category(value: object) -> str:
    raw = str(value or "unknown").strip().lower().replace(" ", "_")
    if raw in CATEGORIES:
        return raw
    return CATEGORY_ALIASES.get(raw, "unknown")


def _enum(value: object, allowed: set[str], default: str) -> str:
    raw = str(value or default).strip().lower()
    return raw if raw in allowed else default


def _damage(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        types = [
            str(item) for item in (value.get("types") or []) if str(item).strip()
        ]
        present = value.get("present")
        if present is None and types:
            present = True
        return {
            "present": present,
            "types": types,
            "description": value.get("description"),
        }
    if isinstance(value, list):
        types = [str(item) for item in value if str(item).strip()]
        return {"present": bool(types), "types": types, "description": None}
    return {"present": None, "types": [], "description": None}
