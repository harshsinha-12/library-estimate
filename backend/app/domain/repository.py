from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from redis import Redis

from backend.app.domain.models import (
    SurveyGeography,
    SurveyRecord,
    SurveyState,
    SurveyStateEvent,
    UploadedFile,
)
from backend.app.storage.objects import ObjectStore, object_key
from backend.app.utils.json_codec import pretty_json
from backend.app.utils.paths import validate_package_path

RECORD_VERSION = 1


class SurveyNotFoundError(KeyError):
    pass


class IdempotencyConflictError(ValueError):
    pass


def _envelope(payload: dict[str, Any]) -> str:
    return json.dumps(
        {"v": RECORD_VERSION, "payload": payload},
        separators=(",", ":"),
        sort_keys=True,
    )


def _unwrap(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    version = data.get("v")
    if version != RECORD_VERSION:
        raise ValueError(f"unsupported record version {version}")
    payload = data.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("record payload must be an object")
    return payload


class SurveyRepository:
    def __init__(
        self,
        redis_client: Redis,
        object_store: ObjectStore,
        *,
        key_prefix: str = "ls:v1",
    ) -> None:
        self.redis = redis_client
        self.object_store = object_store
        self.key_prefix = key_prefix.rstrip(":")

    def initialize(self) -> None:
        if self.redis.ping() is not True:
            raise RuntimeError("Redis ping failed during repository initialize")
        self.object_store.ping()

    def create(self, record: SurveyRecord) -> SurveyRecord:
        key = self._survey_key(record.survey_id)
        payload = _envelope(self._record_payload(record))
        created = self.redis.set(key, payload, nx=True)
        if not created:
            raise IdempotencyConflictError(f"survey already exists: {record.survey_id}")
        pipe = self.redis.pipeline()
        pipe.sadd(self._index_key(), str(record.survey_id))
        pipe.set(self._seq_key(record.survey_id), 1)
        pipe.rpush(
            self._events_key(record.survey_id),
            _envelope(
                {
                    "sequence": 1,
                    "survey_id": str(record.survey_id),
                    "state": record.status,
                    "occurred_at": record.created_at.isoformat(),
                    "detail": None,
                }
            ),
        )
        pipe.execute()
        return record

    def get(self, survey_id: UUID) -> SurveyRecord:
        raw = self.redis.get(self._survey_key(survey_id))
        if raw is None:
            raise SurveyNotFoundError(str(survey_id))
        return self._record_from_payload(_unwrap(raw))

    def store_upload(self, survey_id: UUID, upload: UploadedFile, content: bytes) -> UploadedFile:
        self.get(survey_id)
        validate_package_path(upload.path)
        self.object_store.put(
            object_key(survey_id, upload.path),
            content,
            upload.mime_type,
        )
        self.redis.hset(
            self._uploads_key(survey_id),
            upload.path,
            _envelope(upload.model_dump(mode="json")),
        )
        return upload

    def uploads(self, survey_id: UUID) -> dict[str, UploadedFile]:
        self.get(survey_id)
        rows = self.redis.hgetall(self._uploads_key(survey_id))
        return {
            path: UploadedFile.model_validate(_unwrap(raw))
            for path, raw in rows.items()
        }

    def get_bytes(self, survey_id: UUID, relative_path: str) -> bytes:
        return self.object_store.get(object_key(survey_id, relative_path))

    def put_bytes(
        self,
        survey_id: UUID,
        relative_path: str,
        content: bytes,
        content_type: str,
    ) -> None:
        self.object_store.put(object_key(survey_id, relative_path), content, content_type)

    def exists_bytes(self, survey_id: UUID, relative_path: str) -> bool:
        return self.object_store.exists(object_key(survey_id, relative_path))

    def seal(self, survey_id: UUID, package_hash: str, sealed_at: datetime) -> SurveyRecord:
        record = self.get(survey_id)
        updated = record.model_copy(update={"package_hash": package_hash, "sealed_at": sealed_at})
        self.redis.set(self._survey_key(survey_id), _envelope(self._record_payload(updated)))
        return updated

    def transition(
        self,
        survey_id: UUID,
        state: SurveyState,
        *,
        detail: str | None = None,
        occurred_at: datetime,
    ) -> SurveyRecord:
        record = self.get(survey_id)
        updated = record.model_copy(update={"status": state})
        seq_key = self._seq_key(survey_id)
        pipe = self.redis.pipeline()
        pipe.set(self._survey_key(survey_id), _envelope(self._record_payload(updated)))
        pipe.incr(seq_key)
        results = pipe.execute()
        sequence = int(results[1])
        self.redis.rpush(
            self._events_key(survey_id),
            _envelope(
                {
                    "sequence": sequence,
                    "survey_id": str(survey_id),
                    "state": state,
                    "occurred_at": occurred_at.isoformat(),
                    "detail": detail,
                }
            ),
        )
        return updated

    def events(self, survey_id: UUID) -> list[SurveyStateEvent]:
        self.get(survey_id)
        rows = self.redis.lrange(self._events_key(survey_id), 0, -1)
        return [SurveyStateEvent.model_validate(_unwrap(raw)) for raw in rows]

    def write_manifest(self, survey_id: UUID, manifest: dict[str, object]) -> None:
        self.put_bytes(
            survey_id,
            "manifest.json",
            pretty_json(manifest).encode("utf-8"),
            "application/json",
        )

    def get_idempotency(self, scope: str, key: str) -> tuple[str, str] | None:
        raw = self.redis.get(self._idempotency_key(scope, key))
        if raw is None:
            return None
        payload = _unwrap(raw)
        return payload["request_hash"], payload["response_json"]

    def save_idempotency(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        response_json: str,
        created_at: datetime,
    ) -> None:
        redis_key = self._idempotency_key(scope, key)
        payload = _envelope(
            {
                "request_hash": request_hash,
                "response_json": response_json,
                "created_at": created_at.isoformat(),
            }
        )
        created = self.redis.set(redis_key, payload, nx=True)
        if created:
            return
        existing = self.get_idempotency(scope, key)
        if existing is None:
            raise IdempotencyConflictError("idempotency record vanished during write")
        stored_hash, stored_json = existing
        if stored_hash != request_hash or stored_json != response_json:
            raise IdempotencyConflictError("idempotency key was already used for another request")

    def save_json(self, survey_id: UUID, name: str, payload: dict[str, Any]) -> None:
        self.redis.set(self._named_key(survey_id, name), _envelope(payload))

    def get_json(self, survey_id: UUID, name: str) -> dict[str, Any] | None:
        raw = self.redis.get(self._named_key(survey_id, name))
        if raw is None:
            return None
        return _unwrap(raw)

    def get_cache(self, name: str) -> dict[str, Any] | None:
        raw = self.redis.get(self._cache_key(name))
        if raw is None:
            return None
        return _unwrap(raw)

    def save_cache(self, name: str, payload: dict[str, Any], *, ttl_seconds: int) -> None:
        self.redis.set(self._cache_key(name), _envelope(payload), ex=ttl_seconds)

    def save_job(self, job_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        redis_key = f"{self.key_prefix}:job:{job_key}"
        created = self.redis.set(redis_key, _envelope(payload), nx=True)
        if not created:
            existing = self.redis.get(redis_key)
            if existing is None:
                raise IdempotencyConflictError("job record vanished during write")
            return _unwrap(existing)
        return payload

    def get_job(self, job_key: str) -> dict[str, Any] | None:
        raw = self.redis.get(f"{self.key_prefix}:job:{job_key}")
        if raw is None:
            return None
        return _unwrap(raw)

    def survey_ids(self) -> list[str]:
        return sorted(self.redis.smembers(self._index_key()))

    def delete_survey(self, survey_id: UUID) -> dict[str, int]:
        self.get(survey_id)
        object_keys = self.object_store.list_prefix(f"{survey_id}/")
        for key in object_keys:
            self.object_store.delete(key)
        redis_keys = list(self.redis.scan_iter(f"{self.key_prefix}:survey:{survey_id}*"))
        redis_keys.extend(self.redis.scan_iter(f"{self.key_prefix}:idempotency:upload:{survey_id}:*"))
        redis_keys.extend(self.redis.scan_iter(f"{self.key_prefix}:idempotency:seal:{survey_id}:*"))
        for create_key in self.redis.scan_iter(f"{self.key_prefix}:idempotency:create-survey:*"):
            raw = self.redis.get(create_key)
            if raw:
                response = json.loads(_unwrap(raw)["response_json"])
                if response.get("survey_id") == str(survey_id):
                    redis_keys.append(create_key)
        for job_key in self.redis.scan_iter(f"{self.key_prefix}:job:*"):
            raw = self.redis.get(job_key)
            if raw and _unwrap(raw).get("survey_id") == str(survey_id):
                redis_keys.append(job_key)
        for policy_id in self.redis.smembers(f"{self.key_prefix}:policies"):
            key = f"{self.key_prefix}:policy:{policy_id}"
            raw = self.redis.get(key)
            if raw:
                policy = json.loads(raw)
                if str(survey_id) in (
                    policy.get("train_survey_ids", []) + policy.get("holdout_survey_ids", [])
                ):
                    redis_keys.append(key)
                    self.redis.srem(f"{self.key_prefix}:policies", policy_id)
        if redis_keys:
            self.redis.delete(*set(redis_keys))
        access_key = f"{self.key_prefix}:access_log"
        for entry in self.redis.lrange(access_key, 0, -1):
            if str(survey_id) in entry:
                self.redis.lrem(access_key, 0, entry)
        self.redis.srem(self._index_key(), str(survey_id))
        return {"deleted_objects": len(object_keys), "deleted_redis_keys": len(set(redis_keys))}

    def _record_payload(self, record: SurveyRecord) -> dict[str, Any]:
        return record.model_dump(mode="json")

    def _record_from_payload(self, payload: dict[str, Any]) -> SurveyRecord:
        return SurveyRecord(
            survey_id=payload["survey_id"],
            display_name=payload["display_name"],
            geography=SurveyGeography.model_validate(payload["geography"]),
            status=payload["status"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            sealed_at=(
                datetime.fromisoformat(payload["sealed_at"]) if payload.get("sealed_at") else None
            ),
            package_hash=payload.get("package_hash"),
        )

    def _survey_key(self, survey_id: UUID) -> str:
        return f"{self.key_prefix}:survey:{survey_id}"

    def _uploads_key(self, survey_id: UUID) -> str:
        return f"{self.key_prefix}:survey:{survey_id}:uploads"

    def _events_key(self, survey_id: UUID) -> str:
        return f"{self.key_prefix}:survey:{survey_id}:events"

    def _seq_key(self, survey_id: UUID) -> str:
        return f"{self.key_prefix}:survey:{survey_id}:event_seq"

    def _index_key(self) -> str:
        return f"{self.key_prefix}:surveys"

    def _idempotency_key(self, scope: str, key: str) -> str:
        return f"{self.key_prefix}:idempotency:{scope}:{key}"

    def _named_key(self, survey_id: UUID, name: str) -> str:
        return f"{self.key_prefix}:survey:{survey_id}:{name}"

    def _cache_key(self, name: str) -> str:
        return f"{self.key_prefix}:cache:{name}"
