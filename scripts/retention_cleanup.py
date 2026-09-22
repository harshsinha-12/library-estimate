"""Plan retention; execute only a matching, previously reviewed plan ID."""

from __future__ import annotations

import argparse
import os

from backend.app.config.settings import Settings
from backend.app.domain.repository import SurveyRepository
from backend.app.security.retention import build_retention_plan, execute_retention_plan
from backend.app.storage.encrypted import EncryptedObjectStore
from backend.app.storage.objects import S3ObjectStore
from backend.app.storage.redis_client import connect_redis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute-plan",
        metavar="PLAN_ID",
        help="delete only if the current dry-run plan exactly matches this reviewed ID",
    )
    args = parser.parse_args()
    settings = Settings.from_environment()
    store = S3ObjectStore(settings)
    if settings.data_encryption_key:
        store = EncryptedObjectStore(store, settings.data_encryption_key)
    repository = SurveyRepository(
        connect_redis(settings), store, key_prefix=settings.redis_key_prefix
    )
    days = int(os.getenv("LIBRARY_RETENTION_DAYS", "30"))
    plan = build_retention_plan(repository, retention_days=days)
    print(f"plan_id={plan.plan_id}")
    print(f"evaluated_at={plan.evaluated_at.isoformat()}")
    for survey_id in plan.survey_ids:
        print(f"due {survey_id}")
    if args.execute_plan:
        result = execute_retention_plan(repository, plan, approved_plan_id=args.execute_plan)
        print(f"execution={result}")
    else:
        print(f"{len(plan.survey_ids)} surveys due; dry run only")


if __name__ == "__main__":
    main()
