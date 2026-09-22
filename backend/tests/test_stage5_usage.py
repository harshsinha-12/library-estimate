from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.main import create_app
from backend.app.providers.usage import (
    UsageContext,
    bind_usage,
    calculate_cost,
    record_usage,
    reserve_budget,
    unbind_usage,
    usage_for_run,
)
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now


def test_cost_lookup_counts_cached_tokens_and_web_search_calls() -> None:
    assert calculate_cost(
        "openai", "gpt-5.5", input_tokens=1000, cached_input_tokens=200,
        output_tokens=100, web_search_calls=2,
    ) == "0.027100"
    assert calculate_cost("openai", "unknown", input_tokens=1, output_tokens=1) is None
    assert calculate_cost(
        "openai", "gpt-5.5-20260917", input_tokens=1000, output_tokens=0
    ) == "0.005000"


def test_run_usage_is_saved_to_redis_without_a_spend_cap() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    repository = SurveyRepository(client, MemoryObjectStore(), key_prefix="test:usage")
    survey_id, run_id = uuid4(), uuid4()
    token = bind_usage(UsageContext(repository, survey_id, run_id))
    try:
        reserve_budget("0.25")
        record_usage(
            provider="openai", model="gpt-5.5", operation="web_search",
            response={
                "model": "gpt-5.5",
                "usage": {"input_tokens": 1000, "output_tokens": 100},
                "output": [{"type": "web_search_call"}],
            },
        )
        saved = usage_for_run(repository, survey_id, run_id)
        assert saved["estimated_cost_usd"] == "0.018000"
        assert saved["budget_reserved_usd"] == "0.250000"
        assert saved["events"][0]["web_search_calls"] == 1
        reserve_budget("50")
        reserve_budget("50")
        after = usage_for_run(repository, survey_id, run_id)
        assert after["budget_reserved_usd"] == "100.250000"
    finally:
        unbind_usage(token)


def test_http_run_header_retrieves_usage_from_sync_route() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(redis_client=redis, object_store=MemoryObjectStore())
    repository = app.state.survey_workflow.repository
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Run test",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="created", created_at=utc_now(),
    ))

    def mocked_search(_survey_id, _payload):
        record_usage(
            provider="openai", model="gpt-5.6-luna", operation="small_model:test",
            response={"usage": {"prompt_tokens": 100, "completion_tokens": 10}},
        )
        return {"ok": True}

    app.state.survey_workflow.live_price_search = mocked_search
    with TestClient(app) as client:
        response = client.post(f"/v1/surveys/{survey_id}/live-price-search", json={"title": "Book"})
        assert response.status_code == 200
        run_id = response.headers["X-Survey-Run-Id"]
        usage = client.get(f"/v1/surveys/{survey_id}/runs/{run_id}/usage").json()
        assert usage["events"][0]["operation"] == "small_model:test"
        assert usage["estimated_cost_usd"] == "0.000032"
