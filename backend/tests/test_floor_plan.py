import json
from pathlib import Path

from backend.app.workflows.floor_plan import TaggedFloorPlan, render_svg
from backend.app.workflows.geometry import PortableStructure


def test_tagged_plan_numbers_openings_and_shoelace_area() -> None:
    structure = PortableStructure.model_validate(
        {
            "format": "library-roomplan-1.0",
            "rooms": [
                {
                    "identifier": "room-1",
                    "label": "Library",
                    "walls": [
                        {
                            "identifier": "north",
                            "dimensions": [4, 2.97, 0],
                            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 0, 3, 1],
                        },
                        {
                            "identifier": "east",
                            "dimensions": [3, 2.97, 0],
                            "transform": [0, 0, 1, 0, 0, 1, 0, 0, -1, 0, 0, 0, 4, 0, 1.5, 1],
                        },
                        {
                            "identifier": "south",
                            "dimensions": [4, 2.97, 0],
                            "transform": [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 2, 0, 0, 1],
                        },
                        {
                            "identifier": "west",
                            "dimensions": [3, 2.97, 0],
                            "transform": [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1.5, 1],
                        },
                    ],
                    "doors": [
                        {
                            "identifier": "door-1",
                            "parentIdentifier": "north",
                            "dimensions": [0.76, 2.1, 0],
                            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 0, 3, 1],
                        }
                    ],
                    "windows": [
                        {
                            "identifier": "window-1",
                            "parent_identifier": "west",
                            "dimensions": [0.95, 1.2, 0],
                            "transform": [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1.5, 1],
                        }
                    ],
                    "openings": [],
                }
            ],
        }
    )
    plan = TaggedFloorPlan.from_structure(structure)
    assert [wall.compass for wall in plan.walls] == ["N", "E", "S", "W"]
    assert plan.walls[0].length_cm == 400
    assert plan.ceiling_height_m == 2.97
    assert plan.floor_area_method == "shoelace_wall_polygon"
    assert abs(plan.floor_area_m2 - 12.0) < 0.01
    assert plan.openings[0].kind == "door"
    assert plan.openings[0].wall_index == 1
    assert plan.openings[0].length_cm == 76
    assert plan.openings[1].kind == "window"
    assert plan.openings[1].wall_index == 4
    svg = render_svg(plan)
    assert "Door 1  76 cm · on wall 1" in svg
    assert "Window 1  95 cm · on wall 4" in svg
    assert 'data-door="1"' in svg
    assert 'data-window="1"' in svg


def test_existing_home_capture_keeps_wall_lengths() -> None:
    structure_path = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "runtime"
        / "uploads"
        / "69d0a6d6-6ded-4280-bc07-eca057aa3f80"
        / "roomplan"
        / "processed"
        / "structure.json"
    )
    if not structure_path.is_file():
        return
    payload = json.loads(structure_path.read_text(encoding="utf-8"))
    plan = TaggedFloorPlan.from_structure(PortableStructure.model_validate(payload))
    assert [wall.length_cm for wall in plan.walls] == [368, 205, 196, 131, 61]
    assert plan.ceiling_height_m == 2.928


def test_shelf_overlay_legend_marks_unregistered_footprints() -> None:
    from backend.app.domain.models import ShelfOverlay

    structure = PortableStructure.model_validate(
        {
            "format": "library-roomplan-1.0",
            "rooms": [
                {
                    "identifier": "room-1",
                    "label": "Library",
                    "walls": [
                        {
                            "identifier": "north",
                            "dimensions": [4, 2.97, 0],
                            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 0, 3, 1],
                        },
                        {
                            "identifier": "east",
                            "dimensions": [3, 2.97, 0],
                            "transform": [0, 0, 1, 0, 0, 1, 0, 0, -1, 0, 0, 0, 4, 0, 1.5, 1],
                        },
                        {
                            "identifier": "south",
                            "dimensions": [4, 2.97, 0],
                            "transform": [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 2, 0, 0, 1],
                        },
                        {
                            "identifier": "west",
                            "dimensions": [3, 2.97, 0],
                            "transform": [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1.5, 1],
                        },
                    ],
                    "doors": [],
                    "windows": [],
                    "openings": [],
                }
            ],
        }
    )
    plan = TaggedFloorPlan.from_structure(structure)
    overlay = ShelfOverlay(
        face_id="shelf_01.face_A",
        label="Shelf 1",
        min_x=0.4,
        min_z=0.3,
        max_x=1.6,
        max_z=0.7,
        copy_count_label="8 copies",
        fill_label="live assist",
        status="partial",
    )
    svg = render_svg(plan, overlays=[overlay])
    assert "unregistered overlay" in svg
    assert 'data-shelf="shelf_01.face_A"' in svg
