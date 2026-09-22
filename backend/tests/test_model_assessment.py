from uuid import uuid4

import pytest

from backend.app.providers.models.astra import parse_astra_live_response, parse_astra_response
from backend.app.providers.models.fable import parse_fable_response
from backend.app.providers.models.jev import parse_jev_response
from backend.app.providers.models.normalization import ProviderResponseError
from backend.app.providers.models.remote import (
    configured_astra_model,
    configured_fable_model,
    configured_jev_model,
    extract_json_object,
)


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


def test_extra_provider_fields_are_dropped() -> None:
    response = payload("fable")
    response["invented_price"] = 999
    response["geometry"] = {"x": 1}
    response["merge"] = True
    parsed = parse_fable_response(response)
    dumped = parsed.model_dump(mode="json")
    assert "invented_price" not in dumped
    assert "geometry" not in dumped
    assert "merge" not in dumped
    assert dumped["category"] == "book"


def test_configured_model_ids_have_documented_defaults(monkeypatch) -> None:
    monkeypatch.delenv("FABLE_MODEL", raising=False)
    monkeypatch.delenv("ASTRA_MODEL", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    assert configured_fable_model() == "claude-fable-5-1"
    assert configured_astra_model() == "gpt-6-astra"
    assert configured_jev_model() == "jev-latest"


def test_jev_response_cannot_carry_count_or_price() -> None:
    parsed = parse_jev_response({
        "model": "jev-latest",
        "count": 12,
        "price": 9.99,
        "answers": {"route": {
            "choice": "human_review", "confidence": 0.9,
            "probabilities": {
                "accept_candidate": 0.02, "recapture": 0.04,
                "alternate_resolver": 0.04, "human_review": 0.9,
            },
        }},
    })
    assert parsed["choice"] == "human_review"
    assert "count" not in parsed
    assert "price" not in parsed


def test_astra_live_is_assist_metadata_not_pipeline_b() -> None:
    parsed = parse_astra_live_response({
        "schema_version": "1.0.0",
        "assist_id": str(uuid4()),
        "survey_id": str(uuid4()),
        "pipeline": "astra_live",
        "provider": "openai",
        "model": "gpt-6-astra",
        "authority": "assist_metadata",
        "capture_pass": "B",
        "quality": {"blur": True, "glare": False, "readable": False, "notes": "glare"},
        "provisional_count": 4,
        "unreadable_slots": ["row_01/slot_2"],
        "recapture_hint": "Move closer on row 1",
        "confidence": 0.6,
        "rationale": "Spines are unread",
        "price": 12.5,
        "isbn": "9780000000000",
        "geometry": {"x": 1},
    })
    dumped = parsed.model_dump(mode="json")
    assert dumped["pipeline"] == "astra_live"
    assert dumped["authority"] == "assist_metadata"
    assert "price" not in dumped
    assert "isbn" not in dumped
    assert "geometry" not in dumped


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
