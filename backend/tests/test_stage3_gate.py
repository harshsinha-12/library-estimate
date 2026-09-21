from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.config.settings import Settings
from backend.app.main import create_app
from backend.app.providers.catalog.chain import CatalogChain
from backend.app.workflows.identifiers import type_identifier
from backend.app.workflows.stage3 import TAXONOMY, asset_policy
from backend.tests.conftest import isolated_app
from backend.tests.test_survey_api import STRUCTURE, create_survey, headers, manifest


def test_identifier_checksums_and_types() -> None:
    assert type_identifier("978-0-14-312755-0").valid
    assert type_identifier("978-0-14-312755-0", "isbn").valid
    assert type_identifier("9791090636071").valid
    assert type_identifier("9780143127551").valid is False
    assert type_identifier("0-306-40615-2").valid
    assert type_identifier("2049-3630", "issn").valid
    assert type_identifier("LIB-000123", "library_barcode").kind == "library_barcode"
    assert type_identifier("4006381333931").valid is False  # retail EAN is not an ISBN


def test_catalog_falls_back_and_never_returns_sale_info() -> None:
    calls = []

    def fetch(url):
        calls.append(url)
        if "openlibrary" in url:
            return {"key": "/books/one", "title": "Wrong adjacent book", "publishers": ["A"]}
        return {
            "items": [
                {
                    "id": "volume-1",
                    "volumeInfo": {
                        "title": "The Example Book",
                        "authors": ["A. Writer"],
                        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780143127550"}],
                    },
                    "saleInfo": {"retailPrice": {"amount": 99999}},
                }
            ]
        }

    app, _, _ = isolated_app()
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
    repository = app.state.survey_workflow.repository
    chain = CatalogChain(fetch)
    result = chain.resolve(repository, survey_id, "9780143127550", title="The Example Book")
    assert result["source"] == "google_books"
    assert result["status"] == "candidate"
    assert "saleInfo" not in json.dumps(result)
    assert len(calls) == 2
    assert chain.resolve(repository, survey_id, "9780143127550")["from_cache"]
    assert len(calls) == 2


def test_stage3_sealed_gate(monkeypatch) -> None:
    from backend.app.workflows import stage3

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        stage3,
        "transcribe_segments",
        lambda _audio, start, *, model: [
            {
                "id": "speech-portrait",
                "text": "This portrait is damaged in the lower right",
                "monotonic_seconds": start + 3.0,
            }
        ],
    )
    shelf = {
        "passes": [
            {
                "pass_id": "b",
                "room_id": "library",
                "shelf_id": "shelf_1",
                "face_id": "shelf_1.face_A",
                "face_normal": [0, 0, 1],
                "rows": [
                    {
                        "row_id": "row_01",
                        "coverage": 1,
                        "spines": [
                            {
                                "slot": 0,
                                "x": 0.1,
                                "t": 1,
                                "isbn": "9780143127551",
                                "evidence_ref": "spine-bad",
                            },
                            {"slot": 1, "x": 0.3, "t": 1, "evidence_ref": "spine-no-isbn"},
                        ],
                    }
                ],
            }
        ]
    }
    marks = [
        {
            "id": "p",
            "asset_copy_id": "portrait-1",
            "category": "portrait",
            "label": "Family portrait",
            "room_id": "library",
            "monotonic_seconds": 10.0,
            "evidence_ref": "closeups/portrait.jpg",
        },
        {
            "id": "m",
            "asset_copy_id": "mug-1",
            "category": "cup",
            "label": "Blue mug",
            "room_id": "library",
            "monotonic_seconds": 13.0,
            "evidence_ref": "closeups/mug.jpg",
        },
    ]
    pass_c = {
        "scans": [
            {
                "id": "bad",
                "kind": "barcode",
                "face_id": "shelf_1.face_A",
                "row_id": "row_01",
                "slot": 0,
                "barcode": "9780143127551",
                "evidence_ref": "closeups/bad.jpg",
            },
            {
                "id": "tear",
                "kind": "damage",
                "asset_copy_id": "portrait-1",
                "damage_type": "tear",
                "region": "lower right",
                "closeup_ref": "closeups/damage.jpg",
                "scale_ref": "closeups/scale.jpg",
                "evidence_ref": "closeups/damage.jpg",
            },
        ],
        "notes": [{"id": "ambiguous", "text": "Something is scratched", "monotonic_seconds": 99}],
        "focus_events": [
            {"id": "point-portrait", "asset_copy_id": "portrait-1", "monotonic_seconds": 10.0},
            {"id": "point-mug", "asset_copy_id": "mug-1", "monotonic_seconds": 13.0},
        ],
    }
    uploads = [
        ("roomplan/processed/structure.json", "application/json", STRUCTURE),
        ("roomplan/model.usdz", "model/vnd.usdz+zip", b"fixture-usdz"),
        ("shelf_scans/labeled.json", "application/json", json.dumps(shelf).encode()),
        ("other_assets/marks.json", "application/json", json.dumps(marks).encode()),
        ("exceptions/pass-c.json", "application/json", json.dumps(pass_c).encode()),
        ("audio/survey.m4a", "audio/mp4", b"fixture-audio"),
        (
            "audio/timing.json",
            "application/json",
            json.dumps({"started_monotonic_seconds": 7.0}).encode(),
        ),
        ("closeups/damage.jpg", "image/jpeg", b"damage-closeup"),
        ("closeups/scale.jpg", "image/jpeg", b"ruler-closeup"),
    ]
    app, redis_client, object_store = isolated_app()
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        for path, mime, data in uploads:
            assert (
                client.post(
                    f"/v1/surveys/{survey_id}/uploads",
                    params={"path": path},
                    headers={**headers("upload-" + path), "Content-Type": mime},
                    content=data,
                ).status_code
                == 200
            )
        sealed = manifest(survey_id, uploads)
        sealed["capture_modes"] = ["room", "shelf", "exception"]
        assert (
            client.post(
                f"/v1/surveys/{survey_id}/seal", headers=headers("seal-stage3"), json=sealed
            ).status_code
            == 200
        )
        review = client.get(f"/v1/surveys/{survey_id}/review").json()
        inventory = client.get(f"/v1/surveys/{survey_id}/inventory").json()
        books = [item for item in review["assets"] if item["category"] == "book"]
        assert len(books) == 2 and len({item["asset_copy_id"] for item in books}) == 2
        assert all(item["isbn"] is None for item in books)
        assert all(item["isbn"] is None for item in inventory["asset_copies"])
        assert (
            next(item for item in review["identities"] if item["raw"] == "9780143127551")[
                "usable_for_isbn_price_query"
            ]
            is False
        )
        no_isbn_id = next(item["asset_copy_id"] for item in books if item["slot"] == 1)
        assert no_isbn_id == next(
            item["asset_copy_id"] for item in inventory["asset_copies"] if item["slot"] == 1
        )
        assert next(item for item in review["assets"] if item["asset_copy_id"] == "mug-1")[
            "excluded"
        ]
        assert (
            next(item for item in review["assets"] if item["asset_copy_id"] == "mug-1")[
                "valuation_required"
            ]
            is False
        )
        assert next(item for item in review["assets"] if item["asset_copy_id"] == "portrait-1")[
            "requires_appraisal"
        ]
        spoken = next(item for item in review["notes"] if item["note_id"] == "speech-portrait")
        assert spoken["asset_copy_id"] == "portrait-1"
        assert spoken["closeup_ref"] == "closeups/damage.jpg"
        assert spoken["status"] == "operator_assertion"
        kinds = {item["kind"] for item in review["queue"]}
        assert {"rescan_barcode", "unread_spine", "unbound_note", "high_value"} <= kinds
        unbound = next(item for item in review["queue"] if item["kind"] == "unbound_note")
        decided = client.post(
            f"/v1/reviews/{unbound['id']}/decision",
            json={"survey_id": survey_id, "action": "bind_note", "asset_copy_id": "portrait-1"},
        )
        assert decided.status_code == 200
        assert (
            next(item for item in decided.json()["notes"] if item["note_id"] == "ambiguous")[
                "asset_copy_id"
            ]
            == "portrait-1"
        )
        assert client.get(f"/v1/surveys/{survey_id}/review").json()["queue"]
    restarted = create_app(
        redis_client=redis_client,
        object_store=object_store,
        key_prefix=app.state.survey_workflow.repository.key_prefix,
    )
    with TestClient(restarted) as client:
        after_restart = client.get(f"/v1/surveys/{survey_id}/review").json()
        assert (
            next(item["asset_copy_id"] for item in after_restart["assets"] if item.get("slot") == 1)
            == no_isbn_id
        )


def test_taxonomy_covers_demo_objects() -> None:
    assert {
        "book",
        "portrait",
        "cup",
        "painting",
        "computer",
        "monitor",
        "printer",
        "furniture",
        "shelf",
        "appliance",
        "serial",
        "sculpture",
        "decorative_object",
        "other",
    } <= TAXONOMY
    assert asset_policy("cup") == {
        "valuation_required": False,
        "requires_appraisal": False,
        "excluded": True,
    }


def test_settings_repr_redacts_credentials() -> None:
    settings = Settings.from_environment()
    visible = repr(settings)
    assert all(
        value not in visible
        for value in (
            settings.redis_password,
            settings.s3_access_key_id,
            settings.s3_secret_access_key,
        )
    )
