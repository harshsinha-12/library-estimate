from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssessmentPipeline(StrEnum):
    FABLE = "fable"
    ASTRA_REPLAY = "astra_replay"


class DamageAssessment(StrictModel):
    present: bool | None
    types: list[str]
    description: str | None = None


class IdentityCandidate(StrictModel):
    label: str = Field(min_length=1)
    identifier_type: str | None = None
    identifier_value: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)


class ModelAssessment(StrictModel):
    schema_version: Literal["1.0.0"]
    assessment_id: UUID
    survey_id: UUID
    asset_copy_id: str = Field(min_length=1)
    pipeline: AssessmentPipeline
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    evidence_package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    category: Literal[
        "book", "portrait", "painting", "cup", "furniture", "electronics", "other", "unknown"
    ]
    condition: Literal["new", "good", "worn", "damaged", "unknown"]
    damage: DamageAssessment
    identity_candidates: list[IdentityCandidate] = Field(max_length=5)
    recommended_action: Literal[
        "accept_candidate", "recapture", "alternate_resolver", "human_review"
    ]
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = Field(default=None, max_length=1000)
    evidence_refs: list[str] = Field(min_length=1)


class EvidencePackage(StrictModel):
    survey_id: UUID
    asset_copy_id: str
    package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    task: str = Field(min_length=1)


class AstraLiveQuality(StrictModel):
    blur: bool | None = None
    glare: bool | None = None
    readable: bool | None = None
    notes: str | None = Field(default=None, max_length=400)


class AstraLiveAssist(StrictModel):
    """Capture UX only. Never Pipeline B and never inventory truth."""

    schema_version: Literal["1.0.0"]
    assist_id: UUID
    survey_id: UUID
    pipeline: Literal["astra_live"]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    authority: Literal["assist_metadata"]
    capture_pass: Literal["B", "C"]
    quality: AstraLiveQuality
    provisional_count: int | None = Field(default=None, ge=0, le=500)
    unreadable_slots: list[str] = Field(default_factory=list, max_length=40)
    recapture_hint: str | None = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = Field(default=None, max_length=1000)


class ModelAssessmentProvider(Protocol):
    """Fable and Astra adapters must return this validated, shared shape."""

    pipeline: AssessmentPipeline

    async def assess(self, evidence: EvidencePackage) -> ModelAssessment: ...
