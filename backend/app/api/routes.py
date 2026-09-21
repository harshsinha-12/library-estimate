from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from backend.app.api.dependencies import get_survey_workflow
from backend.app.domain.models import (
    CapturePackageManifest,
    InventoryResult,
    SealResult,
    SurveyCreate,
    SurveyRecord,
    SurveyStateEvent,
    UploadedFile,
)
from backend.app.domain.repository import SurveyNotFoundError
from backend.app.providers.voice import synthesize_prompt
from backend.app.workflows.stage3 import apply_review
from backend.app.workflows.surveys import ManifestConflictError

router = APIRouter(prefix="/v1")

OPERATOR_PROMPTS = {
    "barcode": "Please capture the rear barcode and title page for this book.",
    "damage": "Please capture the damaged region close up with a scale reference.",
    "unbound": "Please select the object this note describes.",
}


@router.post("/surveys", response_model=SurveyRecord, status_code=status.HTTP_201_CREATED)
def create_survey(
    request: Request,
    payload: SurveyCreate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> SurveyRecord:
    try:
        return get_survey_workflow(request).create(payload, idempotency_key=idempotency_key)
    except ManifestConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/surveys/{survey_id}", response_model=SurveyRecord)
def get_survey(request: Request, survey_id: UUID) -> SurveyRecord:
    try:
        return get_survey_workflow(request).repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error


@router.get("/surveys/{survey_id}/jobs", response_model=list[SurveyStateEvent])
def get_survey_jobs(request: Request, survey_id: UUID) -> list[SurveyStateEvent]:
    try:
        return get_survey_workflow(request).repository.events(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error


@router.post("/surveys/{survey_id}/uploads", response_model=UploadedFile)
async def upload_evidence(
    request: Request,
    survey_id: UUID,
    path: Annotated[str, Query(min_length=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    content_type: Annotated[str, Header(alias="Content-Type", min_length=1)],
) -> UploadedFile:
    content = await request.body()
    try:
        return get_survey_workflow(request).upload(
            survey_id,
            path=path,
            mime_type=content_type.split(";", maxsplit=1)[0],
            content=content,
            idempotency_key=idempotency_key,
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except (ManifestConflictError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/surveys/{survey_id}/seal", response_model=SealResult)
def seal_survey(
    request: Request,
    survey_id: UUID,
    manifest: Annotated[CapturePackageManifest, Body()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> SealResult:
    try:
        return get_survey_workflow(request).seal(
            survey_id,
            manifest,
            idempotency_key=idempotency_key,
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ManifestConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/surveys/{survey_id}/inventory", response_model=InventoryResult)
def get_inventory(request: Request, survey_id: UUID) -> InventoryResult:
    try:
        get_survey_workflow(request).repository.get(survey_id)
        inventory = get_survey_workflow(request).inventory(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    if inventory is None:
        raise HTTPException(status_code=404, detail="inventory not available")
    return inventory


@router.get("/surveys/{survey_id}/shelves")
def get_shelves(request: Request, survey_id: UUID) -> dict[str, object]:
    try:
        get_survey_workflow(request).repository.get(survey_id)
        inventory = get_survey_workflow(request).inventory(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    if inventory is None:
        return {
            "survey_id": str(survey_id),
            "faces": [],
            "overlays": [],
            "recapture": [],
            "copy_count": 0,
        }
    return {
        "survey_id": str(survey_id),
        "status": inventory.status,
        "faces": [item.model_dump(mode="json") for item in inventory.shelf_face_data_sizes],
        "overlays": [item.model_dump(mode="json") for item in inventory.overlays],
        "recapture": inventory.recapture,
        "copy_count": len(inventory.asset_copies),
    }


@router.get("/surveys/{survey_id}/review")
def get_review(request: Request, survey_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    return repository.get_json(survey_id, "stage3") or {
        "survey_id": str(survey_id),
        "queue": [],
        "status": "not_processed",
    }


@router.post("/reviews/{review_id}/decision")
def review_decision(request: Request, review_id: str, payload: dict) -> dict:
    try:
        survey_id = UUID(payload["survey_id"])
        return apply_review(
            get_survey_workflow(request).repository, survey_id, {**payload, "queue_id": review_id}
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/operator-prompts/{prompt_id}/speech")
def operator_prompt_speech(request: Request, prompt_id: str) -> Response:
    prompt = OPERATOR_PROMPTS.get(prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="unknown operator prompt")
    settings = request.app.state.settings
    try:
        audio = synthesize_prompt(
            prompt,
            model=settings.openai_tts_model if settings else "gpt-4o-mini-tts",
            voice=settings.openai_tts_voice if settings else "marin",
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail="operator speech is unavailable") from error
    return Response(content=audio, media_type="audio/mpeg")
