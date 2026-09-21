from __future__ import annotations

from typing import TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from backend.app.domain.models import (
    CaptureFile,
    CapturePackageManifest,
    InventoryResult,
    SealResult,
    SurveyCreate,
    SurveyRecord,
    UploadedFile,
)
from backend.app.domain.repository import SurveyRepository
from backend.app.utils.clocks import utc_now
from backend.app.utils.hashing import sha256_bytes
from backend.app.utils.json_codec import canonical_json_bytes
from backend.app.workflows.geometry import GeometryError, GeometryWorker
from backend.app.workflows.stage3 import Stage3Worker
from backend.app.workflows.vision import VisionWorker


class ManifestConflictError(ValueError):
    pass


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class SurveyWorkflow:
    def __init__(
        self,
        repository: SurveyRepository,
        geometry_worker: GeometryWorker | None = None,
        vision_worker: VisionWorker | None = None,
        stage3_worker: Stage3Worker | None = None,
    ) -> None:
        self.repository = repository
        self.geometry_worker = geometry_worker or GeometryWorker()
        self.vision_worker = vision_worker or VisionWorker()
        self.stage3_worker = stage3_worker or Stage3Worker()

    def create(self, request: SurveyCreate, *, idempotency_key: str) -> SurveyRecord:
        request_hash = sha256_bytes(canonical_json_bytes(request.model_dump(mode="json")))
        replay = self._replay("create-survey", idempotency_key, request_hash, SurveyRecord)
        if replay is not None:
            return replay
        record = SurveyRecord(
            survey_id=request.survey_id or uuid4(),
            display_name=request.display_name,
            geography=request.geography,
            status="created",
            created_at=utc_now(),
        )
        result = self.repository.create(record)
        self._record("create-survey", idempotency_key, request_hash, result)
        return result

    def upload(
        self,
        survey_id: UUID,
        *,
        path: str,
        mime_type: str,
        content: bytes,
        idempotency_key: str,
    ) -> UploadedFile:
        if not content:
            raise ManifestConflictError("uploaded evidence must not be empty")
        upload = UploadedFile(
            path=path,
            mime_type=mime_type,
            bytes=len(content),
            sha256=sha256_bytes(content),
        )
        CaptureFile(**upload.model_dump())
        if path.startswith("derived/"):
            raise ManifestConflictError("derived/ is reserved for server-generated output")
        scope = f"upload:{survey_id}"
        request_hash = sha256_bytes(
            canonical_json_bytes(
                {
                    "path": path,
                    "mime_type": mime_type,
                    "content_sha256": upload.sha256,
                }
            )
        )
        replay = self._replay(scope, idempotency_key, request_hash, UploadedFile)
        if replay is not None:
            return replay
        survey = self.repository.get(survey_id)
        if survey.status not in {"created", "capturing", "uploading"}:
            raise ManifestConflictError(f"uploads are not accepted while survey is {survey.status}")
        if survey.status == "created":
            self.repository.transition(survey_id, "capturing", occurred_at=utc_now())
        if survey.status != "uploading":
            self.repository.transition(survey_id, "uploading", occurred_at=utc_now())
        result = self.repository.store_upload(survey_id, upload, content)
        self._record(scope, idempotency_key, request_hash, result)
        return result

    def seal(
        self,
        survey_id: UUID,
        manifest: CapturePackageManifest,
        *,
        idempotency_key: str,
    ) -> SealResult:
        manifest_data = manifest.model_dump(mode="json")
        request_hash = sha256_bytes(canonical_json_bytes(manifest_data))
        scope = f"seal:{survey_id}"
        replay = self._replay(scope, idempotency_key, request_hash, SealResult)
        if replay is not None:
            return replay
        survey = self.repository.get(survey_id)
        if survey.status != "uploading":
            raise ManifestConflictError("survey must have uploaded evidence before sealing")
        self.repository.transition(survey_id, "ingest_validation", occurred_at=utc_now())
        try:
            self._validate_manifest(survey_id, manifest, survey.geography)
        except ManifestConflictError as error:
            self.repository.transition(
                survey_id,
                "recapture_required",
                detail=str(error),
                occurred_at=utc_now(),
            )
            raise

        package_hash = sha256_bytes(canonical_json_bytes(manifest_data))
        sealed_at = utc_now()
        self.repository.write_manifest(survey_id, manifest_data)
        self._verify_reopened_package(survey_id)
        self.repository.seal(survey_id, package_hash, sealed_at)
        geometry_svg_path = None
        geometry_summary_path = None
        usdz_path = None
        try:
            geometry = self.geometry_worker.process(self.repository, survey_id)
            inventory = self.vision_worker.process(self.repository, survey_id)
            self.stage3_worker.process(self.repository, survey_id)
            if inventory and inventory.overlays:
                geometry = self.geometry_worker.process(
                    self.repository,
                    survey_id,
                    overlays=inventory.overlays,
                )
            if geometry.usdz_path is None:
                final_status = "partial"
                detail = "2D geometry generated; RoomPlan USDZ is missing"
            else:
                final_status = "geometry"
                detail = None
                usdz_path = geometry.usdz_path
            geometry_svg_path = geometry.svg_path
            geometry_summary_path = geometry.summary_path
        except GeometryError as error:
            final_status = "partial"
            detail = str(error)
        self.repository.transition(survey_id, final_status, detail=detail, occurred_at=utc_now())
        result = SealResult(
            survey_id=survey_id,
            status=final_status,
            package_hash=package_hash,
            file_count=len(manifest.files),
            sealed_at=sealed_at,
            geometry_svg_path=geometry_svg_path,
            geometry_summary_path=geometry_summary_path,
            usdz_path=usdz_path,
        )
        self._record(scope, idempotency_key, request_hash, result)
        return result

    def inventory(self, survey_id: UUID) -> InventoryResult | None:
        stored = self.repository.get_json(survey_id, "inventory")
        if stored is None:
            return self.vision_worker.process(self.repository, survey_id)
        return InventoryResult.model_validate(stored)

    def _validate_manifest(
        self,
        survey_id: UUID,
        manifest: CapturePackageManifest,
        confirmed_geography: object,
    ) -> None:
        if manifest.survey_id != survey_id:
            raise ManifestConflictError("manifest survey_id does not match the route")
        if manifest.geography != confirmed_geography:
            raise ManifestConflictError(
                "sealed geography differs from the confirmed survey geography"
            )
        uploads = self.repository.uploads(survey_id)
        for expected in manifest.files:
            actual = uploads.get(expected.path)
            if actual is None:
                raise ManifestConflictError(f"missing uploaded file: {expected.path}")
            if actual.sha256 != expected.sha256:
                raise ManifestConflictError(f"SHA-256 mismatch: {expected.path}")
            if actual.bytes != expected.bytes:
                raise ManifestConflictError(f"byte-size mismatch: {expected.path}")
            if actual.mime_type != expected.mime_type:
                raise ManifestConflictError(f"MIME-type mismatch: {expected.path}")

    def _verify_reopened_package(self, survey_id: UUID) -> None:
        reopened = CapturePackageManifest.model_validate_json(
            self.repository.get_bytes(survey_id, "manifest.json").decode("utf-8")
        )
        for expected in reopened.files:
            data = self.repository.get_bytes(survey_id, expected.path)
            if len(data) != expected.bytes or sha256_bytes(data) != expected.sha256:
                raise ManifestConflictError(
                    f"reopened package verification failed: {expected.path}"
                )

    def _replay(
        self,
        scope: str,
        key: str,
        request_hash: str,
        model: type[ResponseModel],
    ) -> ResponseModel | None:
        stored = self.repository.get_idempotency(scope, key)
        if stored is None:
            return None
        stored_hash, response_json = stored
        if stored_hash != request_hash:
            raise ManifestConflictError("idempotency key was already used for another request")
        return model.model_validate_json(response_json)

    def _record(
        self,
        scope: str,
        key: str,
        request_hash: str,
        response: BaseModel,
    ) -> None:
        self.repository.save_idempotency(
            scope=scope,
            key=key,
            request_hash=request_hash,
            response_json=response.model_dump_json(),
            created_at=utc_now(),
        )
