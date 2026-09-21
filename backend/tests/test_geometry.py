import json
from uuid import uuid4

import fakeredis

from backend.app.domain.repository import SurveyRepository
from backend.app.storage.objects import MemoryObjectStore
from backend.app.workflows.geometry import GeometryWorker
from backend.tests.test_survey_api import STRUCTURE


def test_geometry_worker_renders_svg_and_summary() -> None:
    store = MemoryObjectStore()
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True),
        store,
        key_prefix="ls:test:geometry",
    )
    survey_id = uuid4()
    store.put(
        f"{survey_id}/roomplan/processed/structure.json",
        STRUCTURE,
        "application/json",
    )
    store.put(f"{survey_id}/roomplan/model.usdz", b"fixture-usdz", "model/vnd.usdz+zip")

    result = GeometryWorker().process(repository, survey_id)

    assert result.room_count == 1
    assert result.wall_count == 2
    svg = store.get(f"{survey_id}/{result.svg_path}").decode("utf-8")
    assert "<svg" in svg
    assert 'data-wall="1"' in svg
    assert "#3B7BFF" in svg
    assert "N is scan +Z" in svg
    summary = json.loads(store.get(f"{survey_id}/{result.summary_path}").decode("utf-8"))
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
    assert result.usdz_path == "roomplan/model.usdz"
