"""Review-bound survey retention plans and idempotent execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.app.domain.repository import SurveyNotFoundError, SurveyRepository


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    retention_days: int
    evaluated_at: datetime
    survey_ids: tuple[UUID, ...]
    plan_id: str


def build_retention_plan(
    repository: SurveyRepository, *, retention_days: int, now: datetime | None = None
) -> RetentionPlan:
    evaluated_at = now or datetime.now(UTC)
    ids = tuple(due_survey_ids(repository, retention_days=retention_days, now=evaluated_at))
    payload = {
        "retention_days": retention_days,
        "survey_ids": [str(value) for value in ids],
    }
    plan_id = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return RetentionPlan(retention_days, evaluated_at, ids, plan_id)


def execute_retention_plan(
    repository: SurveyRepository, plan: RetentionPlan, *, approved_plan_id: str
) -> dict[str, int]:
    if approved_plan_id != plan.plan_id:
        raise ValueError("approved retention plan ID does not match the dry run")
    deleted_surveys = deleted_objects = deleted_redis_keys = already_absent = 0
    for survey_id in plan.survey_ids:
        try:
            result = repository.delete_survey(survey_id)
        except SurveyNotFoundError:
            already_absent += 1
            continue
        deleted_surveys += 1
        deleted_objects += result["deleted_objects"]
        deleted_redis_keys += result["deleted_redis_keys"]
    return {
        "deleted_surveys": deleted_surveys,
        "deleted_objects": deleted_objects,
        "deleted_redis_keys": deleted_redis_keys,
        "already_absent": already_absent,
    }


def due_survey_ids(
    repository: SurveyRepository, *, retention_days: int, now: datetime | None = None
) -> list[UUID]:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    now = now or datetime.now(UTC)
    deadline = now - timedelta(days=retention_days)
    due: list[UUID] = []
    for value in repository.survey_ids():
        record = repository.get(UUID(value))
        age_from = record.sealed_at or record.created_at
        if age_from <= deadline:
            due.append(record.survey_id)
    return sorted(due, key=str)
