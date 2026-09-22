import json
from hashlib import sha256
from uuid import uuid4

import fakeredis
import pytest

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.rl.offline import (
    IndependentLabel,
    IndependentOutcome,
    append_label,
    freeze_gold_set,
    get_policy,
    reward_from_outcome,
    shadow_policy,
    train_offline_policy,
)
from backend.app.rl.transitions import record_decision, record_successor_state
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now


def _survey(repository):
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Labeled fixture",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="geometry", created_at=utc_now(), sealed_at=utc_now(), package_hash="a" * 64,
    ))
    return survey_id


def _add(repository, survey_id, *, action, index):
    transition_id = uuid4()
    transition = {
        "transition_id": str(transition_id), "survey_id": str(survey_id),
        "action": action, "action_source": "policy", "state": {
            "category": "book" if index % 2 else "cup",
            "disagreement": bool(index % 2),
            "logging_propensity": 0.5,
        },
        "fable": {"confidence": 0.8}, "astra": {"confidence": 0.7},
    }
    repository.redis.rpush(
        f"{repository.key_prefix}:survey:{survey_id}:rl_transitions",
        json.dumps(transition),
    )
    return append_label(repository, IndependentLabel(
        survey_id=survey_id, transition_id=transition_id, labeled_by="fixture",
        truth={"condition": "good" if index % 2 else "worn",
               "quality": "usable" if index % 2 else "blurred"},
        outcome=IndependentOutcome(
            copy_relation_correct=action == "accept",
            recapture_recovered_row=action == "recapture",
        ),
    ))


def test_independent_reward_and_offline_shadow_holdout() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:rl"
    )
    train_id, holdout_id = _survey(repository), _survey(repository)
    labels = [
        _add(repository, train_id, action="accept" if index < 6 else "recapture", index=index)
        for index in range(12)
    ]
    for index in range(4):
        _add(repository, holdout_id, action="recapture", index=index)
    assert reward_from_outcome(IndependentOutcome(false_merge=True, isbn_correct=True)) == -3
    assert labels[0]["reward"] == 1
    policy = train_offline_policy(
        repository, train_survey_ids=[train_id], holdout_survey_ids=[holdout_id]
    )
    assert policy["n_train"] == 12
    assert policy["action_support"] == {"accept": 6, "recapture": 6}
    assert policy["specialists"]["condition"]["n"] == 12
    assert policy["approved_at"] is None
    assert get_policy(repository, policy["policy_id"])["labels_sha256"] == policy["labels_sha256"]
    shadow = shadow_policy(repository, policy["policy_id"])
    assert shadow["holdout_denominator"] == 4
    assert shadow["matched_denominator"] == 4
    assert shadow["ips_warning"]
    assert shadow["specialist_scores"]["condition"]["labeled_denominator"] == 4
    assert policy["technique"].startswith("offline contextual bandit")
    with pytest.raises(ValueError, match="disjoint"):
        train_offline_policy(
            repository, train_survey_ids=[train_id], holdout_survey_ids=[train_id]
        )


def test_duplicate_label_cannot_rewrite_independent_truth() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:rl2"
    )
    survey_id = _survey(repository)
    label = _add(repository, survey_id, action="human_review", index=1)
    with pytest.raises(ValueError, match="already"):
        append_label(repository, IndependentLabel(
            survey_id=survey_id, transition_id=label["transition_id"],
            labeled_by="second reviewer", outcome=IndependentOutcome(false_merge=True),
        ))


def test_recapture_has_append_only_successor_and_deterministic_log_is_not_training_support():
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:seq"
    )
    survey_id, holdout_id = _survey(repository), _survey(repository)
    transition = record_decision(
        repository, survey_id, action="recapture", action_source="policy",
        policy_id="route_v0_log_only", state={"asset_copy_id": "copy-1"},
        next_state_id="state-2",
    )
    repository.put_bytes(survey_id, "derived/recapture.jpg", b"new evidence", "image/jpeg")
    evidence_hash = sha256(b"new evidence").hexdigest()
    with pytest.raises(ValueError, match="successor"):
        record_successor_state(
            repository, survey_id, state_id="wrong",
            predecessor_transition_id=transition["transition_id"],
            evidence_ref="derived/recapture.jpg", evidence_hash=evidence_hash,
            state={"coverage": 1},
        )
    successor = record_successor_state(
        repository, survey_id, state_id="state-2",
        predecessor_transition_id=transition["transition_id"],
        evidence_ref="derived/recapture.jpg", evidence_hash=evidence_hash,
        state={"coverage": 1},
    )
    assert successor["state"]["coverage"] == 1
    with pytest.raises(ValueError, match="already recorded"):
        record_successor_state(
            repository, survey_id, state_id="state-2",
            predecessor_transition_id=transition["transition_id"],
            evidence_ref="derived/recapture.jpg", evidence_hash=evidence_hash,
            state={"coverage": 0},
        )
    append_label(repository, IndependentLabel(
        survey_id=survey_id, transition_id=transition["transition_id"], labeled_by="gold",
        outcome=IndependentOutcome(recapture_recovered_row=True),
    ))
    with pytest.raises(ValueError, match="known logging propensities"):
        train_offline_policy(
            repository, train_survey_ids=[survey_id], holdout_survey_ids=[holdout_id]
        )


def test_gold_roster_freezes_only_complete_sealed_inventory():
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:gold"
    )
    survey_id = _survey(repository)
    copy_ids = [f"copy-{index}" for index in range(50)]
    repository.save_json(survey_id, "inventory", {
        "asset_copies": [{"asset_copy_id": copy_id} for copy_id in copy_ids]
    })
    cases = {
        copy_ids[index]: [case]
        for index, case in enumerate((
            "same_isbn_two_copies", "reverse_scan", "no_isbn", "ambiguous_edition",
            "moved_book", "damaged_book", "portrait_spoken_damage", "mug",
            "appraisal_item",
        ))
    }
    with pytest.raises(ValueError, match="missing cases"):
        freeze_gold_set(
            repository, survey_id, copy_ids=copy_ids,
            case_tags={copy_ids[0]: cases[copy_ids[0]]}, frozen_by="reviewer",
        )
    frozen = freeze_gold_set(
        repository, survey_id, copy_ids=copy_ids, case_tags=cases, frozen_by="reviewer"
    )
    assert frozen["package_hash"] == "a" * 64
    with pytest.raises(ValueError, match="already frozen"):
        freeze_gold_set(
            repository, survey_id, copy_ids=copy_ids, case_tags=cases, frozen_by="reviewer"
        )


def test_specialist_reward_uses_independent_truth():
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:head"
    )
    survey_id = _survey(repository)
    transition = record_decision(
        repository, survey_id, action="use_specialist_head", action_source="policy",
        policy_id="head_v1", state={
            "asset_copy_id": "copy-1",
            "specialist_prediction": {"head": "condition", "class": "good"},
        },
    )
    with pytest.raises(ValueError, match="must match independent truth"):
        append_label(repository, IndependentLabel(
            survey_id=survey_id, transition_id=transition["transition_id"], labeled_by="gold",
            truth={"condition": "worn"},
            outcome=IndependentOutcome(specialist_correct=True),
        ))
    label = append_label(repository, IndependentLabel(
        survey_id=survey_id, transition_id=transition["transition_id"], labeled_by="gold",
        truth={"condition": "good"}, outcome=IndependentOutcome(copy_relation_correct=True),
    ))
    assert label["outcome"]["specialist_correct"] is True
    assert label["reward"] == 2.0
