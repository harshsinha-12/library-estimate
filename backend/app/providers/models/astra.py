from __future__ import annotations

from typing import Any

from backend.app.providers.models.contracts import (
    AssessmentPipeline,
    AstraLiveAssist,
    AstraLiveQuality,
    ModelAssessment,
)
from backend.app.providers.models.normalization import (
    FORBIDDEN_ASSESSMENT_KEYS,
    normalize_assessment,
)

ALLOWED_LIVE_FIELDS = set(AstraLiveAssist.model_fields)
ALLOWED_QUALITY_FIELDS = set(AstraLiveQuality.model_fields)


def parse_astra_response(payload: dict[str, Any]) -> ModelAssessment:
    return normalize_assessment(payload, expected_pipeline=AssessmentPipeline.ASTRA_REPLAY)


def parse_astra_live_response(payload: dict[str, Any]) -> AstraLiveAssist:
    cleaned = {
        key: value
        for key, value in dict(payload).items()
        if key in ALLOWED_LIVE_FIELDS and key not in FORBIDDEN_ASSESSMENT_KEYS
    }
    quality = cleaned.get("quality")
    if isinstance(quality, dict):
        cleaned["quality"] = {
            key: value
            for key, value in quality.items()
            if key in ALLOWED_QUALITY_FIELDS
        }
    else:
        cleaned["quality"] = {}
    slots = [
        str(item) for item in (cleaned.get("unreadable_slots") or []) if str(item).strip()
    ]
    cleaned["unreadable_slots"] = slots[:40]
    count = cleaned.get("provisional_count")
    if count is not None:
        try:
            cleaned["provisional_count"] = max(0, min(500, int(count)))
        except (TypeError, ValueError):
            cleaned["provisional_count"] = None
    cleaned["pipeline"] = "astra_live"
    cleaned["authority"] = "assist_metadata"
    return AstraLiveAssist.model_validate(cleaned)
