"""Append-only decision and successor-state records for the offline replay buffer."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from backend.app.domain.repository import SurveyRepository
from backend.app.utils.clocks import utc_now
from backend.app.utils.hashing import sha256_bytes


def record_decision(
    repository: SurveyRepository,
    survey_id: UUID,
    *,
    action: str,
    action_source: str,
    policy_id: str,
    state: dict,
    fable: dict | None = None,
    astra: dict | None = None,
    jev: dict | None = None,
    cost_usd: float = 0.0,
    elapsed_ms: int = 0,
    next_state_id: str | None = None,
) -> dict:
    repository.get(survey_id)
    if action not in {
        "accept", "recapture", "alternate_resolver", "human_review",
        "use_specialist_head", "use_frontier",
    }:
        raise ValueError("unsupported RL action")
    transition = {
        "schema_version": "1.0.0", "transition_id": str(uuid4()),
        "survey_id": str(survey_id), "policy_id": policy_id,
        "state": state, "action": action, "action_source": action_source,
        "fable": fable, "astra": astra, "jev": jev,
        "human_truth": None, "independent_outcome": None, "reward": None,
        "next_state_id": next_state_id, "cost_usd": cost_usd,
        "elapsed_ms": elapsed_ms, "created_at": utc_now().isoformat(),
    }
    repository.redis.rpush(
        f"{repository.key_prefix}:survey:{survey_id}:rl_transitions",
        json.dumps(transition, sort_keys=True),
    )
    return transition


def record_successor_state(
    repository: SurveyRepository, survey_id: UUID, *,
    state_id: str, predecessor_transition_id: str, evidence_ref: str,
    evidence_hash: str, state: dict,
) -> dict:
    transitions = [
        json.loads(raw) for raw in repository.redis.lrange(
            f"{repository.key_prefix}:survey:{survey_id}:rl_transitions", 0, -1
        )
    ]
    predecessor = next((
        row for row in transitions
        if row["transition_id"] == predecessor_transition_id
        and row.get("next_state_id") == state_id and row["action"] == "recapture"
    ), None)
    if predecessor is None:
        raise ValueError("state is not the successor of a recapture transition")
    if not repository.exists_bytes(survey_id, evidence_ref):
        raise ValueError("successor evidence does not exist")
    if sha256_bytes(repository.get_bytes(survey_id, evidence_ref)) != evidence_hash:
        raise ValueError("successor evidence hash mismatch")
    if evidence_hash == predecessor["state"].get("evidence_package_hash"):
        raise ValueError("recapture must add new evidence")
    payload = {
        "state_id": state_id, "predecessor_transition_id": predecessor_transition_id,
        "evidence_ref": evidence_ref, "evidence_hash": evidence_hash, "state": state,
        "observed_at": utc_now().isoformat(),
    }
    key = f"{repository.key_prefix}:survey:{survey_id}:rl_state:{state_id}"
    if not repository.redis.set(key, json.dumps(payload, sort_keys=True), nx=True):
        raise ValueError("successor state already recorded")
    return payload
