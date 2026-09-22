"""Plan R2/S3 plaintext encryption; execute only a reviewed plan ID."""

from __future__ import annotations

import argparse

from backend.app.config.settings import Settings
from backend.app.security.encryption_migration import (
    build_encryption_migration_plan,
    execute_encryption_migration,
)
from backend.app.storage.encrypted import EncryptedObjectStore
from backend.app.storage.objects import S3ObjectStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="", help="restrict migration to this object-key prefix")
    parser.add_argument(
        "--execute-plan",
        metavar="PLAN_ID",
        help="migrate only the exact object set and content reviewed in this dry run",
    )
    args = parser.parse_args()
    settings = Settings.from_environment()
    if not settings.data_encryption_key:
        raise RuntimeError("LIBRARY_DATA_ENCRYPTION_KEY is required")
    raw_store = S3ObjectStore(settings)
    encrypted_store = EncryptedObjectStore(raw_store, settings.data_encryption_key)
    plan = build_encryption_migration_plan(raw_store, prefix=args.prefix)
    print(f"plan_id={plan.plan_id}")
    print(f"prefix={plan.prefix!r}")
    print(f"plaintext={len(plan.plaintext_objects)} encrypted={len(plan.encrypted_keys)}")
    for key, digest in plan.plaintext_objects:
        print(f"plaintext {digest} {key}")
    if args.execute_plan:
        result = execute_encryption_migration(
            raw_store, encrypted_store, plan, approved_plan_id=args.execute_plan
        )
        print(f"execution={result}")
    else:
        print("dry run only")


if __name__ == "__main__":
    main()
