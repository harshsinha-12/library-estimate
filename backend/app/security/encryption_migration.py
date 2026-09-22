"""Dry-run-reviewed migration of plaintext object-store values to AES-GCM envelopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from backend.app.storage.encrypted import MAGIC, EncryptedObjectStore
from backend.app.storage.objects import ObjectStore


@dataclass(frozen=True, slots=True)
class EncryptionMigrationPlan:
    prefix: str
    plaintext_objects: tuple[tuple[str, str], ...]
    encrypted_keys: tuple[str, ...]
    plan_id: str

    @property
    def plaintext_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _digest in self.plaintext_objects)


def build_encryption_migration_plan(
    raw_store: ObjectStore, *, prefix: str = ""
) -> EncryptionMigrationPlan:
    plaintext: list[tuple[str, str]] = []
    encrypted: list[str] = []
    for key in sorted(raw_store.list_prefix(prefix)):
        data = raw_store.get(key)
        if data.startswith(MAGIC):
            encrypted.append(key)
        else:
            plaintext.append((key, hashlib.sha256(data).hexdigest()))
    payload = {
        "prefix": prefix,
        "plaintext_objects": plaintext,
        "encrypted_keys": encrypted,
    }
    plan_id = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return EncryptionMigrationPlan(prefix, tuple(plaintext), tuple(encrypted), plan_id)


def execute_encryption_migration(
    raw_store: ObjectStore,
    encrypted_store: EncryptedObjectStore,
    plan: EncryptionMigrationPlan,
    *,
    approved_plan_id: str,
) -> dict[str, int]:
    if approved_plan_id != plan.plan_id:
        raise ValueError("approved encryption migration plan ID does not match the dry run")
    migrated = already_encrypted = changed_since_review = 0
    for key, reviewed_digest in plan.plaintext_objects:
        current = raw_store.get(key)
        if current.startswith(MAGIC):
            already_encrypted += 1
            continue
        if hashlib.sha256(current).hexdigest() != reviewed_digest:
            changed_since_review += 1
            continue
        encrypted_store.put(key, current, "application/octet-stream")
        migrated += 1
    return {
        "migrated": migrated,
        "already_encrypted": already_encrypted,
        "changed_since_review": changed_since_review,
    }
