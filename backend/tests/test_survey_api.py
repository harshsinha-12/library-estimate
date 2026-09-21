from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.utils.hashing import sha256_bytes
from backend.tests.conftest import isolated_app

GEOGRAPHY = {
    "country_code": "IN",
    "region": "Delhi",
    "city": "New Delhi",
    "currency": "INR",
    "market": "en-IN",
    "source": "manual",
    "precise_location_consent": False,
}

STRUCTURE = b'''{
  "format": "library-roomplan-1.0",
  "rooms": [{
    "identifier": "room-1",
    "label": "Library",
    "walls": [
      {"identifier": "w1", "dimensions": [4, 2.8, 0.1],
       "transform": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]},
      {"identifier": "w2", "dimensions": [3, 2.8, 0.1],
       "transform": [0,0,1,0, 0,1,0,0, -1,0,0,0, 2,0,1.5,1]}
    ],
    "doors": [], "windows": [], "openings": []
  }]
}\n'''


def headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


def create_survey(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/surveys",
        headers=headers("create-demo"),
        json={"display_name": "Demo Library", "geography": GEOGRAPHY},
    )
    assert response.status_code == 201
    return response.json()


def manifest(
    survey_id: str,
    uploads: list[tuple[str, str, bytes]],
) -> dict[str, object]:
    started_at = datetime(2026, 9, 21, 4, 30, tzinfo=UTC)
    return {
        "schema_version": "1.0.0",
        "survey_id": survey_id,
        "session_id": str(uuid4()),
        "app": {"version": "0.1.0", "build": "1"},
        "device": {
            "model": "iPhone",
            "system_version": "18.0",
            "supports_lidar": True,
            "roomplan_version": "1",
            "vision_version": "1",
        },
        "geography": GEOGRAPHY,
        "consent": {
            "video": True,
            "audio": True,
            "location": True,
            "retention_policy_id": "demo-v1",
        },
        "timing": {
            "started_at": started_at.isoformat(),
            "ended_at": (started_at + timedelta(minutes=5)).isoformat(),
            "monotonic_anchor_seconds": 100,
            "timezone": "Asia/Kolkata",
        },
        "capture_state": "complete",
        "capture_modes": ["room"],
        "files": [
            {
                "path": path,
                "mime_type": mime_type,
                "bytes": len(content),
                "sha256": sha256_bytes(content),
            }
            for path, mime_type, content in uploads
        ],
    }


def test_create_upload_and_seal_round_trip() -> None:
    app, _, store = isolated_app()
    with TestClient(app) as client:
        survey = create_survey(client)
        survey_id = survey["survey_id"]
        content = STRUCTURE

        upload = client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "roomplan/processed/structure.json"},
            headers={**headers("upload-roomplan"), "Content-Type": "application/json"},
            content=content,
        )
        assert upload.status_code == 200
        assert upload.json()["sha256"] == sha256_bytes(content)

        sealed = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-demo"),
            json=manifest(
                survey_id,
                [("roomplan/processed/structure.json", "application/json", content)],
            ),
        )
        assert sealed.status_code == 200
        assert sealed.json()["file_count"] == 1

        stored = client.get(f"/v1/surveys/{survey_id}")
        assert stored.status_code == 200
        assert stored.json()["status"] == "partial"
        assert store.exists(f"{survey_id}/manifest.json")
        assert store.exists(f"{survey_id}/derived/plan.svg")

        jobs = client.get(f"/v1/surveys/{survey_id}/jobs")
        assert jobs.status_code == 200
        assert [event["state"] for event in jobs.json()] == [
            "created",
            "capturing",
            "uploading",
            "ingest_validation",
            "partial",
        ]
        retry = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-retry-after-done"),
            json=manifest(
                survey_id,
                [("roomplan/processed/structure.json", "application/json", content)],
            ),
        )
        assert retry.status_code == 200
        assert retry.json()["status"] == "partial"


def test_complete_roomplan_package_reaches_geometry_state() -> None:
    app, _, _ = isolated_app()
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        uploads = [
            ("roomplan/processed/structure.json", "application/json", STRUCTURE),
            ("roomplan/model.usdz", "model/vnd.usdz+zip", b"fixture-usdz"),
        ]
        for index, (path, mime_type, content) in enumerate(uploads):
            response = client.post(
                f"/v1/surveys/{survey_id}/uploads",
                params={"path": path},
                headers={
                    **headers(f"upload-{index}"),
                    "Content-Type": mime_type,
                },
                content=content,
            )
            assert response.status_code == 200

        sealed = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-complete-roomplan"),
            json=manifest(survey_id, uploads),
        )

    assert sealed.status_code == 200
    assert sealed.json()["status"] == "geometry"
    assert sealed.json()["geometry_svg_path"] == "derived/plan.svg"
    assert sealed.json()["usdz_path"] == "roomplan/model.usdz"


def test_geometry_keeps_sealed_svg_immutable_and_survives_restart() -> None:
    app, redis_client, store = isolated_app()
    prefix = app.state.survey_workflow.repository.key_prefix
    captured_svg = b"<svg xmlns='http://www.w3.org/2000/svg'><title>capture</title></svg>"
    uploads = [
        ("roomplan/processed/structure.json", "application/json", STRUCTURE),
        ("roomplan/model.usdz", "model/vnd.usdz+zip", b"fixture-usdz"),
        ("generated/plan.svg", "image/svg+xml", captured_svg),
    ]
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        for path, mime_type, content in uploads:
            response = client.post(
                f"/v1/surveys/{survey_id}/uploads",
                params={"path": path},
                headers={
                    **headers(f"upload-{path}-{sha256_bytes(content)}"),
                    "Content-Type": mime_type,
                },
                content=content,
            )
            assert response.status_code == 200
        response = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-immutable-svg"),
            json=manifest(survey_id, uploads),
        )
        assert response.status_code == 200
        assert response.json()["geometry_svg_path"] == "derived/plan.svg"

    assert store.get(f"{survey_id}/generated/plan.svg") == captured_svg
    assert sha256_bytes(store.get(f"{survey_id}/generated/plan.svg")) == sha256_bytes(captured_svg)
    assert store.get(f"{survey_id}/derived/plan.svg") != captured_svg
    restarted, _, _ = isolated_app(
        redis_client=redis_client, object_store=store, key_prefix=prefix
    )
    with TestClient(restarted) as client:
        stored = client.get(f"/v1/surveys/{survey_id}")
        assert stored.status_code == 200
        assert stored.json()["status"] == "geometry"
        replay = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-immutable-svg"),
            json=json.loads(store.get(f"{survey_id}/manifest.json").decode("utf-8")),
        )
        assert replay.status_code == 200
        assert replay.json()["package_hash"] == response.json()["package_hash"]


def test_upload_allows_identical_bytes_at_different_paths() -> None:
    app, _, _ = isolated_app()
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        for path in (
            "roomplan/raw/room-data.json",
            "roomplan/processed/structure.json",
        ):
            response = client.post(
                f"/v1/surveys/{survey_id}/uploads",
                params={"path": path},
                headers={
                    **headers(f"upload-{path}-{sha256_bytes(STRUCTURE)}"),
                    "Content-Type": "application/json",
                },
                content=STRUCTURE,
            )
            assert response.status_code == 200
            assert response.json()["path"] == path


def test_interrupted_upload_retries_after_backend_restart_without_duplicate_events() -> None:
    app, redis_client, store = isolated_app()
    prefix = app.state.survey_workflow.repository.key_prefix
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        first = client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "roomplan/processed/structure.json"},
            headers={**headers("retry-structure"), "Content-Type": "application/json"},
            content=STRUCTURE,
        )
        assert first.status_code == 200

    from backend.app.main import create_app

    restarted = create_app(redis_client=redis_client, object_store=store, key_prefix=prefix)
    with TestClient(restarted) as client:
        retry = client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "roomplan/processed/structure.json"},
            headers={**headers("retry-structure"), "Content-Type": "application/json"},
            content=STRUCTURE,
        )
        assert retry.status_code == 200
        assert retry.json() == first.json()
        usdz = client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "roomplan/model.usdz"},
            headers={**headers("retry-usdz"), "Content-Type": "model/vnd.usdz+zip"},
            content=b"fixture-usdz",
        )
        assert usdz.status_code == 200
        sealed = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("retry-seal"),
            json=manifest(
                survey_id,
                [
                    ("roomplan/processed/structure.json", "application/json", STRUCTURE),
                    ("roomplan/model.usdz", "model/vnd.usdz+zip", b"fixture-usdz"),
                ],
            ),
        )
        assert sealed.status_code == 200
        assert sealed.json()["status"] == "geometry"
        events = client.get(f"/v1/surveys/{survey_id}/jobs").json()
        assert [event["state"] for event in events] == [
            "created", "capturing", "uploading", "ingest_validation", "geometry",
        ]


def test_create_survey_is_idempotent_and_rejects_key_reuse() -> None:
    app, _, _ = isolated_app()
    with TestClient(app) as client:
        first = create_survey(client)
        repeated = create_survey(client)
        assert repeated["survey_id"] == first["survey_id"]

        conflict = client.post(
            "/v1/surveys",
            headers=headers("create-demo"),
            json={"display_name": "A different library", "geography": GEOGRAPHY},
        )
    assert conflict.status_code == 409
    assert "another request" in conflict.json()["detail"]


def test_upload_rejects_unsafe_package_path(tmp_path) -> None:
    app, _, _ = isolated_app()
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        response = client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "../escape.json"},
            headers={**headers("unsafe-upload"), "Content-Type": "application/json"},
            content=b"{}\n",
        )
    assert response.status_code == 422
    assert not (tmp_path / "escape.json").exists()


def test_upload_rejects_server_derived_path() -> None:
    app, _, store = isolated_app()
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        response = client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "derived/plan.svg"},
            headers={**headers("reserved-upload"), "Content-Type": "image/svg+xml"},
            content=b"<svg/>",
        )
    assert response.status_code == 422
    assert not store.exists(f"{survey_id}/derived/plan.svg")


def test_seal_rejects_hash_mismatch() -> None:
    app, _, _ = isolated_app()
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        content = STRUCTURE
        client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "roomplan/processed/structure.json"},
            headers={**headers("upload-roomplan"), "Content-Type": "application/json"},
            content=content,
        )
        invalid = manifest(
            survey_id,
            [("roomplan/processed/structure.json", "application/json", content)],
        )
        invalid["files"][0]["sha256"] = "0" * 64
        response = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-bad-hash"),
            json=invalid,
        )
    assert response.status_code == 409
    assert "SHA-256 mismatch" in response.json()["detail"]
    with TestClient(app) as verification_client:
        stored = verification_client.get(f"/v1/surveys/{survey_id}")
    assert stored.json()["status"] == "recapture_required"


def test_repository_has_no_sqlite_dependency() -> None:
    from pathlib import Path

    source = Path("backend/app/domain/repository.py").read_text(encoding="utf-8")
    assert "sqlite" not in source.lower()
    assert "sqlite3" not in source
