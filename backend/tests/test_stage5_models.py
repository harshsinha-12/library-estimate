import json
from pathlib import Path
from uuid import uuid4

import fakeredis
from jsonschema import validate

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now
from backend.app.workflows.models import _route, replay_asset


def test_policy_vetoes_precede_model_acceptance() -> None:
    assessment = {
        "category": "book", "condition": "good", "damage": {"present": False},
        "confidence": 0.99, "recommended_action": "accept_candidate",
    }
    jev = {"confidence": 0.99, "choice": "accept_candidate"}
    cases = [
        ({"category": "book", "requires_appraisal": True}, "high_value_veto"),
        ({"category": "ebook"}, "ebook_physical_veto"),
        ({"category": "book", "merge_basis": "isbn_only"}, "isbn_only_merge_veto"),
    ]
    for asset, reason in cases:
        decision = _route(assessment, assessment, asset, jev)
        assert decision["action"] == "human_review"
        assert decision["reason"] == reason


def test_replay_sends_identical_sealed_evidence_and_routes_disagreement() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:model"
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Model test",
        geography=SurveyGeography(
            country_code="IN", region="Karnataka", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="geometry", created_at=utc_now(), sealed_at=utc_now(), package_hash="a" * 64,
    ))
    repository.save_json(survey_id, "inventory", {
        "asset_copies": [{"asset_copy_id": "copy-1", "category": "book",
                          "observation_refs": ["obs-1"], "requires_appraisal": False}],
        "observations": [{"observation_id": "obs-1", "evidence_ref": "shelf/frame.jpg"}],
    })
    inputs = []

    def provider(pipeline, condition):
        def call(evidence):
            inputs.append(evidence)
            return {"model": "test-model"}, json.dumps({
                "category": "book", "condition": condition,
                "damage": {"present": False, "types": [], "description": None},
                "identity_candidates": [], "recommended_action": "accept_candidate",
                "confidence": 0.95, "rationale": None,
                "evidence_refs": ["shelf/frame.jpg"],
            })
        return call

    result = replay_asset(
        repository, survey_id, "copy-1", fable_call=provider("fable", "good"),
        astra_call=provider("astra_replay", "worn"),
        jev_call=lambda _: {
            "model": "jev-1.13.0",
            "answers": {"route": {
                "choice": "accept_candidate", "confidence": 0.96,
                "probabilities": {
                    "accept_candidate": 0.96, "recapture": 0.01,
                    "alternate_resolver": 0.01, "human_review": 0.02,
                },
            }},
        },
    )
    assert inputs[0] == inputs[1]
    assert result["decision"]["action"] == "human_review"
    assert result["decision"]["reason"] == "model_disagreement"
    assert result["jev"]["choice"] == "accept_candidate"
    assert repository.get_bytes(survey_id, result["evidence_path"]) == inputs[0]
    assert result["assessments"]["fable"]["evidence_package_hash"] == (
        result["assessments"]["astra_replay"]["evidence_package_hash"]
    )
    transition = json.loads(repository.redis.lindex(
        f"{repository.key_prefix}:survey:{survey_id}:rl_transitions", 0
    ))
    schema_path = Path(__file__).resolve().parents[2] / "schemas/rl-transition.schema.json"
    schema = json.loads(schema_path.read_text())
    validate(transition, schema)
    assert transition["reward"] is None
    assert transition["action"] == "human_review"


def test_replay_names_the_extracted_title_in_a_crowded_frame() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:model"
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Model test",
        geography=SurveyGeography(
            country_code="IN", region="Karnataka", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="geometry", created_at=utc_now(), sealed_at=utc_now(), package_hash="a" * 64,
    ))
    repository.save_json(survey_id, "inventory", {
        "asset_copies": [{
            "asset_copy_id": "copy-1", "category": "book",
            "observation_refs": ["obs-1"], "requires_appraisal": False,
            "row_id": "row_01", "slot": 0,
        }],
        "observations": [
            {"observation_id": "obs-1", "evidence_ref": "roomplan/raw/frames/0071.jpg"}
        ],
    })
    repository.save_json(survey_id, "stage3", {
        "identities": [{"asset_copy_id": "copy-1", "title": "Python Data Science Handbook"}],
        "assets": [{"asset_copy_id": "copy-1", "category": "book",
                    "label": "Python Data Science Handbook"}],
    })
    repository.put_bytes(survey_id, "shelf_scans/crops/row_01_slot0.jpg", b"crop", "image/jpeg")
    repository.put_bytes(survey_id, "roomplan/raw/frames/0071.jpg", b"frame", "image/jpeg")
    packages = []

    def provider(evidence):
        packages.append(json.loads(evidence))
        return {"model": "test-model"}, json.dumps({
            "category": "book", "condition": "good",
            "damage": {"present": False, "types": [], "description": None},
            "identity_candidates": [{
                "label": "Python Data Science Handbook", "confidence": 0.9,
                "evidence_refs": ["shelf_scans/crops/row_01_slot0.jpg"],
            }],
            "recommended_action": "accept_candidate", "confidence": 0.9,
            "evidence_refs": ["shelf_scans/crops/row_01_slot0.jpg"],
        })

    replay_asset(
        repository, survey_id, "copy-1", fable_call=provider, astra_call=provider,
        jev_call=lambda _: {
            "model": "jev-1.13.0",
            "answers": {"route": {
                "choice": "accept_candidate", "confidence": 0.96,
                "probabilities": {
                    "accept_candidate": 0.96, "recapture": 0.01,
                    "alternate_resolver": 0.01, "human_review": 0.02,
                },
            }},
        },
    )
    package = packages[0]
    assert package["target_identity"]["title"] == "Python Data Science Handbook"
    assert "Python Data Science Handbook" in package["task"]
    assert package["evidence_refs"][0] == "shelf_scans/crops/row_01_slot0.jpg"
    assert package["media"][0]["evidence_ref"] == "shelf_scans/crops/row_01_slot0.jpg"
    audit = json.loads(repository.redis.lindex(
        f"{repository.key_prefix}:survey:{survey_id}:auto_accept_audit", 0
    ))
    assert audit["asset_copy_id"] == "copy-1"
    assert audit["policy_reason"] == "agreement"
    assert audit["evidence_hash"]
    assert audit["overturn_by"] == "human_reviewer"


def test_replay_attaches_jpeg_over_four_hundred_kb() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:model"
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Model test",
        geography=SurveyGeography(
            country_code="IN", region="Karnataka", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="geometry", created_at=utc_now(), sealed_at=utc_now(), package_hash="a" * 64,
    ))
    repository.save_json(survey_id, "inventory", {
        "asset_copies": [{"asset_copy_id": "copy-1", "category": "book",
                          "observation_refs": ["obs-1"], "requires_appraisal": False}],
        "observations": [{"observation_id": "obs-1", "evidence_ref": "shelf/frame.jpg"}],
    })
    repository.put_bytes(survey_id, "shelf/frame.jpg", b"x" * 558_260, "image/jpeg")

    def provider(evidence):
        package = json.loads(evidence)
        assert package["media"]
        assert package["media"][0]["evidence_ref"] == "shelf/frame.jpg"
        return {"model": "test-model"}, json.dumps({
            "category": "book", "condition": "good",
            "damage": [], "identity_candidates": [],
            "recommended_action": "human_review", "confidence": 0.4,
            "evidence_refs": ["shelf/frame.jpg"],
        })

    result = replay_asset(
        repository, survey_id, "copy-1", fable_call=provider, astra_call=provider,
        jev_call=lambda _: {
            "model": "jev-1.13.0",
            "answers": {"route": {
                "choice": "human_review", "confidence": 0.9,
                "probabilities": {
                    "accept_candidate": 0.02, "recapture": 0.04,
                    "alternate_resolver": 0.04, "human_review": 0.9,
                },
            }},
        },
    )
    assert "fable" in result["assessments"]
    assert "astra_replay" in result["assessments"]
    assert result["assessments"]["fable"]["damage"]["types"] == []
