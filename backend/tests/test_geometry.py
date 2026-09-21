import json

from backend.app.workflows.geometry import GeometryWorker
from backend.tests.test_survey_api import STRUCTURE


def test_geometry_worker_renders_svg_and_summary(tmp_path) -> None:
    structure_path = tmp_path / "roomplan" / "processed" / "structure.json"
    structure_path.parent.mkdir(parents=True)
    structure_path.write_bytes(STRUCTURE)

    result = GeometryWorker().process(tmp_path)

    assert result.room_count == 1
    assert result.wall_count == 2
    svg = result.svg_path.read_text(encoding="utf-8")
    assert "<svg" in svg
    assert 'data-wall="1"' in svg
    assert "#3B7BFF" in svg
    assert "N is scan +Z" in svg
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["floor_area_method"] == "axis_aligned_roomplan_bounds"
    assert summary["status"] == "estimated"
    assert summary["compass"] == "scan_+Z"
    assert summary["walls"][0] == {
        "index": 1,
        "length_cm": 400,
        "compass": "S",
        "color": "#3B7BFF",
    }
    assert summary["walls"][1]["compass"] == "E"
    assert summary["walls"][1]["length_cm"] == 300
