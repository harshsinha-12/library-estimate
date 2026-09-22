"""Append-only independent feedback and conservative offline policy training.

Only actions observed with a known logging propensity contribute to the bandit fit.
Shadow evaluation reports support explicitly and never changes the live router.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.repository import SurveyRepository
from backend.app.utils.clocks import utc_now
from backend.app.utils.json_codec import canonical_json_bytes

ACTIONS = (
    "accept", "recapture", "alternate_resolver", "human_review",
    "use_specialist_head", "use_frontier",
)
HEADS = ("condition", "eligibility", "damage", "duplicate_features", "quality")
POLICY_VERSION = "bandit_v1"
REQUIRED_GOLD_CASES = frozenset({
    "same_isbn_two_copies", "reverse_scan", "no_isbn", "ambiguous_edition",
    "moved_book", "damaged_book", "portrait_spoken_damage", "mug", "appraisal_item",
})


def freeze_gold_set(
    repository: SurveyRepository, survey_id: UUID, *,
    copy_ids: list[str], case_tags: dict[str, list[str]], frozen_by: str,
) -> dict:
    survey = repository.get(survey_id)
    if not survey.package_hash:
        raise ValueError("gold set requires a sealed survey")
    if not 50 <= len(copy_ids) <= 100 or len(copy_ids) != len(set(copy_ids)):
        raise ValueError("gold set requires 50–100 unique physical copy IDs")
    if not frozen_by.strip():
        raise ValueError("frozen_by is required")
    inventory = repository.get_json(survey_id, "inventory") or {}
    actual = {item["asset_copy_id"] for item in inventory.get("asset_copies", [])}
    if not set(copy_ids) <= actual:
        raise ValueError("gold copy IDs must exist in the sealed inventory")
    if not set(case_tags) <= set(copy_ids):
        raise ValueError("case tags must refer to rostered copies")
    covered = {tag for tags in case_tags.values() for tag in tags}
    missing = REQUIRED_GOLD_CASES - covered
    if missing:
        raise ValueError(f"gold set missing cases: {', '.join(sorted(missing))}")
    payload = {
        "survey_id": str(survey_id), "package_hash": survey.package_hash,
        "copy_ids": sorted(copy_ids), "case_tags": case_tags,
        "frozen_by": frozen_by, "frozen_at": utc_now().isoformat(),
    }
    payload["sha256"] = sha256(canonical_json_bytes(payload)).hexdigest()
    key = f"{repository.key_prefix}:survey:{survey_id}:gold_set"
    if not repository.redis.set(key, json.dumps(payload, sort_keys=True), nx=True):
        raise ValueError("gold set is already frozen")
    return payload


class IndependentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    copy_relation_correct: bool | None = None
    false_merge: bool | None = None
    false_split: bool | None = None
    isbn_correct: bool | None = None
    isbn_wrong: bool | None = None
    missed_high_value: bool | None = None
    mug_exclusion_correct: bool | None = None
    mug_valued: bool | None = None
    ebook_as_physical: bool | None = None
    recapture_recovered_row: bool | None = None
    unnecessary_recapture: bool | None = None
    specialist_correct: bool | None = None

    @model_validator(mode="after")
    def needs_an_observation(self):
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("at least one independent outcome is required")
        if self.copy_relation_correct and (self.false_merge or self.false_split):
            raise ValueError("copy relation cannot be both correct and incorrect")
        if self.isbn_correct and self.isbn_wrong:
            raise ValueError("ISBN cannot be both correct and wrong")
        return self


class IndependentLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_id: UUID = Field(default_factory=uuid4)
    survey_id: UUID
    transition_id: UUID
    labeled_by: str = Field(min_length=1)
    observed_at: datetime = Field(default_factory=utc_now)
    truth: dict[str, str] = Field(default_factory=dict)
    outcome: IndependentOutcome


def reward_from_outcome(outcome: IndependentOutcome) -> float:
    weights = {
        "copy_relation_correct": 1.0,
        "false_merge": -5.0,
        "false_split": -2.0,
        "isbn_correct": 2.0,
        "isbn_wrong": -3.0,
        "missed_high_value": -8.0,
        "mug_exclusion_correct": 0.2,
        "mug_valued": -4.0,
        "ebook_as_physical": -4.0,
        "recapture_recovered_row": 1.5,
        "unnecessary_recapture": -0.5,
        "specialist_correct": 1.0,
    }
    reward = sum(weight for name, weight in weights.items() if getattr(outcome, name) is True)
    return round(reward, 4)


def _transitions(repository: SurveyRepository, survey_id: UUID) -> list[dict]:
    key = f"{repository.key_prefix}:survey:{survey_id}:rl_transitions"
    return [json.loads(raw) for raw in repository.redis.lrange(key, 0, -1)]


def _labels(repository: SurveyRepository, survey_id: UUID) -> list[dict]:
    key = f"{repository.key_prefix}:survey:{survey_id}:independent_labels"
    return [json.loads(raw) for raw in repository.redis.lrange(key, 0, -1)]


def append_label(repository: SurveyRepository, label: IndependentLabel) -> dict:
    repository.get(label.survey_id)
    transition = next(
        (row for row in _transitions(repository, label.survey_id)
         if row["transition_id"] == str(label.transition_id)),
        None,
    )
    if transition is None:
        raise ValueError("transition not found in survey")
    prediction = transition.get("state", {}).get("specialist_prediction")
    if prediction is not None:
        head = prediction.get("head")
        if head not in HEADS or head not in label.truth:
            raise ValueError("specialist prediction requires independent truth for its head")
        correct = prediction.get("class") == label.truth[head]
        if (
            label.outcome.specialist_correct is not None
            and label.outcome.specialist_correct != correct
        ):
            raise ValueError("specialist credit must match independent truth")
        label = label.model_copy(update={
            "outcome": label.outcome.model_copy(update={"specialist_correct": correct})
        })
    payload = label.model_dump(mode="json")
    payload["reward"] = reward_from_outcome(label.outcome)
    marker = f"{repository.key_prefix}:survey:{label.survey_id}:label:{label.transition_id}"
    if not repository.redis.set(marker, json.dumps(payload, sort_keys=True), nx=True):
        raise ValueError("transition already has an independent label")
    repository.redis.rpush(
        f"{repository.key_prefix}:survey:{label.survey_id}:independent_labels",
        json.dumps(payload, sort_keys=True),
    )
    return payload


def labeled_transitions(repository: SurveyRepository, survey_ids: list[UUID]) -> list[dict]:
    rows = []
    for survey_id in survey_ids:
        by_transition = {row["transition_id"]: row for row in _labels(repository, survey_id)}
        for transition in _transitions(repository, survey_id):
            label = by_transition.get(transition["transition_id"])
            if label is not None:
                rows.append({"transition": transition, "label": label})
    return rows


def _features(state: dict, transition: dict) -> dict[str, float]:
    category = str(state.get("category") or "unknown")
    fable = transition.get("fable") or {}
    astra = transition.get("astra") or {}
    return {
        "bias": 1.0,
        f"category:{category}": 1.0,
        "disagreement": float(bool(state.get("disagreement"))),
        "appraisal": float(bool(state.get("appraisal_required"))),
        "fable_confidence": float(fable.get("confidence") or 0),
        "astra_confidence": float(astra.get("confidence") or 0),
    }


def _score(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(weights.get(name, 0.0) * value for name, value in features.items())


def _fit_head(rows: list[dict], head: str) -> dict:
    labels = [
        (row, row["label"]["truth"].get(head))
        for row in rows if row["label"]["truth"].get(head)
    ]
    classes = Counter(value for _, value in labels)
    feature_counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for row, value in labels:
        features = _features(row["transition"]["state"], row["transition"])
        for name, feature in features.items():
            if feature > 0:
                feature_counts[value][name]["present"] += 1
    return {
        "n": len(labels), "classes": dict(classes),
        "feature_counts": {
            value: {name: count["present"] for name, count in features.items()}
            for value, features in feature_counts.items()
        },
    }


def specialist_probabilities(head: dict, features: dict[str, float]) -> dict[str, float]:
    total = sum(head["classes"].values())
    if total == 0:
        return {}
    classes = head["classes"]
    log_scores = {}
    for value, count in classes.items():
        score = math.log((count + 1) / (total + len(classes)))
        for name, present in features.items():
            if present > 0:
                feature_count = head["feature_counts"].get(value, {}).get(name, 0)
                score += math.log((feature_count + 1) / (count + 2))
        log_scores[value] = score
    scale = max(log_scores.values())
    exp_scores = {value: math.exp(score - scale) for value, score in log_scores.items()}
    denominator = sum(exp_scores.values())
    return {value: score / denominator for value, score in exp_scores.items()}


def train_offline_policy(
    repository: SurveyRepository,
    *, train_survey_ids: list[UUID], holdout_survey_ids: list[UUID],
) -> dict:
    if not train_survey_ids or not holdout_survey_ids:
        raise ValueError("training and holdout survey IDs are required")
    if set(train_survey_ids) & set(holdout_survey_ids):
        raise ValueError("holdout surveys must be disjoint from training surveys")
    rows = [
        row for row in labeled_transitions(repository, train_survey_ids)
        if row["transition"].get("action_source") in {"policy", "jev+policy", "baseline"}
        and row["transition"].get("state", {}).get("logging_propensity") is not None
    ]
    if len(rows) < 10:
        raise ValueError(
            "at least 10 independently labeled transitions with known logging propensities "
            "are required"
        )
    support = Counter(row["transition"]["action"] for row in rows)
    weights: dict[str, dict[str, float]] = {action: {} for action in ACTIONS}
    for action in ACTIONS:
        action_rows = [row for row in rows if row["transition"]["action"] == action]
        if len(action_rows) < 3:
            continue
        coefficients = weights[action]
        for _ in range(200):
            gradients: dict[str, float] = defaultdict(float)
            for row in action_rows:
                transition = row["transition"]
                features = _features(transition["state"], transition)
                prediction = _score(coefficients, features)
                propensity = float(transition["state"].get("logging_propensity") or 1)
                if propensity <= 0 or propensity > 1:
                    raise ValueError("logging propensity must be in (0, 1]")
                error = (prediction - float(row["label"]["reward"])) / propensity
                for name, value in features.items():
                    gradients[name] += error * value
            for name, gradient in gradients.items():
                coefficients[name] = (
                    coefficients.get(name, 0.0) - 0.02 * gradient / len(action_rows)
                )
    specialists = {head: _fit_head(rows, head) for head in HEADS}
    labels_hash = sha256(canonical_json_bytes(rows)).hexdigest()
    gold_hashes = {}
    for survey_id in [*train_survey_ids, *holdout_survey_ids]:
        raw = repository.redis.get(f"{repository.key_prefix}:survey:{survey_id}:gold_set")
        if raw is not None:
            gold_hashes[str(survey_id)] = json.loads(raw)["sha256"]
    artifact = {
        "schema_version": "1.0.0", "policy_id": f"{POLICY_VERSION}:{labels_hash[:12]}",
        "technique": "offline contextual bandit, propensity-weighted linear reward regression",
        "specialist_technique": "Laplace-smoothed naive Bayes on labeled state features",
        "stage": "shadow", "trained_at": utc_now().isoformat(),
        "approved_at": None, "train_survey_ids": sorted(map(str, train_survey_ids)),
        "holdout_survey_ids": sorted(map(str, holdout_survey_ids)),
        "labels_sha256": labels_hash, "n_train": len(rows),
        "gold_set_hashes": gold_hashes,
        "gold_rosters_frozen": len(gold_hashes) == len(set(train_survey_ids + holdout_survey_ids)),
        "action_support": dict(support), "weights": weights, "specialists": specialists,
        "limitations": [
            "Only logged actions have outcome support; alternate-action rewards are unobserved.",
            "A shadow score is not a causal improvement estimate without adequate overlap.",
        ],
    }
    repository.redis.set(
        f"{repository.key_prefix}:policy:{artifact['policy_id']}",
        json.dumps(artifact, sort_keys=True), nx=True,
    )
    repository.redis.sadd(f"{repository.key_prefix}:policies", artifact["policy_id"])
    return artifact


def get_policy(repository: SurveyRepository, policy_id: str) -> dict:
    raw = repository.redis.get(f"{repository.key_prefix}:policy:{policy_id}")
    if raw is None:
        raise KeyError(policy_id)
    return json.loads(raw)


def list_policies(repository: SurveyRepository) -> list[dict]:
    ids = sorted(repository.redis.smembers(f"{repository.key_prefix}:policies"))
    return [get_policy(repository, policy_id) for policy_id in ids]


def shadow_policy(repository: SurveyRepository, policy_id: str) -> dict:
    policy = get_policy(repository, policy_id)
    rows = [
        row for row in labeled_transitions(
            repository, [UUID(value) for value in policy["holdout_survey_ids"]]
        )
        if row["transition"].get("action_source") in {"policy", "jev+policy", "baseline"}
        and row["transition"].get("state", {}).get("logging_propensity") is not None
    ]
    matched = 0
    supported = 0
    weighted_reward = 0.0
    baseline_reward = 0.0
    predictions = []
    head_scores = {
        head: {"correct_numerator": 0, "labeled_denominator": 0,
               "mean_true_class_probability": None, "probability_sum": 0.0}
        for head in HEADS
    }
    for row in rows:
        transition = row["transition"]
        features = _features(transition["state"], transition)
        available = [
            action for action, n in policy["action_support"].items()
            if n >= 3 and policy["weights"].get(action)
        ]
        choice = (
            max(available, key=lambda action: _score(policy["weights"][action], features))
            if available else "human_review"
        )
        logged_action = transition["action"]
        reward = float(row["label"]["reward"])
        propensity = float(transition["state"].get("logging_propensity") or 1)
        baseline_reward += reward
        if choice == logged_action:
            matched += 1
            weighted_reward += reward / propensity
        if choice in available:
            supported += 1
        predictions.append({
            "transition_id": transition["transition_id"],
            "predicted_action": choice, "logged_action": logged_action,
            "matched_logged_action": choice == logged_action,
        })
        for head_name, head in policy["specialists"].items():
            truth = row["label"].get("truth", {}).get(head_name)
            if truth is None:
                continue
            probabilities = specialist_probabilities(head, features)
            score = head_scores[head_name]
            score["labeled_denominator"] += 1
            score["correct_numerator"] += int(
                bool(probabilities) and max(probabilities, key=probabilities.get) == truth
            )
            score["probability_sum"] += probabilities.get(truth, 0.0)
    for score in head_scores.values():
        denominator = score["labeled_denominator"]
        score["mean_true_class_probability"] = (
            score.pop("probability_sum") / denominator if denominator else None
        )
    return {
        "policy_id": policy_id, "stage": "shadow", "holdout_numerator": len(rows),
        "holdout_denominator": len(rows), "matched_numerator": matched,
        "matched_denominator": len(rows), "supported_numerator": supported,
        "supported_denominator": len(rows),
        "observed_logged_reward_mean": baseline_reward / len(rows) if rows else None,
        "ips_reward_sum_on_matches": weighted_reward,
        "ips_reward_mean": weighted_reward / len(rows) if rows else None,
        "ips_warning": "No causal improvement claim: overlap and propensities require review.",
        "specialist_scores": head_scores,
        "predictions": predictions,
    }
