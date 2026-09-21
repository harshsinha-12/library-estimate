"""Per-request provider usage and estimated USD spend.

Rates are a dated local price catalog, not a claim about the eventual invoice.
Unknown models are recorded with cost=None so a new model cannot appear free.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from backend.app.domain.repository import SurveyRepository
from backend.app.utils.clocks import utc_now

PRICE_SOURCE_OPENAI = "https://developers.openai.com/api/docs/pricing"
PRICE_SOURCE_ANTHROPIC = "https://platform.claude.com/docs/en/models/fable-5-1/overview"
PRICE_SOURCE_SONNET = "https://www.anthropic.com/claude/sonnet"
PRICE_SOURCE_JEV = "https://typesafe.ai/blog/introducing-system-one-models-and-jev"
RATES: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("openai", "gpt-5.5"): ("5", "30", "0.50", PRICE_SOURCE_OPENAI),
    ("openai", "gpt-5.6-luna"): ("0.20", "1.20", "0.02", PRICE_SOURCE_OPENAI),
    ("openai", "gpt-5.6-terra"): ("2", "12", "0.20", PRICE_SOURCE_OPENAI),
    ("openai", "gpt-6-astra"): ("10", "50", "1", PRICE_SOURCE_OPENAI),
    ("openai", "gpt-4o-mini"): ("0.15", "0.60", "0.075", PRICE_SOURCE_OPENAI),
    ("openai", "gpt-4o"): ("2.50", "10", "1.25", PRICE_SOURCE_OPENAI),
    ("openai", "gpt-4.1"): ("2", "8", "0.50", PRICE_SOURCE_OPENAI),
    ("anthropic", "claude-sonnet-5"): ("2", "10", "2", PRICE_SOURCE_SONNET),
    ("anthropic", "claude-fable-5-1"): ("10", "50", "0.25", PRICE_SOURCE_ANTHROPIC),
    ("typesafe", "jev-latest"): ("0.042", "0", "0.042", PRICE_SOURCE_JEV),
    ("typesafe", "jev-1.13.0"): ("0.042", "0", "0.042", PRICE_SOURCE_JEV),
}
WEB_SEARCH_USD = Decimal("0.01")
SPEND_CAP_USD = Decimal("50")


class BudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageContext:
    repository: SurveyRepository
    survey_id: UUID
    run_id: UUID


_context: ContextVar[UsageContext | None] = ContextVar("provider_usage", default=None)


def bind_usage(context: UsageContext) -> Token:
    return _context.set(context)


def unbind_usage(token: Token) -> None:
    _context.reset(token)


@contextmanager
def usage_scope(repository: SurveyRepository, survey_id: UUID, run_id: UUID):
    token = bind_usage(UsageContext(repository, survey_id, run_id))
    try:
        yield
    finally:
        unbind_usage(token)


def lookup_model_pricing(provider: str, model: str) -> dict[str, str] | None:
    """Return published USD per million token rates for a known ID or dated snapshot."""
    provider, model = provider.lower(), model.lower()
    row = RATES.get((provider, model))
    if row is None:
        for (rate_provider, rate_model), candidate in RATES.items():
            prefix = rate_model + "-"
            if rate_provider != provider or not model.startswith(prefix):
                continue
            if re.fullmatch(r"\d{4}(?:-?\d{2}){2}", model.removeprefix(prefix)):
                row = candidate
                break
    if row is None:
        return None
    input_rate, output_rate, cached_rate, source = row
    return {
        "input_per_million_usd": input_rate,
        "output_per_million_usd": output_rate,
        "cached_input_per_million_usd": cached_rate,
        "source": source,
        "checked_at": "2026-09-21",
    }


def calculate_cost(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    web_search_calls: int = 0,
) -> str | None:
    rates = lookup_model_pricing(provider, model)
    if rates is None:
        return None
    if min(input_tokens, output_tokens, cached_input_tokens, web_search_calls) < 0:
        raise ValueError("usage counts cannot be negative")
    if cached_input_tokens > input_tokens:
        raise ValueError("cached input exceeds input tokens")
    total = (
        Decimal(input_tokens - cached_input_tokens)
        * Decimal(rates["input_per_million_usd"])
        + Decimal(cached_input_tokens)
        * Decimal(rates["cached_input_per_million_usd"])
        + Decimal(output_tokens) * Decimal(rates["output_per_million_usd"])
    ) / Decimal(1_000_000)
    total += Decimal(web_search_calls) * WEB_SEARCH_USD
    return str(total.quantize(Decimal("0.000001")))


def record_usage(
    *,
    provider: str,
    model: str,
    operation: str,
    response: dict[str, Any],
    latency_ms: int | None = None,
) -> dict[str, Any] | None:
    context = _context.get()
    if context is None:
        return None
    usage = response.get("usage") or {}
    token_fields = ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens")
    has_usage = any(field in usage for field in token_fields)
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens", 0) or 0)
    search_calls = sum(
        1 for item in response.get("output", [])
        if isinstance(item, dict) and item.get("type") == "web_search_call"
    )
    model_used = str(response.get("model") or model)
    cost = (
        calculate_cost(
            provider, model_used, input_tokens=input_tokens, output_tokens=output_tokens,
            cached_input_tokens=cached, web_search_calls=search_calls,
        )
        if has_usage else None
    )
    event = {
        "run_id": str(context.run_id),
        "survey_id": str(context.survey_id),
        "recorded_at": utc_now().isoformat(),
        "provider": provider,
        "model": model_used,
        "operation": operation,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "web_search_calls": search_calls,
        "cost_usd": cost,
        "usage_reported": has_usage,
        "pricing": lookup_model_pricing(provider, model_used),
        "latency_ms": latency_ms,
    }
    context.repository.redis.rpush(
        f"{context.repository.key_prefix}:survey:{context.survey_id}:usage:{context.run_id}",
        json.dumps(event, sort_keys=True),
    )
    context.repository.redis.sadd(
        f"{context.repository.key_prefix}:survey:{context.survey_id}:usage_runs",
        str(context.run_id),
    )
    return event


def reserve_budget(allowance_usd: str) -> None:
    """Conservatively reserve provider spend before a call; failed calls retain allowance."""
    context = _context.get()
    if context is None:
        return
    amount = int(Decimal(allowance_usd) * 1_000_000)
    key = f"{context.repository.key_prefix}:survey:{context.survey_id}:budget_reserved_micros"
    reserved = context.repository.redis.incrby(key, amount)
    if reserved > int(SPEND_CAP_USD * 1_000_000):
        context.repository.redis.decrby(key, amount)
        raise BudgetExceededError("per-survey $50 provider budget reached")


def usage_for_run(repository: SurveyRepository, survey_id: UUID, run_id: UUID) -> dict[str, Any]:
    key = f"{repository.key_prefix}:survey:{survey_id}:usage:{run_id}"
    events = [json.loads(raw) for raw in repository.redis.lrange(key, 0, -1)]
    total = sum((Decimal(row["cost_usd"]) for row in events if row["cost_usd"]), Decimal(0))
    return {
        "survey_id": str(survey_id), "run_id": str(run_id), "events": events,
        "estimated_cost_usd": str(total.quantize(Decimal("0.000001"))),
        "unpriced_calls": sum(row["cost_usd"] is None for row in events),
        "budget_reserved_usd": str(
            (Decimal(repository.redis.get(
                f"{repository.key_prefix}:survey:{survey_id}:budget_reserved_micros"
            ) or 0) / Decimal(1_000_000)).quantize(Decimal("0.000001"))
        ),
    }


def usage_for_survey(repository: SurveyRepository, survey_id: UUID) -> dict[str, Any]:
    key = f"{repository.key_prefix}:survey:{survey_id}:usage_runs"
    runs = [
        usage_for_run(repository, survey_id, UUID(raw))
        for raw in repository.redis.smembers(key)
    ]
    runs.sort(key=lambda row: row["run_id"])
    total = sum((Decimal(row["estimated_cost_usd"]) for row in runs), Decimal(0))
    return {
        "survey_id": str(survey_id), "runs": runs,
        "estimated_cost_usd": str(total.quantize(Decimal("0.000001"))),
        "unpriced_calls": sum(row["unpriced_calls"] for row in runs),
        "budget_cap_usd": str(SPEND_CAP_USD),
    }
