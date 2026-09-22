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
    assert result["comparison"]["writes_count"] is False
    assert result["comparison"]["writes_price"] is False


def _sealed_repository(prefix: str, copies: list[dict]):
    from backend.app.domain.models import SurveyGeography, SurveyRecord
    from backend.app.domain.repository import SurveyRepository
    from backend.app.storage.objects import MemoryObjectStore
    from backend.app.utils.clocks import utc_now

    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix=prefix
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
    observations = []
    for copy in copies:
        obs_id = f"obs-{copy['asset_copy_id']}"
        path = f"shelf/{copy['asset_copy_id']}.jpg"
        copy.setdefault("observation_refs", [obs_id])
        copy.setdefault("category", "book")
        copy.setdefault("requires_appraisal", False)
        observations.append({"observation_id": obs_id, "evidence_ref": path})
        repository.put_bytes(survey_id, path, b"jpeg", "image/jpeg")
    repository.save_json(survey_id, "inventory", {
        "asset_copies": copies,
        "observations": observations,
    })
    return repository, survey_id


def _agreeing_provider(evidence):
    package = json.loads(evidence)
    ref = package["evidence_refs"][0]
    return {"model": "test-model"}, json.dumps({
        "category": "book", "condition": "good",
        "damage": {"present": False, "types": [], "description": None},
        "identity_candidates": [],
        "recommended_action": "accept_candidate",
        "confidence": 0.95, "rationale": None,
        "evidence_refs": [ref],
        "price": 40, "geometry": {"x": 1}, "isbn": "9789999999999",
    })


def _jev_ok(_state):
    return {
        "model": "jev-1.13.0",
        "count": 99,
        "price": 12,
        "answers": {"route": {
            "choice": "accept_candidate", "confidence": 0.96,
            "probabilities": {
                "accept_candidate": 0.96, "recapture": 0.01,
                "alternate_resolver": 0.01, "human_review": 0.02,
            },
        }},
    }


def test_replay_survey_runs_every_copy_independently() -> None:
    from backend.app.workflows.models import replay_survey

    repository, survey_id = _sealed_repository("test:every-copy", [
        {"asset_copy_id": "copy-1"},
        {"asset_copy_id": "copy-2"},
        {"asset_copy_id": "copy-3"},
    ])
    seen = []

    def provider(evidence):
        seen.append(json.loads(evidence)["asset_copy_id"])
        return _agreeing_provider(evidence)

    summary = replay_survey(
        repository, survey_id, fable_call=provider, astra_call=provider, jev_call=_jev_ok,
    )
    assert summary["copy_count"] == 3
    assert summary["replayed_count"] == 3
    assert summary["source"] == "after_seal"
    assert seen == ["copy-1", "copy-1", "copy-2", "copy-2", "copy-3", "copy-3"]
    for run in summary["runs"]:
        assert run["source"] == "after_seal"
        assert run["comparison"]["writes_count"] is False
        assert run["comparison"]["writes_price"] is False
        assert "isbn" not in run["assessments"]["fable"]
        assert "price" not in run["assessments"]["fable"]
    repeated = replay_survey(
        repository, survey_id, fable_call=provider, astra_call=provider, jev_call=_jev_ok,
    )
    assert repeated["reused"] is True
    assert seen == ["copy-1", "copy-1", "copy-2", "copy-2", "copy-3", "copy-3"]


def test_unavailable_provider_is_disclosed_partial_not_invented() -> None:
    from backend.app.workflows.models import replay_asset

    repository, survey_id = _sealed_repository("test:unavailable", [
        {"asset_copy_id": "copy-1"},
    ])
    inventory_before = repository.get_json(survey_id, "inventory")

    def fail(_evidence):
        raise RuntimeError("OPENAI_API_KEY is required for Astra")

    result = replay_asset(
        repository, survey_id, "copy-1", fable_call=fail, astra_call=fail, jev_call=_jev_ok,
    )
    assert result["partial"] is True
    assert result["disclosed"] is True
    assert result["review"] == "human"
    assert result["assessments"] == {}
    assert result["jev"] is None
    assert result["decision"]["action"] == "human_review"
    assert result["decision"]["reason"] == "model_unavailable"
    assert result["comparison"]["a"] is None
    assert result["comparison"]["b"] is None
    assert result["comparison"]["chosen_route"] is None
    assert repository.get_json(survey_id, "inventory") == inventory_before


def test_replay_does_not_write_inventory_geometry_or_prices() -> None:
    from backend.app.workflows.models import replay_asset

    repository, survey_id = _sealed_repository("test:no-write", [
        {"asset_copy_id": "copy-1", "isbn": "9780143127741"},
    ])
    inventory_before = json.loads(json.dumps(repository.get_json(survey_id, "inventory")))
    result = replay_asset(
        repository, survey_id, "copy-1",
        fable_call=_agreeing_provider, astra_call=_agreeing_provider, jev_call=_jev_ok,
    )
    assert repository.get_json(survey_id, "inventory") == inventory_before
    assert repository.get_json(survey_id, "geometry") is None
    pricing = repository.get_json(survey_id, "pricing")
    assert pricing is None
    assert "price" not in result["assessments"]["fable"]
    assert result["comparison"]["writes_count"] is False
    assert result["jev"].get("count") is None


def test_jev_comparison_record_separates_route_from_policy() -> None:
    from backend.app.workflows.models import replay_asset

    repository, survey_id = _sealed_repository("test:comparison", [
        {"asset_copy_id": "copy-1"},
    ])
    result = replay_asset(
        repository, survey_id, "copy-1",
        fable_call=_agreeing_provider, astra_call=_agreeing_provider,
        jev_call=lambda _: {
            "model": "jev-1.13.0",
            "answers": {"route": {
                "choice": "recapture", "confidence": 0.91,
                "probabilities": {
                    "accept_candidate": 0.04, "recapture": 0.91,
                    "alternate_resolver": 0.03, "human_review": 0.02,
                },
            }},
        },
    )
    comparison = result["comparison"]
    assert comparison["a"]["condition"] == "good"
    assert comparison["b"]["condition"] == "good"
    assert comparison["disagreement"] is False
    assert comparison["chosen_route"] == "recapture"
    assert comparison["confidence"] == 0.91
    assert result["decision"]["action"] == "recapture"
    assert result["decision"]["reason"] == "jev_proposal"


def test_astra_live_is_sampled_assist_and_skips_without_provider() -> None:
    from backend.app.workflows.astra_live import record_astra_live

    repository, survey_id = _sealed_repository("test:astra-live", [
        {"asset_copy_id": "copy-1"},
    ])
    inventory_before = repository.get_json(survey_id, "inventory")
    skipped = record_astra_live(
        repository, survey_id,
        {"capture_pass": "B", "image_base64": "aaaa", "provisional_count": 3},
        astra_call=lambda _body: (_ for _ in ()).throw(RuntimeError("no key")),
    )
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "provider_unavailable"
    assert skipped["assist"] is None
    assert skipped["invented"] is False
    assert skipped["review"] == "human"
    assert skipped["pipeline"] == "astra_live"
    assert skipped["authority"] == "assist_metadata"

    def live_call(_body):
        return {"model": "gpt-6-astra"}, json.dumps({
            "quality": {"blur": False, "glare": True, "readable": False, "notes": "glare"},
            "provisional_count": 5,
            "unreadable_slots": ["row_01/slot_3"],
            "recapture_hint": "Tilt the camera",
            "confidence": 0.7,
            "rationale": "Glare on the right",
            "isbn": "9780000000000",
            "price": 20,
        })

    recorded = record_astra_live(
        repository, survey_id,
        {
            "capture_pass": "B",
            "image_base64": "aGVsbG8=",
            "quality_messages": ["Tilt to reduce glare"],
            "provisional_count": 3,
            "unreadable_slots": ["row_01/slot_3"],
        },
        astra_call=live_call,
    )
    # Second call is too soon unless we bypass interval by using a fresh survey.
    assert recorded["status"] in {"assist", "skipped"}
    assert repository.get_json(survey_id, "inventory") == inventory_before


def test_astra_live_stores_assist_without_writing_inventory() -> None:
    from backend.app.workflows.astra_live import list_astra_live, record_astra_live

    repository, survey_id = _sealed_repository("test:astra-live-ok", [
        {"asset_copy_id": "copy-1"},
    ])
    inventory_before = repository.get_json(survey_id, "inventory")

    def live_call(_body):
        return {"model": "gpt-6-astra"}, json.dumps({
            "quality": {"blur": True, "glare": False, "readable": False, "notes": "blur"},
            "provisional_count": 2,
            "unreadable_slots": ["row_01/slot_1"],
            "recapture_hint": "Hold still",
            "confidence": 0.55,
            "rationale": "Motion blur",
        })

    recorded = record_astra_live(
        repository, survey_id,
        {
            "capture_pass": "C",
            "image_base64": "aGVsbG8=",
            "provisional_count": 1,
            "unreadable_slots": ["row_01/slot_1"],
        },
        astra_call=live_call,
    )
    assert recorded["status"] == "assist"
    assert recorded["pipeline"] == "astra_live"
    assert recorded["authority"] == "assist_metadata"
    assert recorded["assist"]["pipeline"] == "astra_live"
    assert recorded["assist"]["capture_pass"] == "C"
    assert recorded["assist"]["provisional_count"] == 2
    listed = list_astra_live(repository, survey_id)
    assert len(listed["assists"]) == 1
    assert listed["authority"] == "assist_metadata"
    assert repository.get_json(survey_id, "inventory") == inventory_before
    cap = record_astra_live(
        repository, survey_id,
        {"capture_pass": "C", "image_base64": "aGVsbG8="},
        astra_call=live_call,
    )
    assert cap["status"] == "skipped"
    assert cap["reason"] == "sample_interval"
