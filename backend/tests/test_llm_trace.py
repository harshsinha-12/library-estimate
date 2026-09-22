import json

from backend.app.providers.llm_trace import record_llm_call, sanitize


def test_sanitize_redacts_secrets_and_image_bytes() -> None:
    cleaned = sanitize(
        {
            "authorization": "Bearer secret",
            "api_key": "sk-test",
            "image_url": {"url": "data:image/jpeg;base64," + ("A" * 80)},
            "messages": [{"role": "user", "content": "Find Clean Code"}],
        }
    )
    assert cleaned["authorization"] == "[redacted]"
    assert cleaned["api_key"] == "[redacted]"
    assert cleaned["image_url"]["url"].startswith("[omitted data URI")
    assert cleaned["messages"][0]["content"] == "Find Clean Code"


def test_record_writes_reason_query_and_response(tmp_path, monkeypatch) -> None:
    path = tmp_path / "llm_calls.json"
    monkeypatch.setenv("LLM_TRACE_PATH", str(path))
    monkeypatch.delenv("LLM_TRACE_DISABLED", raising=False)
    record_llm_call(
        reason="Web search: find a current physical-copy purchase price",
        operation="web_search",
        provider="openai",
        model="gpt-5.6-luna",
        query={"prompt": "price Clean Code in IN"},
        response={
            "output_text": '{"amount": 499}',
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
        latency_ms=12,
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["reason"].startswith("Web search:")
    assert saved[0]["query"]["prompt"] == "price Clean Code in IN"
    assert saved[0]["response_text"] == '{"amount": 499}'
    assert saved[0]["response"]["output_text"] == '{"amount": 499}'


def test_record_appends_to_existing_json_array(tmp_path, monkeypatch) -> None:
    path = tmp_path / "llm_calls.json"
    monkeypatch.setenv("LLM_TRACE_PATH", str(path))
    for index in range(2):
        record_llm_call(
            reason=f"call {index}",
            operation="stt",
            provider="openai",
            model="gpt-4o-transcribe-diarize",
            query={"audio_bytes": index},
            response={"text": f"hello {index}"},
        )
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [row["reason"] for row in saved] == ["call 0", "call 1"]
