from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from backend.app.utils.json_codec import pretty_json


class GeometryError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class PortableSurface(StrictModel):
    identifier: str
    dimensions: list[float] = Field(min_length=3, max_length=3)
    transform: list[float] = Field(min_length=16, max_length=16)
    confidence: str = "unknown"
    parent_identifier: str | None = Field(
        default=None,
        validation_alias=AliasChoices("parent_identifier", "parentIdentifier"),
    )


class PortableRoom(StrictModel):
    identifier: str
    label: str
    walls: list[PortableSurface]
    doors: list[PortableSurface] = Field(default_factory=list)
    windows: list[PortableSurface] = Field(default_factory=list)
    openings: list[PortableSurface] = Field(default_factory=list)


class PortableStructure(StrictModel):
    format: str
    rooms: list[PortableRoom] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class GeometryResult:
    room_count: int
    wall_count: int
    svg_path: Path
    summary_path: Path
    usdz_path: Path | None


class GeometryWorker:
    structure_relative_path = "roomplan/processed/structure.json"
    usdz_relative_path = "roomplan/model.usdz"

    def process(self, package_root: Path) -> GeometryResult:
        structure_path = package_root / self.structure_relative_path
        if not structure_path.is_file():
            raise GeometryError(f"missing {self.structure_relative_path}")
        try:
            payload = json.loads(structure_path.read_text(encoding="utf-8"))
            structure = PortableStructure.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as error:
            raise GeometryError(f"invalid processed RoomPlan structure: {error}") from error

        from backend.app.workflows.floor_plan import TaggedFloorPlan, render_svg

        try:
            plan = TaggedFloorPlan.from_structure(structure)
        except ValueError as error:
            raise GeometryError(str(error)) from error

        # The sealed capture package may already contain generated/plan.svg in
        # its manifest. Worker output must never rewrite those hashed bytes.
        derived = package_root / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        svg_path = derived / "plan.svg"
        svg_path.write_text(render_svg(plan), encoding="utf-8")
        summary = plan.summary_dict()
        summary["source"] = self.structure_relative_path
        summary_path = derived / "geometry.json"
        summary_path.write_text(pretty_json(summary), encoding="utf-8")
        usdz_path = package_root / self.usdz_relative_path
        return GeometryResult(
            room_count=len(structure.rooms),
            wall_count=len(plan.walls),
            svg_path=svg_path,
            summary_path=summary_path,
            usdz_path=usdz_path if usdz_path.is_file() else None,
        )
