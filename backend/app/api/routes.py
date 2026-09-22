from __future__ import annotations

import json
import mimetypes
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from backend.app.api.dependencies import get_survey_workflow
from backend.app.domain.models import (
    AstraLiveRequest,
    CapturePackageManifest,
    InventoryResult,
    LivePriceSearchRequest,
    PriceObservationWrite,
    PriceQueueRequest,
    PriceSearchRequest,
    SealResult,
    SurveyCreate,
    SurveyRecord,
    SurveyStateEvent,
    UploadedFile,
)
from backend.app.domain.repository import SurveyNotFoundError
from backend.app.providers.usage import usage_for_run, usage_for_survey, usage_scope
from backend.app.providers.voice import synthesize_prompt
from backend.app.rl.offline import (
    IndependentLabel,
    append_label,
    freeze_gold_set,
    list_policies,
    shadow_policy,
    train_offline_policy,
)
from backend.app.rl.transitions import record_successor_state
from backend.app.utils.paths import validate_package_path
from backend.app.workflows.astra_live import list_astra_live, record_astra_live
from backend.app.workflows.models import ModelReplayError, replay_asset
from backend.app.workflows.operator_failures import failure_actions
from backend.app.workflows.report import build_report
from backend.app.workflows.stage3 import apply_review, correct_book_identity
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


@router.get("/surveys/{survey_id}/operator-actions")
def get_operator_actions(request: Request, survey_id: UUID) -> dict:
    workflow = get_survey_workflow(request)
    repository = workflow.repository
    try:
        survey = repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    overview = workflow.overview(survey_id) if survey.package_hash else {}
    stage3 = repository.get_json(survey_id, "stage3") or {}
    model_key = f"{repository.key_prefix}:survey:{survey_id}:model_runs"
    runs = [
        repository.get_json(survey_id, f"model-run:{raw}")
        for raw in sorted(repository.redis.smembers(model_key))
    ]
    return {
        "survey_id": str(survey_id),
        "actions": failure_actions(
            survey.model_dump(mode="json"), overview, stage3, [row for row in runs if row]
        ),
    }


@router.get("/surveys/{survey_id}/runs/{run_id}/usage")
def get_run_usage(request: Request, survey_id: UUID, run_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    return usage_for_run(repository, survey_id, run_id)


@router.get("/surveys/{survey_id}/usage")
def get_survey_usage(request: Request, survey_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    return usage_for_survey(repository, survey_id)


@router.get("/surveys/{survey_id}/report")
def get_report(request: Request, survey_id: UUID) -> dict:
    try:
        report, _ = build_report(get_survey_workflow(request).repository, survey_id)
        return report
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/surveys/{survey_id}/report.pdf")
def get_report_pdf(request: Request, survey_id: UUID) -> Response:
    try:
        _, pdf = build_report(get_survey_workflow(request).repository, survey_id)
        return Response(content=pdf, media_type="application/pdf")
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/surveys/{survey_id}/evidence")
def get_evidence(request: Request, survey_id: UUID, path: str = Query(min_length=1)) -> Response:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
        validate_package_path(path)
        if path not in repository.uploads(survey_id) and not path.startswith("derived/"):
            raise FileNotFoundError(path)
        content = repository.get_bytes(survey_id, path)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="evidence not found") from error
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=content, media_type=media_type)


@router.delete("/surveys/{survey_id}")
def delete_survey(
    request: Request,
    survey_id: UUID,
    confirm: Annotated[str, Header(alias="X-Confirm-Delete")],
) -> dict:
    if confirm != str(survey_id):
        raise HTTPException(status_code=422, detail="X-Confirm-Delete must match survey ID")
    try:
        counts = get_survey_workflow(request).repository.delete_survey(survey_id)
        return {"survey_id": str(survey_id), **counts}
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error


@router.post("/surveys/{survey_id}/assets/{asset_copy_id}/model-replay")
def model_replay(request: Request, survey_id: UUID, asset_copy_id: str) -> dict:
    try:
        return replay_asset(
            get_survey_workflow(request).repository,
            survey_id,
            asset_copy_id,
            run_id=request.state.run_id,
            source="button_replay",
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ModelReplayError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/surveys/{survey_id}/astra-live")
def astra_live(request: Request, survey_id: UUID, payload: AstraLiveRequest) -> dict:
    try:
        return record_astra_live(
            get_survey_workflow(request).repository,
            survey_id,
            payload.model_dump(mode="json"),
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error


@router.get("/surveys/{survey_id}/astra-live")
def get_astra_live(request: Request, survey_id: UUID) -> dict:
    try:
        return list_astra_live(get_survey_workflow(request).repository, survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error


@router.get("/surveys/{survey_id}/model-runs")
def get_model_runs(request: Request, survey_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    key = f"{repository.key_prefix}:survey:{survey_id}:model_runs"
    runs = [
        repository.get_json(survey_id, f"model-run:{raw}")
        for raw in sorted(repository.redis.smembers(key))
    ]
    return {"survey_id": str(survey_id), "runs": [row for row in runs if row]}


@router.get("/surveys/{survey_id}/rl-transitions")
def get_rl_transitions(request: Request, survey_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    key = f"{repository.key_prefix}:survey:{survey_id}:rl_transitions"
    rows = [json.loads(raw) for raw in repository.redis.lrange(key, 0, -1)]
    return {"survey_id": str(survey_id), "transitions": rows}


@router.get("/surveys/{survey_id}/auto-accept-audit")
def get_auto_accept_audit(request: Request, survey_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    key = f"{repository.key_prefix}:survey:{survey_id}:auto_accept_audit"
    return {
        "survey_id": str(survey_id),
        "entries": [json.loads(raw) for raw in repository.redis.lrange(key, 0, -1)],
    }


@router.post("/surveys/{survey_id}/rl-successor-states")
def create_rl_successor_state(request: Request, survey_id: UUID, payload: dict) -> dict:
    try:
        return record_successor_state(
            get_survey_workflow(request).repository,
            survey_id,
            state_id=str(payload["state_id"]),
            predecessor_transition_id=str(payload["predecessor_transition_id"]),
            evidence_ref=str(payload["evidence_ref"]),
            evidence_hash=str(payload["evidence_hash"]),
            state=dict(payload["state"]),
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/surveys/{survey_id}/independent-labels")
def create_independent_label(request: Request, survey_id: UUID, payload: IndependentLabel) -> dict:
    if payload.survey_id != survey_id:
        raise HTTPException(status_code=422, detail="label survey_id does not match route")
    try:
        return append_label(get_survey_workflow(request).repository, payload)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/surveys/{survey_id}/gold-set")
def create_gold_set(request: Request, survey_id: UUID, payload: dict) -> dict:
    try:
        return freeze_gold_set(
            get_survey_workflow(request).repository,
            survey_id,
            copy_ids=list(payload["copy_ids"]),
            case_tags=dict(payload["case_tags"]),
            frozen_by=str(payload["frozen_by"]),
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/surveys/{survey_id}/gold-set")
def get_gold_set(request: Request, survey_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    raw = repository.redis.get(f"{repository.key_prefix}:survey:{survey_id}:gold_set")
    if raw is None:
        raise HTTPException(status_code=404, detail="gold set not frozen")
    return json.loads(raw)


@router.get("/surveys/{survey_id}/independent-labels")
def get_independent_labels(request: Request, survey_id: UUID) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    key = f"{repository.key_prefix}:survey:{survey_id}:independent_labels"
    return {
        "survey_id": str(survey_id),
        "labels": [json.loads(raw) for raw in repository.redis.lrange(key, 0, -1)],
    }


@router.post("/policies/train")
def train_policy(request: Request, payload: dict) -> dict:
    try:
        train_ids = [UUID(value) for value in payload["train_survey_ids"]]
        holdout_ids = [UUID(value) for value in payload["holdout_survey_ids"]]
        return train_offline_policy(
            get_survey_workflow(request).repository,
            train_survey_ids=train_ids,
            holdout_survey_ids=holdout_ids,
        )
    except (KeyError, ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/policies")
def get_policies(request: Request) -> dict:
    return {"policies": list_policies(get_survey_workflow(request).repository)}


@router.post("/policies/{policy_id}/shadow")
def run_policy_shadow(request: Request, policy_id: str) -> dict:
    try:
        return shadow_policy(get_survey_workflow(request).repository, policy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="policy not found") from error


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


@router.get("/surveys/{survey_id}/overview")
def get_overview(request: Request, survey_id: UUID) -> dict:
    try:
        get_survey_workflow(request).repository.get(survey_id)
        return get_survey_workflow(request).overview(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error


@router.post("/assets/{asset_id}/price-search")
def price_search(
    request: Request, response: Response, asset_id: str, payload: PriceSearchRequest
) -> dict:
    try:
        run_id = uuid4()
        with usage_scope(get_survey_workflow(request).repository, payload.survey_id, run_id):
            result = get_survey_workflow(request).price_search(payload.survey_id, asset_id)
        response.headers["X-Survey-Run-Id"] = str(run_id)
        return result
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/surveys/{survey_id}/live-price-search")
def live_price_search(
    request: Request,
    survey_id: UUID,
    payload: LivePriceSearchRequest,
) -> dict:
    try:
        return get_survey_workflow(request).live_price_search(
            survey_id, payload.model_dump(mode="json")
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/surveys/{survey_id}/identify-and-price")
def identify_and_price(request: Request, survey_id: UUID) -> dict:
    try:
        return get_survey_workflow(request).identify_and_price(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/surveys/{survey_id}/price-search-queue")
def price_search_queue(
    request: Request,
    survey_id: UUID,
    payload: PriceQueueRequest | None = None,
) -> dict:
    del payload
    try:
        return get_survey_workflow(request).price_queue(survey_id)
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/assets/{asset_id}/price-observations")
def price_observations(request: Request, asset_id: str, payload: PriceObservationWrite) -> dict:
    try:
        return get_survey_workflow(request).price_observation(
            payload.survey_id, asset_id, payload.model_dump(mode="json")
        )
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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


@router.post("/surveys/{survey_id}/books/{asset_id}/identity-correction")
def post_identity_correction(
    request: Request, survey_id: UUID, asset_id: str, payload: dict
) -> dict:
    repository = get_survey_workflow(request).repository
    try:
        repository.get(survey_id)
        return correct_book_identity(repository, survey_id, {**payload, "asset_copy_id": asset_id})
    except SurveyNotFoundError as error:
        raise HTTPException(status_code=404, detail="survey not found") from error
    except ValueError as error:
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
