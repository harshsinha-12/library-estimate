from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.utils.paths import validate_package_path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type SurveyState = Literal[
    "created",
    "capturing",
    "uploading",
    "ingest_validation",
    "geometry",
    "partial",
    "recapture_required",
    "failed",
]


class SurveyGeography(StrictModel):
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    region: str | None = None
    city: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    market: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    source: Literal["gps", "manual", "mixed"]
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    precise_location_consent: bool = False

    @model_validator(mode="after")
    def precise_coordinates_require_consent(self) -> SurveyGeography:
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude:
            raise ValueError("latitude and longitude must be supplied together")
        if has_latitude and not self.precise_location_consent:
            raise ValueError("precise coordinates require explicit consent")
        return self


class SurveyCreate(StrictModel):
    survey_id: UUID | None = None
    geography: SurveyGeography
    display_name: str = Field(min_length=1, max_length=160)


class SurveyRecord(StrictModel):
    survey_id: UUID
    display_name: str
    geography: SurveyGeography
    status: SurveyState
    created_at: datetime
    sealed_at: datetime | None = None
    package_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AppIdentity(StrictModel):
    version: str = Field(min_length=1)
    build: str = Field(min_length=1)


class DeviceIdentity(StrictModel):
    model: str = Field(min_length=1)
    system_version: str = Field(min_length=1)
    supports_lidar: bool
    roomplan_version: str | None = None
    vision_version: str | None = None


class CaptureConsent(StrictModel):
    video: bool
    audio: bool
    location: bool
    retention_policy_id: str = Field(min_length=1)


class CaptureTiming(StrictModel):
    started_at: datetime
    ended_at: datetime
    monotonic_anchor_seconds: float = Field(ge=0)
    timezone: str = Field(min_length=1)

    @model_validator(mode="after")
    def end_must_follow_start(self) -> CaptureTiming:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self


class CaptureFile(StrictModel):
    path: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def path_must_be_safe_and_normalized(self) -> CaptureFile:
        validate_package_path(self.path)
        return self


class CapturePackageManifest(StrictModel):
    schema_version: Literal["1.0.0"]
    survey_id: UUID
    session_id: UUID
    app: AppIdentity
    device: DeviceIdentity
    geography: SurveyGeography
    consent: CaptureConsent
    timing: CaptureTiming
    capture_state: Literal["complete", "interrupted", "resumed", "manually_sealed"]
    capture_modes: list[Literal["room", "shelf", "exception"]] = Field(default_factory=list)
    files: list[CaptureFile] = Field(min_length=1)

    @model_validator(mode="after")
    def paths_must_be_unique(self) -> CapturePackageManifest:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest file paths must be unique")
        return self


class UploadedFile(StrictModel):
    path: str
    mime_type: str
    bytes: int
    sha256: str


class SealResult(StrictModel):
    survey_id: UUID
    status: SurveyState
    package_hash: str
    file_count: int
    sealed_at: datetime
    geometry_svg_path: str | None = None
    geometry_summary_path: str | None = None
    usdz_path: str | None = None


class SurveyStateEvent(StrictModel):
    sequence: int
    survey_id: UUID
    state: SurveyState
    occurred_at: datetime
    detail: str | None = None


class MeasurementInterval(StrictModel):
    low: float
    high: float
    level: float = Field(gt=0, le=1)


class Measurement(StrictModel):
    value: float | int
    unit: str
    status: Literal["ok", "partial", "failed", "needs_review"]
    confidence: float = Field(ge=0, le=1)
    interval: MeasurementInterval | None = None
    method: str
    evidence_refs: list[str] = Field(default_factory=list)
    run_id: str


class Observation(StrictModel):
    observation_id: str
    evidence_ref: str
    captured_at: datetime | None = None
    monotonic_seconds: float = 0
    category: str
    confidence: float = Field(ge=0, le=1)
    track_id: str | None = None
    asset_copy_id: str | None = None
    room_id: str | None = None
    shelf_id: str | None = None
    face_id: str | None = None
    row_id: str | None = None
    slot: int | None = None
    x: float | None = None
    face_normal: list[float] | None = None
    isbn: str | None = None
    pass_id: str | None = None


class Track(StrictModel):
    track_id: str
    observation_refs: list[str] = Field(min_length=1)
    status: Literal["ok", "partial", "failed", "needs_review"]
    face_id: str | None = None
    row_id: str | None = None


class AssetCopy(StrictModel):
    asset_copy_id: str
    category: str
    observation_refs: list[str]
    valuation_required: bool = True
    requires_appraisal: bool = False
    possibly_moved: bool = False
    book_edition_ref: str | None = None
    room_id: str | None = None
    shelf_id: str | None = None
    face_id: str | None = None
    row_id: str | None = None
    slot: int | None = None
    isbn: str | None = None


class ShelfFaceDataSize(StrictModel):
    shelf_face_id: str
    occupied_length: Measurement
    capacity_length: Measurement
    fill_ratio: Measurement
    copy_count: Measurement
    unresolved_count: Measurement
    evidence_bytes: Measurement
    rows: list[dict[str, object]] = Field(default_factory=list)


class ShelfOverlay(StrictModel):
    face_id: str
    label: str
    min_x: float
    min_z: float
    max_x: float
    max_z: float
    copy_count_label: str
    fill_label: str
    status: Literal["ok", "partial", "failed", "needs_review"]
    placement: Literal["operator", "unregistered"] = "unregistered"


class InventoryResult(StrictModel):
    survey_id: UUID
    status: Literal["ok", "partial", "failed", "needs_review"]
    pipeline_version: str
    run_id: str
    observations: list[Observation] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    asset_copies: list[AssetCopy] = Field(default_factory=list)
    shelf_face_data_sizes: list[ShelfFaceDataSize] = Field(default_factory=list)
    overlays: list[ShelfOverlay] = Field(default_factory=list)
    recapture: list[str] = Field(default_factory=list)


class PriceSearchRequest(StrictModel):
    survey_id: UUID


class LivePriceSearchRequest(StrictModel):
    isbn: str | None = None
    title: str | None = None
    barcode: str | None = None
    ocr_text: str | None = None
    identifier_kind: str | None = None
    category: str | None = None
    spoken_text: str | None = None
    image_base64: str | None = None
    asset_copy_id: str | None = None


class AstraLiveRequest(StrictModel):
    capture_pass: Literal["B", "C"] = "B"
    image_base64: str | None = None
    quality_messages: list[str] = Field(default_factory=list)
    provisional_count: int | None = None
    unreadable_slots: list[str] = Field(default_factory=list)
    blur: float | None = None
    glare: float | None = None
    recapture_rows: list[str] = Field(default_factory=list)


class PriceQueueRequest(StrictModel):
    pass


class PriceObservationWrite(StrictModel):
    survey_id: UUID
    action: Literal["confirm", "reject", "manual", "no_comparable"]
    price_observation_id: str | None = None
    amount: float | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    source_url: str | None = None
    reason: str | None = None
    condition: str | None = None
    format: str | None = None
    query: str | None = None
