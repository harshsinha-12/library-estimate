from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.tests.conftest import isolated_app
from backend.tests.test_survey_api import STRUCTURE, create_survey, headers, manifest
from cv.library_vision.pipeline import count_labeled_shelf

LABELED_SHELF = {
    "passes": [
        {
            "pass_id": "pass_a",
            "room_id": "library",
            "shelf_id": "shelf_01",
            "face_id": "shelf_01.face_A",
            "face_normal": [0, 0, 1],
            "label": "Shelf 1 A",
            "min_x": 0.4,
            "min_z": 0.3,
            "max_x": 1.6,
            "max_z": 0.7,
            "capacity_m": 1.2,
            "evidence_bytes": 412_000,
            "t": 1.0,
            "quality": {
                "blur": 0.1,
                "glare": 0.05,
                "speed": 0.1,
                "text_pixel_height": 22,
                "occlusion": 0.05,
            },
            "rows": [
                {
                    "row_id": "row_01",
                    "coverage": 0.96,
                    "capacity_m": 0.6,
                    "spines": [
                        {
                            "slot": 0,
                            "x": 0.12,
                            "t": 1.0,
                            "isbn": "9780143127550",
                            "appearance": "blue-spine",
                            "evidence_ref": "frame_a1",
                        },
                        {
                            "slot": 1,
                            "x": 0.28,
                            "t": 1.1,
                            "isbn": "9780143127550",
                            "appearance": "blue-spine-right",
                            "evidence_ref": "frame_a2",
                        },
                    ],
                },
                {
                    "row_id": "row_02",
                    "coverage": 0.91,
                    "capacity_m": 0.6,
                    "spines": [
                        {
                            "slot": 0,
                            "x": 0.15,
                            "t": 2.0,
                            "isbn": "9780061122415",
                            "appearance": "red-spine",
                            "evidence_ref": "frame_a3",
                        }
                    ],
                },
                {"row_id": "row_03", "coverage": 0.18, "capacity_m": 0.6, "spines": []},
            ],
        },
        {
            "pass_id": "pass_a_reverse",
            "room_id": "library",
            "shelf_id": "shelf_01",
            "face_id": "shelf_01.face_A",
            "face_normal": [0, 0, 1],
            "label": "Shelf 1 A",
            "min_x": 0.4,
            "min_z": 0.3,
            "max_x": 1.6,
            "max_z": 0.7,
            "capacity_m": 1.2,
            "evidence_bytes": 380_000,
            "t": 8.0,
            "rows": [
                {
                    "row_id": "row_01",
                    "coverage": 0.94,
                    "capacity_m": 0.6,
                    "spines": [
                        {
                            "slot": 1,
                            "x": 0.29,
                            "t": 8.0,
                            "isbn": "9780143127550",
                            "appearance": "blue-spine-right",
                            "evidence_ref": "frame_r1",
                        },
                        {
                            "slot": 0,
                            "x": 0.13,
                            "t": 8.1,
                            "isbn": "9780143127550",
                            "appearance": "blue-spine",
                            "evidence_ref": "frame_r2",
                        },
                    ],
                },
                {
                    "row_id": "row_02",
                    "coverage": 0.9,
                    "capacity_m": 0.6,
                    "spines": [
                        {
                            "slot": 0,
                            "x": 0.16,
                            "t": 9.0,
                            "isbn": "9780061122415",
                            "appearance": "red-spine",
                            "evidence_ref": "frame_r3",
                        }
                    ],
                },
                {"row_id": "row_03", "coverage": 0.12, "capacity_m": 0.6, "spines": []},
            ],
        },
        {
            "pass_id": "pass_b_walkaround",
            "room_id": "library",
            "shelf_id": "shelf_01",
            "face_id": "shelf_01.face_B",
            "face_normal": [0, 0, -1],
            "label": "Shelf 1 B",
            "min_x": 0.4,
            "min_z": -0.2,
            "max_x": 1.6,
            "max_z": 0.2,
            "capacity_m": 0.6,
            "evidence_bytes": 220_000,
            "t": 20.0,
            "rows": [
                {
                    "row_id": "row_01",
                    "coverage": 0.88,
                    "capacity_m": 0.6,
                    "spines": [
                        {
                            "slot": 0,
                            "x": 0.12,
                            "t": 20.0,
                            "isbn": "9780143127550",
                            "appearance": "other-side",
                            "evidence_ref": "frame_b1",
                        }
                    ],
                }
            ],
        },
        {
            "pass_id": "moved_book",
            "room_id": "library",
            "shelf_id": "shelf_01",
            "face_id": "shelf_01.face_A",
            "face_normal": [0, 0, 1],
            "label": "Shelf 1 A",
            "min_x": 0.4,
            "min_z": 0.3,
            "max_x": 1.6,
            "max_z": 0.7,
            "t": 40.0,
            "rows": [
                {
                    "row_id": "row_02",
                    "coverage": 0.9,
                    "capacity_m": 0.6,
                    "spines": [
                        {
                            "slot": 4,
                            "x": 0.82,
                            "t": 40.0,
                            "isbn": "9780061122415",
                            "appearance": "red-spine",
                            "evidence_ref": "frame_moved",
                        }
                    ],
                }
            ],
        },
    ]
}


def test_labeled_shelf_count_interval_no_double_and_partial_uncovered() -> None:
    result = count_labeled_shelf(LABELED_SHELF)
    face_a_copies = [item for item in result.asset_copies if item["face_id"] == "shelf_01.face_A"]
    face_b_copies = [item for item in result.asset_copies if item["face_id"] == "shelf_01.face_B"]
    row1 = [item for item in face_a_copies if item["row_id"] == "row_01"]
    assert len(row1) == 2
    assert {item["slot"] for item in row1} == {0, 1}
    assert all(item["isbn"] == "9780143127550" for item in row1)
    assert len(face_b_copies) == 1
    assert any(item["possibly_moved"] for item in result.asset_copies)
    row3 = next(
        row
        for face in result.faces
        if face["shelf_face_id"] == "shelf_01.face_A"
        for row in face["rows"]
        if row["row_id"] == "row_03"
    )
    assert row3["status"] == "partial"
    assert row3["copy_count"] == 0
    face_a = next(item for item in result.faces if item["shelf_face_id"] == "shelf_01.face_A")
    assert face_a["copy_count"]["interval"] is not None
    assert face_a["copy_count"]["status"] == "partial"
    assert "shelf_01.face_A/row_03" in result.recapture
    assert result.status == "partial"


def test_vision_job_is_idempotent_and_inventory_exposes_data_size() -> None:
    app, _, store = isolated_app()
    labeled = json.dumps(LABELED_SHELF).encode("utf-8")
    uploads = [
        ("roomplan/processed/structure.json", "application/json", STRUCTURE),
        ("roomplan/model.usdz", "model/vnd.usdz+zip", b"fixture-usdz"),
        ("shelf_scans/labeled.json", "application/json", labeled),
    ]
    with TestClient(app) as client:
        survey_id = create_survey(client)["survey_id"]
        for path, mime_type, content in uploads:
            response = client.post(
                f"/v1/surveys/{survey_id}/uploads",
                params={"path": path},
                headers={**headers(f"upload-{path}"), "Content-Type": mime_type},
                content=content,
            )
            assert response.status_code == 200
        payload = manifest(survey_id, uploads)
        payload["capture_modes"] = ["room", "shelf"]
        first = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-shelf"),
            json=payload,
        )
        assert first.status_code == 200
        inventory = client.get(f"/v1/surveys/{survey_id}/inventory")
        shelves = client.get(f"/v1/surveys/{survey_id}/shelves")
        assert inventory.status_code == 200
        assert shelves.status_code == 200
        body = inventory.json()
        assert body["status"] == "partial"
        row1 = [
            item
            for item in body["asset_copies"]
            if item["face_id"] == "shelf_01.face_A" and item["row_id"] == "row_01"
        ]
        assert len(row1) == 2
        assert any(item["possibly_moved"] for item in body["asset_copies"])
        assert shelves.json()["copy_count"] == len(body["asset_copies"])
        svg = store.get(f"{survey_id}/derived/plan.svg").decode("utf-8")
        assert "data-shelf=" in svg
        assert "copies" in svg
        second = client.post(
            f"/v1/surveys/{survey_id}/seal",
            headers=headers("seal-shelf"),
            json=payload,
        )
        assert second.status_code == 200
        again = client.get(f"/v1/surveys/{survey_id}/inventory").json()
        assert [item["asset_copy_id"] for item in again["asset_copies"]] == [
            item["asset_copy_id"] for item in body["asset_copies"]
        ]
