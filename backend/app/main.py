from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from backend.app.api.routes import router
from backend.app.config import Settings
from backend.app.domain.repository import SurveyRepository
from backend.app.workflows.surveys import SurveyWorkflow


def create_app(data_dir: Path | None = None) -> FastAPI:
    settings = Settings.from_environment()
    if data_dir is not None:
        settings = Settings(
            data_dir=data_dir,
            openai_small_model=settings.openai_small_model,
            openai_tts_model=settings.openai_tts_model,
            openai_tts_voice=settings.openai_tts_voice,
        )
    repository = SurveyRepository(settings.data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        yield

    app = FastAPI(
        title="Library Survey API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.survey_workflow = SurveyWorkflow(repository)
    app.include_router(router)

    @app.get("/healthz", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
