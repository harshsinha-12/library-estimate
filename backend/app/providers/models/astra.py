from __future__ import annotations

from typing import Any

from backend.app.providers.models.contracts import AssessmentPipeline, ModelAssessment
from backend.app.providers.models.normalization import normalize_assessment


def parse_astra_response(payload: dict[str, Any]) -> ModelAssessment:
    return normalize_assessment(payload, expected_pipeline=AssessmentPipeline.ASTRA_REPLAY)

