from uuid import uuid4

import fakeredis

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now
from backend.app.workflows.report import build_report, build_report_snapshot


def test_report_keeps_citations_limit_and_spend_visible() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:report"
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Demo Library",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="partial", created_at=utc_now(), sealed_at=utc_now(), package_hash="a" * 64,
    ))
    repository.save_json(survey_id, "inventory", {
        "status": "partial", "asset_copies": [{"asset_copy_id": "copy-1"}],
    })
    repository.save_json(survey_id, "overview", {
        "copies": [{
            "asset_copy_id": "copy-1", "title": "A Book", "category": "book",
            "valuation_status": "quoted", "query": "9780000000000", "query_kind": "isbn",
            "isbn": "9780000000000", "reason": "Confirmed physical listing",
            "draft_count": 1,
            "valuation": {"amount": {"value": 800}, "currency": "INR"},
            "listing_url": "https://example.test/book",
            "evidence_refs": ["frame-1"],
        }, {
            "asset_copy_id": "ac-1", "label": "Window AC", "category": "appliance",
            "valuation_status": "price_pending", "query": None,
            "reason": "Needs Pass C photo", "draft_count": 0,
            "evidence_refs": ["closeups/ac.jpg"],
        }],
        "priced_eligible": {"numerator": 1, "denominator": 1},
        "city_market": "Bengaluru, IN · en-IN",
        "contents": {
            "currency": "INR", "low": 800, "central": 800, "high": 800, "status": "estimated",
        },
        "building": {
            "currency": "INR", "basis": "replacement_cost",
            "amount": {"value": 120000}, "floor_area": {"value": 12},
        },
    })
    report, pdf = build_report(repository, survey_id)
    assert report["spend"]["estimated_cost_usd"] == "0.000000"
    assert report["valuation"]["copies"][0]["evidence_refs"] == ["frame-1"]
    assert report["model_pipelines"]["fable"]["status"] == "not_run"
    assert report["model_pipelines"]["astra"]["status"] == "not_run"
    assert report["model_pipelines"]["jev"]["status"] == "not_run"
    assert report["valuation"]["copies"][1]["label"] == "Window AC"
    assert pdf.startswith(b"%PDF")
    assert repository.get_bytes(survey_id, "derived/report.pdf") == pdf


def test_report_snapshot_does_not_render_pdf() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True),
        MemoryObjectStore(),
        key_prefix="test:report-snapshot",
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Fast report",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="partial", created_at=utc_now(), sealed_at=utc_now(), package_hash="e" * 64,
    ))
    report = build_report_snapshot(repository, survey_id)
    assert report["survey_id"] == str(survey_id)
    assert repository.exists_bytes(survey_id, "derived/report.json")
    assert not repository.exists_bytes(survey_id, "derived/report.pdf")


def test_report_does_not_turn_unbound_searches_into_inventory() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:report"
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Spoken objects",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="partial", created_at=utc_now(), sealed_at=utc_now(), package_hash="b" * 64,
    ))
    repository.save_json(survey_id, "inventory", {"status": "partial", "asset_copies": []})
    repository.save_json(survey_id, "overview", {"copies": []})
    repository.save_json(survey_id, "pricing", {
        "live_searches": [
            {
                "title": "24-inch monitor",
                "query": "24-inch monitor Bareilly INR",
                "query_kind": "object",
                "category": "monitor",
                "status": "draft",
                "amount": 8999,
                "currency": "INR",
                "listing_url": "https://example.test/monitor",
                "reason": "Web search citations are drafts",
            },
            {
                "title": "M1 MacBook Air, base variant",
                "query": "M1 MacBook Air",
                "query_kind": "object",
                "category": "computer",
                "status": "draft",
                "amount": 65000,
                "currency": "INR",
                "listing_url": "https://example.test/macbook",
            },
        ]
    })
    report, pdf = build_report(repository, survey_id)
    assert report["valuation"]["copies"] == []
    assert len(report["valuation"]["live_searches"]) == 2
    assert pdf.startswith(b"%PDF")
    assert report["model_pipelines"]["fable"]["status"] == "not_run"


def test_report_collapses_duplicate_object_rows_and_labels_drafts() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:report"
    )
    survey_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Duplicates",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="partial", created_at=utc_now(), sealed_at=utc_now(), package_hash="c" * 64,
    ))
    repository.save_json(survey_id, "inventory", {
        "status": "partial",
        "asset_copies": [{"asset_copy_id": "found_881c98117be9"}],
    })
    copies = [
        {
            "asset_copy_id": "found_881c98117be9",
            "title": "Study table",
            "category": "furniture",
            "valuation_status": "price_pending",
            "query": '"Study table" paperback hardcover buy price',
            "query_kind": "name",
            "draft_count": 0,
        },
        {
            "asset_copy_id": "found_881c98117be9",
            "title": "Study table",
            "category": "furniture",
            "valuation_status": "price_pending",
            "query": "Study table (object)",
            "query_kind": "object",
            "draft_count": 1,
            "listing_url": "https://example.test/table",
            "valuation": {"amount": {"value": 2497}, "currency": "INR"},
            "reason": "Web search citations are drafts",
        },
    ]
    repository.save_json(survey_id, "overview", {"copies": copies})
    report, pdf = build_report(repository, survey_id)
    from backend.app.workflows.report import _unique_copy_rows

    rows = _unique_copy_rows(report["valuation"]["copies"])
    assert len(rows) == 1
    assert rows[0]["valuation"]["amount"]["value"] == 2497
    assert rows[0]["listing_url"] == "https://example.test/table"
    assert pdf.startswith(b"%PDF")


def test_report_reads_astra_replay_assessment() -> None:
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True), MemoryObjectStore(), key_prefix="test:report"
    )
    survey_id = uuid4()
    run_id = uuid4()
    repository.create(SurveyRecord(
        survey_id=survey_id, display_name="Replay",
        geography=SurveyGeography(
            country_code="IN", city="Bengaluru", currency="INR",
            market="en-IN", source="manual",
        ),
        status="partial", created_at=utc_now(), sealed_at=utc_now(), package_hash="d" * 64,
    ))
    repository.save_json(survey_id, "inventory", {"status": "partial", "asset_copies": []})
    repository.save_json(survey_id, "overview", {"copies": []})
    repository.save_json(survey_id, f"model-run:{run_id}", {
        "asset_copy_id": "copy-1",
        "assessments": {
            "fable": {"category": "book", "condition": "good", "confidence": 0.9},
            "astra_replay": {"category": "book", "condition": "good", "confidence": 0.91},
        },
        "jev": {"choice": "human_review", "confidence": 0.8},
        "decision": {"action": "human_review", "reason": "low_confidence"},
        "failures": {},
    })
    repository.redis.sadd(
        f"{repository.key_prefix}:survey:{survey_id}:model_runs", str(run_id)
    )
    report, _ = build_report(repository, survey_id)
    assert report["model_pipelines"]["fable"]["status"] == "ran"
    assert report["model_pipelines"]["astra"]["status"] == "ran"
    assert report["model_pipelines"]["jev"]["status"] == "ran"
