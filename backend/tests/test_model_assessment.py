from uuid import uuid4

import pytest

from backend.app.providers.models.astra import parse_astra_response
from backend.app.providers.models.fable import parse_fable_response
from backend.app.providers.models.normalization import ProviderResponseError
from backend.app.providers.models.remote import extract_json_object


def payload(pipeline: str) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "assessment_id": str(uuid4()),
        "survey_id": str(uuid4()),
        "asset_copy_id": "asset_1",
        "pipeline": pipeline,
        "provider": "configured-provider",
        "model": "configured-model",
        "evidence_package_hash": "a" * 64,
        "category": "book",
        "condition": "worn",
        "damage": {"present": False, "types": [], "description": None},
        "identity_candidates": [],
        "recommended_action": "human_review",
        "confidence": 0.72,
        "rationale": "Spine evidence is incomplete.",
        "evidence_refs": ["crop_1"],
    }


def test_each_pipeline_accepts_only_its_normalized_response() -> None:
    assert parse_fable_response(payload("fable")).pipeline.value == "fable"
    assert parse_astra_response(payload("astra_replay")).pipeline.value == "astra_replay"

    with pytest.raises(ProviderResponseError):
        parse_fable_response(payload("astra_replay"))


def test_extra_provider_fields_are_rejected() -> None:
    response = payload("fable")
    response["invented_price"] = 999
    with pytest.raises(ValueError):
        parse_fable_response(response)


def test_fable_damage_array_and_category_aliases_are_coerced() -> None:
    response = payload("fable")
    response["damage"] = []
    response["category"] = "computer"
    parsed = parse_fable_response(response)
    assert parsed.damage.present is None or parsed.damage.present is False
    assert parsed.damage.types == []
    assert parsed.category == "electronics"


def test_extract_json_object_from_fenced_text() -> None:
    blob = extract_json_object(
        '```json\n{"category": "book", "confidence": 0.4}\n```'
    )
    assert blob["category"] == "book"
