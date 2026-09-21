from __future__ import annotations

from typing import Any

from backend.app.providers.models.contracts import AssessmentPipeline, ModelAssessment


class ProviderResponseError(ValueError):
    pass


def normalize_assessment(
    payload: dict[str, Any],
    *,
    expected_pipeline: AssessmentPipeline,
) -> ModelAssessment:
    """Validate untrusted model JSON before it enters the domain workflow."""
    assessment = ModelAssessment.model_validate(payload)
    if assessment.pipeline is not expected_pipeline:
        raise ProviderResponseError(
            f"expected {expected_pipeline.value} response, got {assessment.pipeline.value}"
        )
    return assessment

