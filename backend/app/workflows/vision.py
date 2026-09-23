from __future__ import annotations

import json
from collections.abc import Callable
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
from cv.library_vision.yolo_spines import (
    SpineCrop,
    crop_path,
    detections_path,
    labeled_pass_from_spines,
    merge_yolo_into_labeled,
    overlay_path,
    render_overlay_jpeg,
    segment_book_spines,
    sort_spines_left_to_right,
)


def vision_job_key(
    survey_id: UUID, input_hash: str, pipeline_version: str = PIPELINE_VERSION
) -> str:
    return f"{survey_id}:vision:{input_hash}:{pipeline_version}"


class VisionWorker:
    def __init__(
        self,
        segment_spines: Callable[..., list[SpineCrop]] | None = None,
    ) -> None:
        self.segment_spines = segment_spines or segment_book_spines

    def process(self, repository: SurveyRepository, survey_id: UUID) -> InventoryResult | None:
        labeled = self._load_labeled(repository, survey_id)
        labeled = self._apply_yolo(repository, survey_id, labeled)
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

    def _apply_yolo(
        self, repository: SurveyRepository, survey_id: UUID, labeled: dict | None
    ) -> dict | None:
        stored = _load_yolo_detections(repository, survey_id)
        if stored is not None:
            return merge_yolo_into_labeled(labeled, list(stored.get("passes") or []))
        frames = _shelf_frame_paths(repository, survey_id)
        if not frames:
            return labeled
        yolo_passes: list[dict] = []
        stored_crops: list[dict] = []
        for index, path in enumerate(frames[:8]):
            try:
                jpeg = repository.get_bytes(survey_id, path)
            except FileNotFoundError:
                continue
            try:
                spines = self.segment_spines(jpeg, source_path=path)
            except TypeError:
                try:
                    spines = self.segment_spines(jpeg)
                except Exception:
                    continue
            except Exception:
                continue
            if not spines:
                continue
            spines = sort_spines_left_to_right(list(spines))
            row_id = _row_hint(labeled, index)
            for spine in spines:
                dest = crop_path(row_id, spine.slot)
                if spine.jpeg:
                    repository.put_bytes(survey_id, dest, spine.jpeg, "image/jpeg")
                stored_crops.append({**spine.meta(), "path": dest, "row_id": row_id})
            try:
                overlay = render_overlay_jpeg(jpeg, spines, match_source=False)
            except Exception:
                overlay = b""
            if overlay:
                repository.put_bytes(
                    survey_id, overlay_path(path), overlay, "image/jpeg"
                )
            yolo_passes.append(
                labeled_pass_from_spines(
                    spines,
                    frame_path=path,
                    pass_id=f"yolo_{index}",
                    row_id=row_id,
                    t=float(index + 1),
                    **_geometry_hint(labeled),
                )
            )
        if not yolo_passes:
            return labeled
        repository.put_bytes(
            survey_id,
            detections_path(),
            canonical_json_bytes(
                {
                    "schema_version": "1.0.0",
                    "pipeline": "yolo11x-seg-book-spines",
                    "count_source": "yolo",
                    "recognizer": "fable_astra_jev",
                    "moondream2": False,
                    "frames": frames[:8],
                    "crops": stored_crops,
                    "passes": yolo_passes,
                }
            ),
            "application/json",
        )
        return merge_yolo_into_labeled(labeled, yolo_passes)


def _load_yolo_detections(repository: SurveyRepository, survey_id: UUID) -> dict | None:
    path = detections_path()
    if not repository.exists_bytes(survey_id, path):
        return None
    try:
        payload = json.loads(repository.get_bytes(survey_id, path).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, FileNotFoundError):
        return None
    return payload if isinstance(payload, dict) and payload.get("passes") else None


def _shelf_frame_paths(repository: SurveyRepository, survey_id: UUID) -> list[str]:
    paths: list[str] = []
    for path in sorted(repository.uploads(survey_id)):
        lower = path.lower()
        if lower.startswith("shelf_scans/frames/") and lower.endswith((".jpg", ".jpeg", ".png")):
            paths.append(path)
    return paths


def _row_hint(labeled: dict | None, index: int) -> str:
    if isinstance(labeled, dict):
        for scan in labeled.get("passes") or []:
            rows = [row for row in (scan.get("rows") or []) if isinstance(row, dict)]
            if not rows:
                continue
            return str(rows[min(index, len(rows) - 1)].get("row_id") or "row_01")
    return "row_01"


def _geometry_hint(labeled: dict | None) -> dict:
    if not isinstance(labeled, dict):
        return {}
    for scan in labeled.get("passes") or []:
        if not isinstance(scan, dict):
            continue
        normal = scan.get("face_normal") or (0.0, 0.0, 1.0)
        return {
            "room_id": str(scan.get("room_id") or "room"),
            "shelf_id": str(scan.get("shelf_id") or "shelf"),
            "face_id": str(scan.get("face_id") or "shelf.face_A"),
            "face_normal": tuple(normal) if len(tuple(normal)) == 3 else (0.0, 0.0, 1.0),
        }
    return {}
