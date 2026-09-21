import json
from uuid import uuid4

import fakeredis
import pytest

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.rl.offline import (
    IndependentLabel,
    IndependentOutcome,
    append_label,
    get_policy,
    reward_from_outcome,
    shadow_policy,
    train_offline_policy,
)
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
