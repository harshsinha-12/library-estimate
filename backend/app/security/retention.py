"""Survey retention selection; callers must explicitly execute deletion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.app.domain.repository import SurveyRepository


def due_survey_ids(
    repository: SurveyRepository, *, retention_days: int, now: datetime | None = None
) -> list[UUID]:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    now = now or datetime.now(UTC)
    deadline = now - timedelta(days=retention_days)
    due = []
    for value in repository.survey_ids():
        record = repository.get(UUID(value))
        age_from = record.sealed_at or record.created_at
        if age_from <= deadline:
            due.append(record.survey_id)
    return due
