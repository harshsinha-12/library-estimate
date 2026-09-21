from __future__ import annotations

import json
from uuid import UUID

from backend.app.domain.models import (
    AssetCopy,
    InventoryResult,
    Observation,
    ShelfFaceDataSize,
    ShelfOverlay,
    Track,
)
from backend.app.domain.repository import SurveyRepository
from backend.app.utils.hashing import sha256_bytes
from backend.app.utils.json_codec import canonical_json_bytes
from cv.library_vision.pipeline import PIPELINE_VERSION, count_labeled_shelf


def vision_job_key(
    survey_id: UUID, input_hash: str, pipeline_version: str = PIPELINE_VERSION
) -> str:
    return f"{survey_id}:vision:{input_hash}:{pipeline_version}"


class VisionWorker:
    def process(self, repository: SurveyRepository, survey_id: UUID) -> InventoryResult | None:
        labeled = self._load_labeled(repository, survey_id)
        if labeled is None:
            return None
        input_hash = sha256_bytes(canonical_json_bytes(labeled))
        job_key = vision_job_key(survey_id, input_hash)
        existing = repository.get_job(job_key)
        if existing is not None:
            result = InventoryResult.model_validate(existing["result"])
            repository.save_json(survey_id, "inventory", result.model_dump(mode="json"))
            return result
        counted = count_labeled_shelf(labeled)
        result = InventoryResult(
            survey_id=survey_id,
            status=counted.status,  # type: ignore[arg-type]
            pipeline_version=counted.pipeline_version,
            run_id=counted.run_id,
            observations=[Observation.model_validate(item) for item in counted.observations],
            tracks=[Track.model_validate(item) for item in counted.tracks],
            asset_copies=[
                AssetCopy.model_validate(
                    {key: value for key, value in item.items() if key != "track_ids"}
                )
                for item in counted.asset_copies
            ],
            shelf_face_data_sizes=[
                ShelfFaceDataSize.model_validate(item) for item in counted.faces
            ],
            overlays=[ShelfOverlay.model_validate(item) for item in counted.overlays],
            recapture=counted.recapture,
        )
        payload = {
            "job_key": job_key,
            "survey_id": str(survey_id),
            "stage": "vision",
            "status": result.status,
            "input_hash": input_hash,
            "pipeline_version": PIPELINE_VERSION,
            "result": result.model_dump(mode="json"),
        }
        stored = repository.save_job(job_key, payload)
        result = InventoryResult.model_validate(stored["result"])
        repository.save_json(survey_id, "inventory", result.model_dump(mode="json"))
        repository.save_json(
            survey_id,
            "ir",
            {
                "schema_version": "1.0.0",
                "survey_id": str(survey_id),
                "observations": result.model_dump(mode="json")["observations"],
                "tracks": result.model_dump(mode="json")["tracks"],
                "asset_copies": result.model_dump(mode="json")["asset_copies"],
                "shelf_face_data_sizes": result.model_dump(mode="json")["shelf_face_data_sizes"],
            },
        )
        return result

    def _load_labeled(self, repository: SurveyRepository, survey_id: UUID) -> dict | None:
        candidates = [
            "shelf_scans/labeled.json",
            "shelf_scans/quality.json",
        ]
        for path in candidates:
            if not repository.exists_bytes(survey_id, path):
                continue
            try:
                payload = json.loads(repository.get_bytes(survey_id, path).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("passes"):
                return payload
        return None
