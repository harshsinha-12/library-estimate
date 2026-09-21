from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from redis import Redis

from backend.app.api.routes import router
from backend.app.config import Settings
from backend.app.domain.repository import SurveyRepository
from backend.app.providers.pricing import log as pricing_log
from backend.app.storage.objects import ObjectStore, S3ObjectStore
from backend.app.storage.redis_client import connect_redis
from backend.app.workflows.migrate_sqlite import migrate_sqlite_if_present
from backend.app.workflows.surveys import SurveyWorkflow


def create_app(
    data_dir: Path | None = None,
    *,
    redis_client: Redis | None = None,
    object_store: ObjectStore | None = None,
    key_prefix: str | None = None,
    migrate_sqlite: bool = False,
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
    app.include_router(router)

    @app.get("/healthz", tags=["operations"])
    def health() -> dict[str, str]:
        repository.initialize()
        return {"status": "ok", "redis": "ok", "object_store": "ok"}

    return app


def production_app() -> FastAPI:
    return create_app(migrate_sqlite=True)
