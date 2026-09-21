"""List expired surveys; pass --execute to delete their Redis and object records."""

from __future__ import annotations

import argparse
import os

from backend.app.config.settings import Settings
from backend.app.domain.repository import SurveyRepository
from backend.app.security.retention import due_survey_ids
from backend.app.storage.encrypted import EncryptedObjectStore
from backend.app.storage.objects import S3ObjectStore
from backend.app.storage.redis_client import connect_redis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_environment()
    store = S3ObjectStore(settings)
    if settings.data_encryption_key:
        store = EncryptedObjectStore(store, settings.data_encryption_key)
    repository = SurveyRepository(
        connect_redis(settings), store, key_prefix=settings.redis_key_prefix
    )
    days = int(os.getenv("LIBRARY_RETENTION_DAYS", "30"))
    due = due_survey_ids(repository, retention_days=days)
    for survey_id in due:
        if args.execute:
            print(f"deleted {survey_id}: {repository.delete_survey(survey_id)}")
        else:
            print(f"due {survey_id}")
    print(f"{len(due)} surveys {'deleted' if args.execute else 'due; dry run only'}")


if __name__ == "__main__":
    main()
