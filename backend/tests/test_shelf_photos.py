import json
from uuid import uuid4

import fakeredis

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now
from backend.app.workflows.pricing import PricingWorker
from backend.app.workflows.shelf_photos import ShelfPhotoWorker, interpret_shelf_reading


def test_interpret_keeps_the_model_count_when_titles_are_missing() -> None:
    reading = interpret_shelf_reading(
        {
            "estimated_book_count": 12,
            "unidentified_count": 0,
            "currency": "inr",
            "shelf_total": None,
            "calculation_note": "No titles were readable.",
            "books": [
                {
                    "title": " ",
                    "author": None,
                    "count": 4,
                    "unit_price": 10,
                    "listing_url": "nope",
                }
            ],
        },
        currency="INR",
    )
    assert reading["books"] == []
    assert reading["estimated_book_count"] == 12
    assert reading["unidentified_count"] == 12
    assert reading["line_total"] == 0


def test_shelf_photo_worker_prices_named_copies_and_counts_the_rest() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True),
        MemoryObjectStore(),
        key_prefix="test:shelf-photos",
    )
    survey_id = uuid4()
    repository.create(
        SurveyRecord(
            survey_id=survey_id,
            display_name="Photo shelves",
            geography=SurveyGeography(
                country_code="IN", city="Bareilly", currency="INR",
                market="en-IN", source="manual",
            ),
            status="geometry",
            created_at=utc_now(),
            sealed_at=utc_now(),
            package_hash="a" * 64,
        )
    )
    repository.put_bytes(
        survey_id,
        "shelf_photos/manifest.json",
        json.dumps(
            {
                "schema_version": "shelf-photos-v1",
                "shelves": [
                    {
                        "shelf_id": "shelf_01",
                        "label": "Shelf 1",
                        "photos": ["shelf_photos/shelf_01/photo_01.jpg"],
                    }
                ],
            }
        ).encode(),
        "application/json",
    )
    repository.put_bytes(
        survey_id, "shelf_photos/shelf_01/photo_01.jpg", b"jpeg-bytes", "image/jpeg",
    )
    calls = {"n": 0}

    def reader(jpeg, *, geography, shelf_label):
        calls["n"] += 1
        assert jpeg == b"jpeg-bytes"
        assert geography["currency"] == "INR"
        assert shelf_label == "Shelf 1"
        return {
            "estimated_book_count": 3,
            "unidentified_count": 1,
            "currency": "INR",
            "shelf_total": 798,
            "calculation_note": "2 x 399",
            "books": [
                {
                    "title": "The Example Book",
                    "author": "A. Writer",
                    "count": 2,
                    "unit_price": 399,
                    "listing_url": "https://shop.example/book",
                }
            ],
        }

    worker = ShelfPhotoWorker(reader)
    inventory = worker.process(repository, survey_id)
    assert inventory is not None
    assert len(inventory.asset_copies) == 3
    assert inventory.shelf_face_data_sizes[0].copy_count.value == 3
    overview = PricingWorker().overview(repository, survey_id)
    priced = [row for row in overview["copies"] if row["valuation_status"] == "quoted"]
    pending = [row for row in overview["copies"] if row["title"].startswith("Unidentified")]
    assert len(priced) == 2
    assert {row["title"] for row in priced} == {"The Example Book"}
    assert overview["contents"]["central"] == 798
    assert overview["contents"]["currency"] == "INR"
    assert "language-model" in overview["contents"]["note"]
    assert len(pending) == 1
    assert pending[0]["valuation"] is None
    stored = repository.get_json(survey_id, "shelf_llm")
    assert stored["line_total"] == 798
    assert stored["model_total"] == 798
    assert stored["estimated_book_count"] == 3
    worker.process(repository, survey_id)
    assert calls["n"] == 1
