from __future__ import annotations

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis import Redis

from backend.app.api.routes import router
from backend.app.config import Settings
from backend.app.domain.repository import SurveyRepository
from backend.app.providers.pricing import log as pricing_log
from backend.app.providers.usage import BudgetExceededError, UsageContext, bind_usage, unbind_usage
from backend.app.storage.encrypted import EncryptedObjectStore
from backend.app.storage.objects import ObjectStore, S3ObjectStore
from backend.app.storage.redis_client import connect_redis
from backend.app.utils.clocks import utc_now
from backend.app.workflows.migrate_sqlite import migrate_sqlite_if_present
from backend.app.workflows.surveys import SurveyWorkflow


def create_app(
    data_dir: Path | None = None,
    *,
    redis_client: Redis | None = None,
    object_store: ObjectStore | None = None,
    key_prefix: str | None = None,
    migrate_sqlite: bool = False,
    operator_token: str | None = None,
    require_auth: bool | None = None,
    data_encryption_key: str | None = None,
) -> FastAPI:
    settings: Settings | None = None
    if redis_client is None or object_store is None:
        settings = Settings.from_environment()
        redis_client = redis_client or connect_redis(settings)
        object_store = object_store or S3ObjectStore(settings)
        key_prefix = key_prefix or settings.redis_key_prefix
        data_dir = data_dir or settings.data_dir
    else:
        key_prefix = key_prefix or "ls:test"
        data_dir = data_dir or Path("data/runtime")

    operator_token = operator_token if operator_token is not None else (
        settings.operator_token if settings else os.getenv("LIBRARY_OPERATOR_TOKEN", "").strip()
    )
    require_auth = require_auth if require_auth is not None else (
        settings.require_auth if settings else False
    )
    data_encryption_key = data_encryption_key if data_encryption_key is not None else (
        settings.data_encryption_key if settings else ""
    )
    if require_auth and not operator_token:
        raise RuntimeError("LIBRARY_OPERATOR_TOKEN is required when auth is enabled")
    if data_encryption_key:
        object_store = EncryptedObjectStore(object_store, data_encryption_key)
    repository = SurveyRepository(redis_client, object_store, key_prefix=key_prefix)
    small_model = settings.openai_small_model if settings else "gpt-5.6-luna"
    pricing_log.configure()
    pricing_log.info("backend_ready", small_model=small_model)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        if migrate_sqlite and data_dir is not None:
            migrate_sqlite_if_present(data_dir, repository)
        yield

    app = FastAPI(
        title="Library Survey API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.data_dir = data_dir
    app.state.survey_workflow = SurveyWorkflow(repository, small_model=small_model)

    @app.middleware("http")
    async def operator_access(request, call_next):
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {operator_token}"
        authorized = not require_auth or hmac.compare_digest(supplied, expected)
        if not authorized:
            response = JSONResponse(
                status_code=401,
                content={"detail": "operator authorization required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        repository.redis.rpush(
            f"{repository.key_prefix}:access_log",
            json.dumps({
                "at": utc_now().isoformat(), "method": request.method,
                "path": re.sub(
                    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", ":id", request.url.path,
                ),
                "status": response.status_code,
                "authorized": authorized,
            }, sort_keys=True),
        )
        repository.redis.ltrim(f"{repository.key_prefix}:access_log", -10000, -1)
        return response

    @app.exception_handler(BudgetExceededError)
    async def budget_exceeded_handler(_, error: BudgetExceededError):
        return JSONResponse(status_code=429, content={"detail": str(error)})

    @app.middleware("http")
    async def usage_scope(request, call_next):
        parts = request.url.path.split("/")
        try:
            survey_id = UUID(parts[3]) if len(parts) > 3 and parts[2] == "surveys" else None
        except ValueError:
            survey_id = None
        if survey_id is None:
            return await call_next(request)
        run_id = uuid4()
        request.state.run_id = run_id
        token = bind_usage(UsageContext(repository, survey_id, run_id))
        try:
            response = await call_next(request)
            response.headers["X-Survey-Run-Id"] = str(run_id)
            return response
        finally:
            unbind_usage(token)
    app.include_router(router)

    @app.get("/healthz", tags=["operations"])
    def health() -> dict[str, str]:
        repository.initialize()
        return {"status": "ok", "redis": "ok", "object_store": "ok"}

    return app


def production_app() -> FastAPI:
    return create_app(migrate_sqlite=True)
