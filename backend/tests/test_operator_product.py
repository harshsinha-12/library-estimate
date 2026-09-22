from uuid import uuid4

import pytest

from backend.app.workflows.operator_failures import failure_actions
from backend.app.workflows.stage3 import correct_book_identity


def test_action_table_does_not_call_an_active_upload_an_interruption() -> None:
    actions = failure_actions(
        {"status": "uploading", "geography": {"source": "manual"}},
        {
            "rows": [{"recapture": True}],
            "copies": [
                {"asset_copy_id": "a", "category": "book", "isbn": "9780132350884"},
                {"asset_copy_id": "b", "category": "book", "isbn": "9780132350884"},
            ],
        },
        {"queue": []},
        [],
    )
    by_code = {item["code"]: item["status"] for item in actions}
    assert by_code["shelf_coverage"] == "action_required"
    assert by_code["same_isbn_copies"] == "recorded"
    assert by_code["location_fallback"] == "recorded"
    assert by_code["upload_interrupted"] == "not_observed"
    assert len(actions) == 22


def test_identity_correction_keeps_typed_isbn_out_of_price_query(monkeypatch) -> None:
    import backend.app.workflows.stage3 as stage3

    class Repository:
        def __init__(self) -> None:
            self.result = {
                "assets": [
                    {"asset_copy_id": "copy-a", "category": "book"},
                    {"asset_copy_id": "copy-b", "category": "book"},
                ],
                "identities": [{"asset_copy_id": "copy-b", "title": "Other"}],
                "queue": [{
                    "id": "task-a", "asset_copy_id": "copy-a",
                    "kind": "unread_spine", "status": "open",
                }],
            }

        def get_json(self, _survey_id, _key):
            return self.result

        def save_json(self, _survey_id, _key, value):
            self.result = value

    repository = Repository()
    decisions = []
    monkeypatch.setattr(stage3, "record_decision", lambda *args, **kwargs: decisions.append(kwargs))
    identity = correct_book_identity(
        repository,
        uuid4(),
        {
            "asset_copy_id": "copy-a",
            "title": "Visible title",
            "isbn": "9780132350884",
            "scope": "volume",
            "format": "paperback",
            "reason": "Spine OCR was wrong",
        },
    )
    assert identity["valid"] is True
    assert identity["usable_for_isbn_price_query"] is False
    assert repository.result["identities"][0]["asset_copy_id"] == "copy-b"
    assert repository.result["corrections"][0]["previous"] is None
    assert repository.result["queue"][0]["status"] == "resolved_by_operator_correction"
    assert repository.result["queue"][1]["kind"] == "catalog_review"
    assert decisions[0]["state"]["asset_copy_id"] == "copy-a"
    with pytest.raises(ValueError, match="checksum"):
        correct_book_identity(
            repository,
            uuid4(),
            {
                "asset_copy_id": "copy-a",
                "title": "Visible title",
                "isbn": "9780132350885",
                "reason": "Spine OCR was wrong",
            },
        )
