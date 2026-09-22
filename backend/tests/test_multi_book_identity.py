import json
from uuid import uuid4

import fakeredis

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now
from backend.app.workflows.stage3 import Stage3Worker


class FixedCatalog:
    def resolve(self, _repository, _survey_id, _isbn, *, title):
        return {
            "status": "candidate", "title": title or "Example",
            "authors": ["Writer"], "source": "fixture", "work_refs": ["work-1"],
        }


def test_same_isbn_set_and_volume_keep_two_copies_and_editions() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:scope"
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Two copies",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="geometry", created_at=utc_now(), sealed_at=utc_now(), package_hash="a" * 64,
    ))
    repository.save_json(survey_id, "inventory", {"asset_copies": [
        {"asset_copy_id": "copy-1", "category": "book", "face_id": "face_A",
         "row_id": "row_01", "slot": 0, "observation_refs": ["obs-1"]},
        {"asset_copy_id": "copy-2", "category": "book", "face_id": "face_A",
         "row_id": "row_01", "slot": 1, "observation_refs": ["obs-2"]},
    ]})
    repository.put_bytes(survey_id, "exceptions/pass-c.json", json.dumps({
        "scans": [
            {"id": "scan-1", "kind": "barcode", "face_id": "face_A",
             "row_id": "row_01", "slot": 0, "barcode": "9780143127550",
             "scope": "set", "format": "hardcover", "evidence_ref": "closeups/set.jpg"},
            {"id": "scan-2", "kind": "barcode", "face_id": "face_A",
             "row_id": "row_01", "slot": 1, "barcode": "9780143127550",
             "scope": "volume", "format": "paperback", "evidence_ref": "closeups/volume.jpg"},
        ], "notes": [], "focus_events": [],
    }).encode(), "application/json")
    result = Stage3Worker(catalog=FixedCatalog()).process(repository, survey_id)
    assert {asset["asset_copy_id"] for asset in result["assets"]} == {"copy-1", "copy-2"}
    assert {item["identifier_kind"] for item in result["identities"]} == {
        "set_isbn", "volume_isbn",
    }
    assert len(result["book_editions"]) == 2
    assert len({item["book_edition_ref"] for item in result["assets"]}) == 2
