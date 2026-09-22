import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient

from backend.app.domain.models import SurveyGeography, SurveyRecord, UploadedFile
from backend.app.main import create_app
from backend.app.security.encryption_migration import (
    build_encryption_migration_plan,
    execute_encryption_migration,
)
from backend.app.security.retention import build_retention_plan, execute_retention_plan
from backend.app.storage.encrypted import MAGIC, EncryptedObjectStore
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now
from backend.app.utils.hashing import sha256_bytes


def test_encrypted_objects_bind_ciphertext_to_path() -> None:
    inner = MemoryObjectStore()
    secret = base64.b64encode(bytes(range(32))).decode()
    store = EncryptedObjectStore(inner, secret)
    store.put("survey/a.jpg", b"private image", "image/jpeg")
    assert store.get("survey/a.jpg") == b"private image"
    assert inner.get("survey/a.jpg").startswith(MAGIC)
    assert b"private image" not in inner.get("survey/a.jpg")
    inner.put("survey/b.jpg", inner.get("survey/a.jpg"), "application/octet-stream")
    try:
        store.get("survey/b.jpg")
    except Exception:
        pass
    else:
        raise AssertionError("ciphertext was accepted under another object key")


def test_plaintext_encryption_migration_is_review_bound_and_idempotent() -> None:
    raw = MemoryObjectStore()
    raw.put("survey/a.jpg", b"private image", "image/jpeg")
    secret = base64.b64encode(bytes(range(32))).decode()
    encrypted = EncryptedObjectStore(raw, secret)
    plan = build_encryption_migration_plan(raw, prefix="survey/")
    assert plan.plaintext_keys == ("survey/a.jpg",)
    try:
        execute_encryption_migration(raw, encrypted, plan, approved_plan_id="wrong")
    except ValueError:
        pass
    else:
        raise AssertionError("migration accepted an unreviewed plan")
    assert raw.get("survey/a.jpg") == b"private image"
    assert execute_encryption_migration(
        raw, encrypted, plan, approved_plan_id=plan.plan_id
    )["migrated"] == 1
    assert encrypted.get("survey/a.jpg") == b"private image"
    second = execute_encryption_migration(raw, encrypted, plan, approved_plan_id=plan.plan_id)
    assert second == {"migrated": 0, "already_encrypted": 1, "changed_since_review": 0}


def test_retention_execution_requires_reviewed_plan_and_is_idempotent() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    repository = create_app(
        redis_client=redis, object_store=MemoryObjectStore(), key_prefix="test:retention"
    ).state.survey_workflow.repository
    survey_id = uuid4()
    now = datetime(2026, 9, 22, tzinfo=UTC)
    repository.create(SurveyRecord(
        survey_id=survey_id,
        display_name="Expired",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="created",
        created_at=now - timedelta(days=31),
    ))
    plan = build_retention_plan(repository, retention_days=30, now=now)
    assert plan.survey_ids == (survey_id,)
    try:
        execute_retention_plan(repository, plan, approved_plan_id="wrong")
    except ValueError:
        pass
    else:
        raise AssertionError("retention accepted an unreviewed plan")
    first = execute_retention_plan(repository, plan, approved_plan_id=plan.plan_id)
    second = execute_retention_plan(repository, plan, approved_plan_id=plan.plan_id)
    assert first["deleted_surveys"] == 1
    assert second["already_absent"] == 1


def test_operator_auth_evidence_and_confirmed_deletion() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    inner = MemoryObjectStore()
    app = create_app(
        redis_client=redis, object_store=inner, key_prefix="test:secure",
        operator_token="test-secret", require_auth=True,
        data_encryption_key=base64.b64encode(bytes(range(32))).decode(),
    )
    repository = app.state.survey_workflow.repository
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Private",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="created", created_at=utc_now(),
    ))
    data = b"book image"
    repository.store_upload(survey_id, UploadedFile(
        path="shelf/image.jpg", mime_type="image/jpeg", bytes=len(data),
        sha256=sha256_bytes(data),
    ), data)
    repository.save_idempotency(
        scope="create-survey", key="private-survey", request_hash="hash",
        response_json=json.dumps({"survey_id": str(survey_id)}), created_at=utc_now(),
    )
    path = f"/v1/surveys/{survey_id}/evidence?path=shelf/image.jpg"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        headers = {"Authorization": "Bearer test-secret"}
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["Cache-Control"] == "no-store"
        assert client.delete(f"/v1/surveys/{survey_id}", headers=headers).status_code == 422
        removed = client.delete(
            f"/v1/surveys/{survey_id}",
            headers={**headers, "X-Confirm-Delete": str(survey_id)},
        )
        assert removed.status_code == 200
        assert removed.json()["deleted_objects"] == 1
        assert client.get(path, headers=headers).status_code == 404
    logs = redis.lrange("test:secure:access_log", 0, -1)
    assert any(json.loads(row)["status"] == 401 for row in logs)
    assert all("test-secret" not in row for row in logs)
    assert all(str(survey_id) not in row for row in logs)
    assert redis.get("test:secure:idempotency:create-survey:private-survey") is None
