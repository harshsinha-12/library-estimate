from __future__ import annotations

import base64
import json
from uuid import UUID

from fastapi.testclient import TestClient

from backend.app.providers.catalog.chain import CatalogChain
from backend.app.providers.pricing.parse import (
    classify_offer,
    is_shop_url,
    parse_prices,
    parse_spoken_cost,
)
from backend.app.providers.pricing.queries import (
    template_query,
    title_from_ocr,
    titles_from_ocr,
)
from backend.app.providers.pricing.schema import BATCH_SIZE, MAX_LISTING_URLS, PRICE_SCHEMA
from backend.app.providers.pricing.small_model import completion_body
from backend.app.providers.pricing.web_search import _parse_output, citations_from_schema
from backend.app.workflows.pricing import PricingWorker, load_rebuild_rates
from backend.tests.conftest import isolated_app
from backend.tests.test_survey_api import STRUCTURE, headers, manifest

TITLES = {
    "9780132350884": "Clean Code",
    "9780061122415": "The Alchemist",
    "9780143127550": "The Example Book",
}


def test_luna_completion_omits_temperature() -> None:
    luna = completion_body(
        "gpt-5.6-luna",
        messages=[],
        response_format={"type": "json_object"},
    )
    assert "temperature" not in luna
    mini = completion_body(
        "gpt-4o-mini",
        messages=[],
        response_format={"type": "json_object"},
    )
    assert mini["temperature"] == 0


def test_vision_model_defaults_to_luna(monkeypatch) -> None:
    from backend.app.providers.pricing.vision_titles import vision_model

    monkeypatch.delenv("OPENAI_VISION_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_SMALL_MODEL", "gpt-5.6-luna")
    assert vision_model() == "gpt-5.6-luna"


def test_luna_vision_omits_temperature(monkeypatch) -> None:
    from io import BytesIO

    from backend.app.providers.pricing.vision_titles import extract_book_titles

    captured: dict = {}

    class _Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):
        del timeout
        captured["body"] = json.loads(request.data.decode())
        payload = {
            "choices": [{"message": {"content": json.dumps({"books": []})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "model": "gpt-5.6-luna",
        }
        return _Response(json.dumps(payload).encode())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_VISION_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(
        "backend.app.providers.pricing.vision_titles.urlopen",
        fake_urlopen,
    )
    extract_book_titles(b"\xff\xd8fakejpeg", model="gpt-5.6-luna")
    assert captured["body"]["model"] == "gpt-5.6-luna"
    assert "temperature" not in captured["body"]


def _web_result(title: str, amount: str, *, currency: str = "INR", url: str | None = None) -> dict:
    listing = url or "https://www.amazon.in/dp/example"
    return {
        "query": title,
        "market": "en-IN",
        "listing_url": listing,
        "citations": [
            {
                "title": f"{title} paperback",
                "url": listing,
                "snippet": f"paperback {amount}",
                "offer_type": "physical",
                "parsed_amount": amount,
                "parsed_currency": currency,
                "format": "paperback",
                "condition": "new",
            },
            {
                "title": f"{title} Kindle",
                "url": "https://example.test/kindle",
                "snippet": "eBook rental bundle ₹199",
                "offer_type": "ebook",
                "parsed_amount": "199",
                "parsed_currency": currency,
                "format": "ebook",
                "condition": "new",
            },
        ],
        "listing_urls": [listing],
        "html_sha256": "abc",
        "listing_count": 1,
        "parser": "openai_web_search",
        "small_model": "gpt-5.5",
    }


def fake_web_search(title, **kwargs):
    geography = kwargs.get("geography") or {}
    isbn = str(kwargs.get("isbn") or "")
    kind = str(kwargs.get("kind") or "")
    description = str(kwargs.get("description") or "")
    currency = geography.get("currency") or "INR"
    country = geography.get("country_code") or "IN"
    blob = f"{title} {isbn} {description} {kind}".lower()
    url = {
        "IT": "https://www.amazon.it/dp/example",
        "JP": "https://www.amazon.co.jp/dp/example",
    }.get(country, "https://www.amazon.in/dp/example")
    if "9780132350884" in blob or "clean code" in blob:
        amount = {"IT": "31.99", "JP": "2640"}.get(country, "825")
        return _web_result("Clean Code", amount, currency=currency, url=url)
    if "9780061122415" in blob or "alchemist" in blob:
        return _web_result("The Alchemist", "399", currency=currency, url=url)
    if "9780143127550" in blob or "example book" in blob:
        return _web_result("The Example Book", "450", currency=currency, url=url)
    if "distant shore" in blob:
        return _web_result("Distant Shore", "275", currency=currency, url=url)
    if "handbook" in blob:
        return _web_result(
            title or "Python Data Science Handbook",
            "1750",
            currency=currency,
            url=url,
        )
    if "large language" in blob:
        return _web_result(
            title or "Introduction to Large Language Models",
            "762",
            currency=currency,
            url=url,
        )
    if "deep learning" in blob:
        return _web_result(title or "Deep Learning", "2100", currency=currency, url=url)
    if kind == "object" or "appliance" in blob or " ac" in blob:
        return _web_result(title or "split air conditioner", "25000", currency=currency, url=url)
    if not str(title or "").strip():
        return None
    return _web_result(title, "100", currency=currency, url=url)


def bind_web_search(worker, search_fn) -> None:
    worker.web_search = search_fn
    worker.web_search_batch = lambda items, geography: [
        search_fn(
            item.get("title"),
            geography=geography,
            isbn=item.get("isbn"),
            kind=item.get("kind"),
            description=item.get("description"),
        )
        for item in items
    ]


def stub_pricing(worker, *, titles: list[str] | None = None) -> None:
    bind_web_search(worker, fake_web_search)
    worker.plan_targets = lambda *args, **kwargs: []
    if titles is not None:
        worker.extract_titles = lambda jpeg, titles=titles: titles if jpeg else []


def fake_resolve(self, repository, survey_id, isbn, *, title=None):
    del self, repository, survey_id, title
    return {
        "status": "candidate",
        "source": "open_library",
        "source_record_id": f"/books/{isbn}",
        "isbn": isbn,
        "title": TITLES[isbn],
        "authors": ["A. Writer"],
        "publisher": "Demo Press",
        "edition": None,
        "work_refs": [f"/works/{isbn}"],
    }


def fake_resolve_title(self, repository, survey_id, title, author=""):
    del self, repository, survey_id
    return {
        "status": "candidate_needs_edition_review",
        "query_title": title,
        "candidates": [
            {"source": "open_library", "title": title, "authors": [author or "A. Writer"]}
        ],
    }


def _spines() -> list[dict]:
    specs = [
        (0, 0.10, "9780132350884"),
        (1, 0.28, "9780132350884"),
        (2, 0.46, "9780061122415"),
        (3, 0.64, None),
        (4, 0.82, None),
        (5, 1.00, "9780143127550"),
        (6, 1.18, None),
        (7, 1.36, None),
        (8, 1.54, None),
    ]
    return [
        {
            "slot": slot,
            "x": x,
            "t": 1.0 + slot * 0.1,
            "isbn": isbn,
            "appearance": f"spine-{slot}",
            "evidence_ref": f"frame_a_{slot}",
        }
        for slot, x, isbn in specs
    ]


def row_package() -> tuple[dict, list, dict]:
    spines = _spines()
    shelf = {
        "passes": [
            {
                "pass_id": "pass_a",
                "room_id": "library",
                "shelf_id": "shelf_01",
                "face_id": "shelf_01.face_A",
                "face_normal": [0, 0, 1],
                "label": "Shelf 1 A",
                "min_x": 0.4,
                "min_z": 0.3,
                "max_x": 1.6,
                "max_z": 0.7,
                "capacity_m": 1.2,
                "evidence_bytes": 412000,
                "rows": [
                    {
                        "row_id": "row_01",
                        "coverage": 0.96,
                        "capacity_m": 0.9,
                        "actual_count": 9,
                        "spines": spines,
                    },
                    {"row_id": "row_02", "coverage": 0.18, "capacity_m": 0.9, "spines": []},
                ],
            },
            {
                "pass_id": "pass_a_reverse",
                "room_id": "library",
                "shelf_id": "shelf_01",
                "face_id": "shelf_01.face_A",
                "face_normal": [0, 0, 1],
                "label": "Shelf 1 A",
                "t": 9.0,
                "rows": [
                    {
                        "row_id": "row_01",
                        "coverage": 0.94,
                        "capacity_m": 0.9,
                        "actual_count": 9,
                        "spines": [
                            {
                                **spine,
                                "t": 9.0 + spine["slot"] * 0.05,
                                "evidence_ref": f"frame_r_{spine['slot']}",
                            }
                            for spine in reversed(spines[:4])
                        ],
                    }
                ],
            },
        ]
    }
    marks = [
        {
            "id": "p",
            "asset_copy_id": "portrait-1",
            "category": "portrait",
            "label": "Portrait of this person",
            "room_id": "library",
            "monotonic_seconds": 10.0,
            "evidence_ref": "closeups/portrait.jpg",
            "high_value": True,
        },
        {
            "id": "m",
            "asset_copy_id": "mug-1",
            "category": "cup",
            "label": "Blue mug",
            "room_id": "library",
            "monotonic_seconds": 13.0,
            "evidence_ref": "closeups/mug.jpg",
        },
        {
            "id": "ac",
            "asset_copy_id": "ac-1",
            "category": "appliance",
            "label": "Window AC",
            "room_id": "library",
            "monotonic_seconds": 20.0,
            "evidence_ref": "closeups/ac.jpg",
            "stated_cost": 25000,
            "stated_currency": "INR",
        },
        {
            "id": "table",
            "asset_copy_id": "table-1",
            "category": "furniture",
            "label": "Reading table",
            "room_id": "library",
            "monotonic_seconds": 21.0,
            "evidence_ref": "closeups/table.jpg",
        },
    ]
    pass_c = {
        "scans": [
            {
                "id": "s0",
                "kind": "barcode",
                "face_id": "shelf_01.face_A",
                "row_id": "row_01",
                "slot": 0,
                "barcode": "9780132350884",
                "evidence_ref": "closeups/isbn0.jpg",
            },
            {
                "id": "s1",
                "kind": "barcode",
                "face_id": "shelf_01.face_A",
                "row_id": "row_01",
                "slot": 1,
                "barcode": "9780132350884",
                "evidence_ref": "closeups/isbn1.jpg",
            },
            {
                "id": "s2",
                "kind": "barcode",
                "face_id": "shelf_01.face_A",
                "row_id": "row_01",
                "slot": 2,
                "barcode": "9780061122415",
                "evidence_ref": "closeups/isbn2.jpg",
            },
            {
                "id": "s3",
                "kind": "title_page",
                "face_id": "shelf_01.face_A",
                "row_id": "row_01",
                "slot": 3,
                "title": "The Example Book",
                "author": "A. Writer",
                "evidence_ref": "closeups/title3.jpg",
            },
            {
                "id": "s5",
                "kind": "barcode",
                "face_id": "shelf_01.face_A",
                "row_id": "row_01",
                "slot": 5,
                "barcode": "9780143127550",
                "evidence_ref": "closeups/isbn5.jpg",
            },
            {
                "id": "s6",
                "kind": "barcode",
                "face_id": "shelf_01.face_A",
                "row_id": "row_01",
                "slot": 6,
                "barcode": "9780143127551",
                "evidence_ref": "closeups/bad6.jpg",
            },
            {
                "id": "s7",
                "kind": "title_page",
                "face_id": "shelf_01.face_A",
                "row_id": "row_01",
                "slot": 7,
                "title": "Distant Shore",
                "author": "N. Author",
                "evidence_ref": "closeups/title7.jpg",
            },
        ],
        "notes": [
            {
                "id": "speech-portrait",
                "text": "See, there is a portrait of this person. It cost us $500.",
                "monotonic_seconds": 10.0,
                "tapped_asset_id": "portrait-1",
            },
            {
                "id": "speech-ac",
                "text": "This is an AC, this cost us 25000 rupees",
                "monotonic_seconds": 20.0,
                "tapped_asset_id": "ac-1",
            },
            {
                "id": "speech-table",
                "text": "This table cost us 8000 INR",
                "monotonic_seconds": 21.0,
                "tapped_asset_id": "table-1",
            },
        ],
        "focus_events": [
            {"id": "point-portrait", "asset_copy_id": "portrait-1", "monotonic_seconds": 10.0},
            {"id": "point-ac", "asset_copy_id": "ac-1", "monotonic_seconds": 20.0},
            {"id": "point-table", "asset_copy_id": "table-1", "monotonic_seconds": 21.0},
        ],
    }
    return shelf, marks, pass_c


def create_survey(client: TestClient, geography: dict | None = None) -> tuple[dict, dict]:
    geo = geography or {
        "country_code": "IN",
        "region": "Uttar Pradesh",
        "city": "Bareilly",
        "currency": "INR",
        "market": "en-IN",
        "source": "manual",
        "precise_location_consent": False,
    }
    response = client.post(
        "/v1/surveys",
        headers=headers("create-stage4"),
        json={"display_name": "Stage 4 Row", "geography": geo},
    )
    assert response.status_code == 201
    return response.json(), geo


def test_query_builder_and_physical_filter_are_local() -> None:
    isbn_query, kind = template_query(
        isbn="9780132350884",
        title="Clean Code",
        author=None,
        publisher=None,
        edition=None,
        country_code="IN",
    )
    assert kind == "isbn" and isbn_query.startswith("9780132350884")
    name_query, kind = template_query(
        isbn=None,
        title="The Example Book",
        author="A. Writer",
        publisher=None,
        edition=None,
        country_code="IT",
    )
    assert kind == "name" and "Italy" in name_query
    local_name, local_kind = template_query(
        isbn=None,
        title="Python Data Science Handbook",
        author=None,
        publisher=None,
        edition=None,
        country_code="IN",
        city="Bareilly",
        currency="INR",
    )
    assert local_kind == "name"
    assert "Python Data Science Handbook" in local_name
    assert "Bareilly" in local_name
    assert "INR" in local_name
    assert "price" in local_name.lower()
    assert "amazon.in" in local_name
    assert "flipkart.com" in local_name
    ocr_title = title_from_ocr(
        "INTRODUCTION TO\nLARGE LANGUAGE\nMODELS\nJay Alammar\nMaarten Grootendorst"
    )
    assert ocr_title is not None
    assert "LARGE LANGUAGE" in ocr_title
    assert title_from_ocr("12") is None
    names = titles_from_ocr(
        "Python Data Science Handbook\nJake VanderPlas\nINTRODUCTION TO\nLARGE LANGUAGE\nMODELS"
    )
    assert any("Python Data Science Handbook" in item for item in names)
    assert classify_offer("Clean Code Kindle", "eBook ₹199") == "ebook"
    assert classify_offer("The Example Book paperback", "Local listing ₹450") == "physical"
    assert classify_offer("Clean Code paperback", "Physical ₹825") == "physical"
    assert parse_prices("Local listing ₹825")[0] == (parse_prices("₹825")[0][0], "INR")
    spoken = parse_spoken_cost("This portrait cost us $500", default_currency="INR")
    assert spoken is not None and spoken[1] == "USD"
    rates = load_rebuild_rates()
    assert rates["table_id"] == "demo_rebuild_rates_v1"
    assert set(rates["rates"]) == {"IN", "IT", "JP"}
    assert BATCH_SIZE == 5
    assert MAX_LISTING_URLS == 5
    assert "citations" in PRICE_SCHEMA["properties"]
    assert is_shop_url("https://www.amazon.in/dp/123")
    assert is_shop_url("https://www.flipkart.com/book")
    assert not is_shop_url("https://en.wikipedia.org/wiki/Book")


def test_web_search_parses_responses_json() -> None:
    payload = {
        "output_text": json.dumps(
            {
                "citations": [
                    {
                        "title": "Python Data Science Handbook paperback",
                        "url": "https://www.amazon.in/dp/example",
                        "snippet": "₹1750 hardcover",
                        "offer_type": "physical",
                        "amount": 1750,
                        "currency": "INR",
                        "format": "paperback",
                        "condition": "new",
                    }
                ]
            }
        )
    }
    parsed = _parse_output(payload)
    citations = citations_from_schema(parsed, "INR")
    assert citations[0]["parsed_amount"] == "1750"
    assert citations[0]["offer_type"] == "physical"


def test_web_search_batch_chunks_unique_items(monkeypatch) -> None:
    from backend.app.providers.pricing import web_search as module

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen: list[int] = []

    def fake_chunk(items, *, geography, key):
        del geography, key
        seen.append(len(items))
        return [{"query": item["title"]} for item in items]

    monkeypatch.setattr(module, "_batch_chunk", fake_chunk)
    items = [{"title": f"Book {index}"} for index in range(12)]
    rows = module.search_price_batch(items, geography={"country_code": "IN"})
    assert seen == [5, 5, 2]
    assert len(rows) == 12


def test_live_price_search_uses_web_search_when_priced() -> None:
    app, _, _ = isolated_app()
    stub_pricing(app.state.survey_workflow.pricing_worker)
    bind_web_search(
        app.state.survey_workflow.pricing_worker,
        lambda title, **kwargs: {
            "query": title,
            "market": "en-IN",
            "listing_url": "https://www.amazon.in/dp/example",
            "citations": [
                {
                    "title": title,
                    "url": "https://www.amazon.in/dp/example",
                    "snippet": "paperback ₹1750",
                    "offer_type": "physical",
                    "parsed_amount": "1750",
                    "parsed_currency": "INR",
                    "format": "paperback",
                    "condition": "new",
                }
            ],
            "listing_urls": ["https://www.amazon.in/dp/example"],
            "html_sha256": "abc",
            "listing_count": 1,
            "parser": "openai_web_search",
            "small_model": "gpt-5.5",
        },
    )
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        body = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={"title": "Python Data Science Handbook"},
        ).json()
        assert body["status"] == "draft"
        assert body["amount"] == "1750"
        assert body["parser"] == "openai_web_search"
        overview = client.get(f"/v1/surveys/{survey_id}/overview").json()
        found = overview.get("found_prices") or []
        assert found and found[0]["amount"] == "1750"


def test_live_price_search_sends_image_description() -> None:
    app, _, _ = isolated_app()
    stub_pricing(app.state.survey_workflow.pricing_worker)
    captured: dict = {}

    def fake_search(title, **kwargs):
        captured["title"] = title
        captured["description"] = kwargs.get("description")
        captured["kind"] = kwargs.get("kind")
        return {
            "query": title,
            "market": "en-IN",
            "listing_url": "https://www.amazon.in/dp/example",
            "citations": [
                {
                    "title": title,
                    "url": "https://www.amazon.in/dp/example",
                    "snippet": "paperback ₹2100",
                    "offer_type": "physical",
                    "parsed_amount": "2100",
                    "parsed_currency": "INR",
                    "format": "hardcover",
                    "condition": "new",
                }
            ],
            "listing_urls": ["https://www.amazon.in/dp/example"],
            "html_sha256": "abc",
            "listing_count": 1,
            "parser": "openai_web_search",
            "small_model": "gpt-5.5",
        }

    worker = app.state.survey_workflow.pricing_worker
    bind_web_search(worker, fake_search)
    worker.extract_titles = lambda jpeg: ["Deep Learning"]
    jpeg = base64.b64encode(b"fake-jpeg").decode("ascii")
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        body = client.post(
            f"/v1/surveys/{survey['survey_id']}/live-price-search",
            json={"ocr_text": "Ian Goodfellow", "image_base64": jpeg},
        ).json()
    assert captured["title"] == "Deep Learning"
    assert "Deep Learning" in (captured["description"] or "")
    assert "Ian Goodfellow" in (captured["description"] or "")
    assert body["amount"] == "2100"
    assert body["parser"] == "openai_web_search"


def test_frames_align_to_speech_window() -> None:
    from backend.app.providers.pricing.targets import (
        fallback_targets,
        format_timeline,
        frames_in_span,
    )

    visuals = [
        {"t": 10.0, "path": "roomplan/raw/frames/0001.jpg", "kind": "video"},
        {"t": 20.0, "path": "roomplan/raw/frames/0002.jpg", "kind": "video"},
        {"t": 30.0, "path": "roomplan/raw/frames/0003.jpg", "kind": "video"},
    ]
    matched = frames_in_span(visuals, 19.5, 21.2)
    assert matched[0]["path"].endswith("0002.jpg")
    notes = [
        {
            "text": "there is an AC",
            "monotonic_seconds": 19.6,
            "ended_monotonic_seconds": 21.0,
        },
        {
            "text": "there is an AC again",
            "monotonic_seconds": 20.4,
            "ended_monotonic_seconds": 21.5,
        },
    ]
    timeline = format_timeline(notes, visuals)
    assert "0002.jpg" in timeline
    assert "0001.jpg" not in timeline
    targets = fallback_targets(notes, visuals)
    assert len(targets) == 1
    assert targets[0]["category"] == "appliance"
    assert targets[0]["frame_path"].endswith("0002.jpg")


def test_live_price_search_stops_after_price_and_caps_retries(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, _ = isolated_app()
    calls = {"n": 0}

    def fake_search(title, **kwargs):
        calls["n"] += 1
        if "Handbook" in (title or ""):
            return {
                "query": title,
                "market": "en-IN",
                "listing_url": "https://www.amazon.in/dp/example",
                "citations": [
                    {
                        "title": title,
                        "url": "https://www.amazon.in/dp/example",
                        "snippet": "₹1750",
                        "offer_type": "physical",
                        "parsed_amount": "1750",
                        "parsed_currency": "INR",
                        "format": "paperback",
                        "condition": "new",
                    }
                ],
                "listing_urls": ["https://www.amazon.in/dp/example"],
                "html_sha256": "abc",
                "listing_count": 1,
                "parser": "openai_web_search",
                "small_model": "gpt-5.5",
            }
        return {
            "query": title,
            "market": "en-IN",
            "listing_url": "",
            "citations": [],
            "listing_urls": [],
            "html_sha256": "abc",
            "listing_count": 0,
            "parser": "openai_web_search",
            "small_model": "gpt-5.5",
        }

    worker = app.state.survey_workflow.pricing_worker
    bind_web_search(worker, fake_search)
    worker.plan_targets = lambda *args, **kwargs: []
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        first = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={"title": "Python Data Science Handbook"},
        ).json()
        second = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={"title": "Python Data Science Handbook"},
        ).json()
        priced_calls = calls["n"]
        assert first["amount"] == "1750"
        assert second["amount"] == "1750"
        assert priced_calls == 1
        for _ in range(6):
            client.post(
                f"/v1/surveys/{survey_id}/live-price-search",
                json={"title": "Unknown Spine Title That Will Not Match"},
            )
        assert calls["n"] - priced_calls == 5


def test_spoken_pricing_skips_unidentified_book_placeholders() -> None:
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    worker.plan_targets = lambda *args, **kwargs: [
        {
            "name": "Book with unrecorded ISBN",
            "kind": "book",
            "category": "book",
            "description": "unresolved shelf item",
        }
    ]
    bind_web_search(
        worker,
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("generic placeholder must not trigger web search")
        ),
    )
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = UUID(survey["survey_id"])
        app.state.survey_workflow.repository.save_json(
            survey_id,
            "stage3",
            {
                "assets": [],
                "identities": [],
                "queue": [],
                "notes": [{
                    "text": "There is a book near the ISBN mention",
                    "monotonic_seconds": 1.0,
                }],
            },
        )
        result = worker.price_spoken_notes(
            app.state.survey_workflow.repository, survey_id
        )
    assert result["searches"] == []


def test_stage4_row_pricing_gate(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(CatalogChain, "resolve", fake_resolve)
    monkeypatch.setattr(CatalogChain, "resolve_title", fake_resolve_title)
    shelf, marks, pass_c = row_package()
    uploads = [
        ("roomplan/processed/structure.json", "application/json", STRUCTURE),
        ("roomplan/model.usdz", "model/vnd.usdz+zip", b"fixture-usdz"),
        ("shelf_scans/labeled.json", "application/json", json.dumps(shelf).encode()),
        ("other_assets/marks.json", "application/json", json.dumps(marks).encode()),
        ("exceptions/pass-c.json", "application/json", json.dumps(pass_c).encode()),
        ("closeups/portrait.jpg", "image/jpeg", b"portrait-bytes"),
        ("closeups/ac.jpg", "image/jpeg", b"ac-bytes"),
        ("closeups/table.jpg", "image/jpeg", b"table-bytes"),
    ]
    app, _, _ = isolated_app()
    stub_pricing(app.state.survey_workflow.pricing_worker)
    with TestClient(app) as client:
        survey, geo = create_survey(client)
        survey_id = survey["survey_id"]
        for path, mime, data in uploads:
            assert (
                client.post(
                    f"/v1/surveys/{survey_id}/uploads",
                    params={"path": path},
                    headers={**headers("upload-" + path), "Content-Type": mime},
                    content=data,
                ).status_code
                == 200
            )
        sealed = manifest(survey_id, uploads)
        sealed["geography"] = geo
        sealed["capture_modes"] = ["room", "shelf", "exception"]
        assert (
            client.post(
                f"/v1/surveys/{survey_id}/seal", headers=headers("seal-stage4"), json=sealed
            ).status_code
            == 200
        )
        overview = client.get(f"/v1/surveys/{survey_id}/overview").json()
        books = [item for item in overview["copies"] if item["category"] == "book"]
        assert len(books) == 9
        assert len({item["asset_copy_id"] for item in books}) == 9
        row = next(item for item in overview["rows"] if item["row_id"] == "row_01")
        assert row["detected_count"] == 9
        assert row["actual_count"] == 9
        assert row["detected_actual"] == {"numerator": 9, "denominator": 9}
        missed = next(item for item in overview["rows"] if item["row_id"] == "row_02")
        assert missed["recapture"] is True
        assert missed["detected_count"] == 0
        isbn_copies = [item for item in books if item["isbn"] == "9780132350884"]
        assert len(isbn_copies) == 2
        assert isbn_copies[0]["asset_copy_id"] != isbn_copies[1]["asset_copy_id"]
        unread = [item for item in books if item["slot"] in {4, 8}]
        assert all(item["identity_task"] for item in unread)
        assert all(item["valuation_status"] == "price_pending" for item in unread)
        invalid = next(item for item in books if item["slot"] == 6)
        assert invalid["isbn"] is None
        assert invalid["query"] is None or not str(invalid["query"]).startswith("9780143127551")
        mug = next(item for item in overview["copies"] if item["asset_copy_id"] == "mug-1")
        assert mug["excluded"] is True
        assert mug["eligible"] is False
        portrait = next(
            item for item in overview["copies"] if item["asset_copy_id"] == "portrait-1"
        )
        assert portrait["requires_appraisal"] is True
        assert portrait["valuation_status"] == "price_pending"
        assert "draft" in portrait["reason"].lower() or portrait["draft_count"] >= 1
        building = overview["building"]
        assert building["basis"] == "replacement_cost"
        assert building["rate_table_version"] == "demo_rebuild_rates_v1"
        assert building["amount"]["value"] > 0
        assert building["floor_area"]["value"] > 0
        assert overview["contents"]["status"] == "unavailable"

        queued = client.post(f"/v1/surveys/{survey_id}/price-search-queue", json={}).json()
        assert queued["ledger"]["lines"]
        isbn_search = next(item for item in queued["queued"] if item["query_kind"] == "isbn")
        name_search = next(item for item in queued["queued"] if item["query_kind"] == "name")
        assert "amazon.in" in (isbn_search.get("listing_url") or "")
        assert "bing.com" not in (isbn_search.get("listing_url") or "")
        assert isbn_search.get("country_code") == "IN"
        assert len(isbn_search["listing_urls"]) <= 5
        assert "Example Book" in name_search["query"] or "Distant Shore" in name_search["query"]
        overview = client.get(f"/v1/surveys/{survey_id}/overview").json()
        eligible = [
            item
            for item in overview["copies"]
            if item["eligible"] and item["category"] == "book"
        ]
        searched = [item for item in eligible if item["query"]]
        assert searched
        assert all(item["valuation_status"] != "quoted" for item in searched)
        draft_copy = next(item for item in searched if item["query_kind"] == "isbn")
        evidence = client.post(
            f"/v1/assets/{draft_copy['asset_copy_id']}/price-search",
            json={"survey_id": survey_id},
        ).json()
        physical = next(
            item
            for item in evidence["drafts"]
            if item["offer_type"] == "physical" and item.get("parsed_amount")
        )
        ebook = next(item for item in evidence["drafts"] if item["offer_type"] == "ebook")
        confirm = client.post(
            f"/v1/assets/{draft_copy['asset_copy_id']}/price-observations",
            json={
                "survey_id": survey_id,
                "action": "confirm",
                "price_observation_id": physical["price_observation_id"],
                "condition": "used_good",
                "format": "paperback",
            },
        )
        assert confirm.status_code == 200
        blocked = client.post(
            f"/v1/assets/{draft_copy['asset_copy_id']}/price-observations",
            json={
                "survey_id": survey_id,
                "action": "confirm",
                "price_observation_id": ebook["price_observation_id"],
            },
        )
        assert blocked.status_code == 422
        sibling = next(
            item
            for item in isbn_copies
            if item["asset_copy_id"] != draft_copy["asset_copy_id"]
        )
        overview = client.get(f"/v1/surveys/{survey_id}/overview").json()
        confirmed = next(
            item
            for item in overview["copies"]
            if item["asset_copy_id"] == draft_copy["asset_copy_id"]
        )
        still_pending = next(
            item for item in overview["copies"] if item["asset_copy_id"] == sibling["asset_copy_id"]
        )
        assert confirmed["valuation_status"] == "quoted"
        assert still_pending["valuation_status"] != "quoted"
        name_copy = next(item for item in overview["copies"] if item["query_kind"] == "name")
        name_evidence = client.post(
            f"/v1/assets/{name_copy['asset_copy_id']}/price-search",
            json={"survey_id": survey_id},
        ).json()
        name_draft = next(
            item
            for item in name_evidence["drafts"]
            if item["offer_type"] == "physical" and item.get("parsed_amount")
        )
        assert (
            client.post(
                f"/v1/assets/{name_copy['asset_copy_id']}/price-observations",
                json={
                    "survey_id": survey_id,
                    "action": "confirm",
                    "price_observation_id": name_draft["price_observation_id"],
                },
            ).status_code
            == 200
        )
        ac = next(item for item in overview["copies"] if item["asset_copy_id"] == "ac-1")
        spoken_draft = next(
            item
            for item in client.get(f"/v1/surveys/{survey_id}/overview").json()["copies"]
            if item["asset_copy_id"] == "ac-1"
        )
        pricing = app.state.survey_workflow.repository.get_json(survey_id, "pricing")
        ac_draft = next(
            item
            for item in pricing["observations"]
            if item.get("asset_copy_id") == "ac-1" and item.get("query_kind") == "spoken"
        )
        assert (
            client.post(
                "/v1/assets/ac-1/price-observations",
                json={
                    "survey_id": survey_id,
                    "action": "confirm",
                    "price_observation_id": ac_draft["price_observation_id"],
                    "condition": "used_good",
                },
            ).status_code
            == 200
        )
        mug_search = client.post(
            "/v1/assets/mug-1/price-search",
            json={"survey_id": survey_id},
        ).json()
        assert mug_search["status"] == "excluded"
        overview = client.get(f"/v1/surveys/{survey_id}/overview").json()
        priced = overview["priced_eligible"]
        assert priced["numerator"] >= 3
        assert priced["denominator"] >= priced["numerator"]
        unread_after = [item for item in overview["copies"] if item.get("slot") in {4, 8}]
        assert all(item["valuation"] is None for item in unread_after)
        assert overview["contents"]["status"] == "estimated"
        assert "cap_usd" not in overview["ledger"]
        assert "spent_usd" not in overview["ledger"]
        assert any(line["kind"] == "openai_web_search" for line in overview["ledger"]["lines"])
        assert ac["eligible"] is True
        assert spoken_draft["draft_count"] >= 1 or ac_draft["parsed_amount"] == "25000"
        assert "saleInfo" not in json.dumps(overview)


def test_live_price_search_uses_book_name() -> None:
    app, _, _ = isolated_app()
    stub_pricing(app.state.survey_workflow.pricing_worker)
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        empty = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={"ocr_text": "12"},
        )
        assert empty.status_code == 200
        assert empty.json()["status"] == "unresolved"
        named = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={
                "ocr_text": "INTRODUCTION TO\nLARGE LANGUAGE\nMODELS\nJay Alammar",
            },
        )
        assert named.status_code == 200
        body = named.json()
        assert body["query_kind"] == "name"
        assert "LARGE LANGUAGE" in (body.get("title") or "")
        assert body["listing_count"] <= 5
        assert len(body.get("listing_urls") or []) <= 5
        assert body["status"] in {"draft", "unresolved"}
        if body["status"] == "draft":
            assert body["amount"]
        overview = client.get(f"/v1/surveys/{survey_id}/overview").json()
        assert any(item.get("event") == "live_book_search" for item in overview.get("log") or [])
        found = overview.get("found_prices") or []
        assert all(item.get("amount") for item in found)
        if body["status"] == "draft":
            assert found


def test_identify_from_frames_does_not_search_unbound_ocr_fragments(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, _ = isolated_app()
    stub_pricing(
        app.state.survey_workflow.pricing_worker,
        titles=["Python Data Science Handbook"],
    )
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        uploaded = client.post(
            f"/v1/surveys/{survey_id}/uploads",
            params={"path": "shelf_scans/frames/0001.jpg"},
            headers={**headers("upload-shelf-frame"), "Content-Type": "image/jpeg"},
            content=b"fake-jpeg-bytes",
        )
        assert uploaded.status_code == 200
        identified = client.post(f"/v1/surveys/{survey_id}/identify-and-price")
        assert identified.status_code == 200
        body = identified.json()
        assert body["titles"] == ["Python Data Science Handbook"]
        assert body["searches"] == []


def test_identify_prefers_tagged_crops(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    worker.extract_titles = lambda jpeg: (
        ["From Crop"] if jpeg == b"crop-bytes" else ["From Frame"]
    )
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        for path, body in (
            ("shelf_scans/frames/0001.jpg", b"frame-bytes"),
            ("shelf_scans/crops/row_01_slot0.jpg", b"crop-bytes"),
        ):
            assert (
                client.post(
                    f"/v1/surveys/{survey_id}/uploads",
                    params={"path": path},
                    headers={**headers("upload-" + path), "Content-Type": "image/jpeg"},
                    content=body,
                ).status_code
                == 200
            )
        identified = client.post(f"/v1/surveys/{survey_id}/identify-and-price")
        assert identified.status_code == 200
        assert identified.json()["titles"][0] == "From Crop"


def test_live_object_search_uses_spoken_label_and_image(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    worker.describe_object = lambda jpeg, spoken=None, category=None: {
        "label": "split air conditioner",
        "details": "wall mounted",
    }
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        named = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={
                "category": "appliance",
                "title": "AC",
                "spoken_text": "there is an AC",
                "image_base64": base64.b64encode(b"fake-jpeg").decode("ascii"),
            },
        )
        assert named.status_code == 200
        body = named.json()
        assert body["query_kind"] == "object"
        assert body["listing_count"] <= 5
        assert "AC" in (body.get("title") or "") or "air" in (body.get("title") or "").lower()
        assert body["status"] in {"draft", "unresolved"}


def test_identify_reads_later_roomplan_frames(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    worker.extract_titles = lambda jpeg: (
        ["Deep Learning with Python"] if jpeg == b"late-cover" else []
    )
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        for path, body in (
            ("roomplan/raw/frames/0001.jpg", b"early-wall"),
            ("roomplan/raw/frames/0068.jpg", b"late-cover"),
        ):
            uploaded = client.post(
                f"/v1/surveys/{survey_id}/uploads",
                params={"path": path},
                headers={**headers("upload-" + path), "Content-Type": "image/jpeg"},
                content=body,
            )
            assert uploaded.status_code == 200
        identified = client.post(f"/v1/surveys/{survey_id}/identify-and-price")
        assert identified.status_code == 200
        payload = identified.json()
        assert payload["titles"] == ["Deep Learning with Python"]
        assert payload["copy_count"] == 0


def test_overview_keeps_unbound_searches_out_of_inventory(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        named = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={
                "category": "monitor",
                "title": "24-inch monitor",
                "spoken_text": "this is a 24 inch monitor",
            },
        )
        assert named.status_code == 200
        overview = client.get(f"/v1/surveys/{survey_id}/overview")
        assert overview.status_code == 200
        copy_titles = [
            str(row.get("title") or row.get("label") or "")
            for row in overview.json().get("copies") or []
        ]
        assert not any("monitor" in title.lower() for title in copy_titles)
        assert any(
            "monitor" in str(row.get("title") or "").lower()
            for row in overview.json().get("live_searches") or []
        )


def test_queue_reuses_live_object_price_without_another_web_search(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    original = worker.web_search
    calls: list[str] = []

    def counted(title, **kwargs):
        calls.append(title)
        return original(title, **kwargs)

    worker.web_search = counted
    original_batch = worker.web_search_batch
    batch_calls: list[str] = []

    def counted_batch(items, geography):
        batch_calls.extend(str(item.get("title") or "") for item in items)
        return original_batch(items, geography=geography)

    worker.web_search_batch = counted_batch
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = survey["survey_id"]
        first = client.post(
            f"/v1/surveys/{survey_id}/live-price-search",
            json={
                "category": "computer",
                "title": "MacBook Air M1 base variant",
                "spoken_text": "MacBook Air M1 base variant",
            },
        )
        assert first.status_code == 200
        assert first.json()["amount"]
        queued = client.post(f"/v1/surveys/{survey_id}/price-search-queue")
        assert queued.status_code == 200
    assert "MacBook Air M1 base variant" in calls + batch_calls
    assert calls.count("MacBook Air M1 base variant") + batch_calls.count(
        "MacBook Air M1 base variant"
    ) == 1


def test_live_draft_enriches_captured_copy_with_numeric_amount() -> None:
    worker = PricingWorker()
    existing = [{
        "asset_copy_id": "copy-monitor",
        "category": "monitor",
        "title": "24-inch monitor",
        "label": "24-inch monitor",
        "query": None,
        "query_kind": None,
        "valuation": None,
        "valuation_status": "price_pending",
        "draft_count": 0,
    }]
    state = {
        "live_searches": [{
            "title": "24-inch monitor",
            "category": "monitor",
            "query_kind": "object",
            "query": "24-inch monitor",
            "status": "draft",
            "amount": "8990",
            "currency": "INR",
            "listing_url": "https://example.test/monitor",
        }]
    }
    assert worker._rows_from_searches(existing, state, {"currency": "INR"}) == []
    assert existing[0]["valuation"]["amount"]["value"] == 8990.0


def test_queue_batches_duplicate_titles_and_skips_already_priced() -> None:
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    batches: list[list[str]] = []
    original_batch = worker.web_search_batch

    def counted_batch(items, geography):
        batches.append([str(item.get("title") or "") for item in items])
        return original_batch(items, geography=geography)

    worker.web_search_batch = counted_batch
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = UUID(survey["survey_id"])
        repo = app.state.survey_workflow.repository
        copies = [
            {"asset_copy_id": "book-1", "category": "book"},
            {"asset_copy_id": "book-2", "category": "book"},
            {"asset_copy_id": "book-3", "category": "book"},
            {
                "asset_copy_id": "mac-1",
                "category": "computer",
                "label": "MacBook Air M1 base variant",
            },
        ]
        repo.save_json(survey_id, "inventory", {"asset_copies": copies, "status": "partial"})
        repo.save_json(
            survey_id,
            "stage3",
            {
                "assets": copies,
                "identities": [
                    {"asset_copy_id": "book-1", "title": "Clean Code"},
                    {"asset_copy_id": "book-2", "title": "Clean Code"},
                    {"asset_copy_id": "book-3", "title": "Clean Code"},
                    {"asset_copy_id": "mac-1", "title": "MacBook Air M1 base variant"},
                ],
                "queue": [],
                "notes": [],
            },
        )
        repo.save_json(
            survey_id,
            "pricing",
            {
                "observations": [],
                "searches": [],
                "live_searches": [
                    {
                        "title": "MacBook Air M1 base variant",
                        "query_kind": "object",
                        "category": "computer",
                        "amount": 65000,
                        "currency": "INR",
                        "listing_url": "https://example.test/macbook",
                    }
                ],
                "found_prices": [],
                "search_attempts": {},
                "ledger": {"currency": "USD", "lines": []},
                "no_comparable": {},
                "log": [],
            },
        )
        worker.queue(repo, survey_id)
        pricing = repo.get_json(survey_id, "pricing") or {}
    searched = [title for chunk in batches for title in chunk]
    assert searched.count("Clean Code") == 1
    assert not any("MacBook" in title for title in searched)
    assert all(len(chunk) <= BATCH_SIZE for chunk in batches)
    assert any(
        item.get("event") == "price_search_queue finished" for item in pricing.get("log") or []
    )


def test_spoken_notes_search_unique_names_in_batches() -> None:
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    batches: list[int] = []
    original_batch = worker.web_search_batch

    def counted_batch(items, geography):
        batches.append(len(items))
        return original_batch(items, geography=geography)

    worker.web_search_batch = counted_batch
    names = [f"Study table {index}" for index in range(6)] + ["Study table 0"]
    worker.plan_targets = lambda *args, **kwargs: [
        {
            "name": name,
            "kind": "object",
            "category": "furniture",
            "description": name,
        }
        for name in names
    ]
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = UUID(survey["survey_id"])
        app.state.survey_workflow.repository.save_json(
            survey_id,
            "stage3",
            {
                "assets": [],
                "identities": [],
                "queue": [],
                "notes": [{"text": "furniture around the room", "monotonic_seconds": 1.0}],
            },
        )
        worker.price_spoken_notes(app.state.survey_workflow.repository, survey_id)
    assert batches == [5, 1]


def test_store_search_survives_report_stub_without_searches() -> None:
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = UUID(survey["survey_id"])
        repository = app.state.survey_workflow.repository
        repository.save_json(survey_id, "pricing", {"report_objects": []})
        state = worker._state(repository, survey_id)
        stored = worker._store_search(
            repository,
            survey_id,
            state,
            {"market": "in-IN", "country_code": "IN", "currency": "INR"},
            {
                "edition_key": "live:object:table",
                "query": "Study table",
                "book_title": "Study table",
                "kind": "object",
                "lookup": "object:study table",
            },
            {
                "query": "Study table",
                "citations": [
                    {
                        "offer_type": "physical",
                        "parsed_amount": "100",
                        "url": "https://www.example.com/table",
                    }
                ],
                "listing_url": "https://www.example.com/table",
                "listing_urls": ["https://www.example.com/table"],
                "html_sha256": "abc",
                "parser": "openai_web_search",
            },
        )
    assert stored["search_id"]
    assert any(item["search_id"] == stored["search_id"] for item in state["searches"])
    assert state["found_prices"]
    persisted = repository.get_json(survey_id, "pricing")
    assert any(item["search_id"] == stored["search_id"] for item in persisted["searches"])


def test_report_object_cache_does_not_clobber_searches() -> None:
    from backend.app.workflows.report import _cached_report_objects

    app, _, _ = isolated_app()
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = UUID(survey["survey_id"])
        repository = app.state.survey_workflow.repository
        repository.save_json(
            survey_id,
            "pricing",
            {"searches": [{"search_id": "keep"}], "live_searches": []},
        )
        _cached_report_objects(repository, survey_id, {}, {})
        saved = repository.get_json(survey_id, "pricing")
    assert saved["searches"] == [{"search_id": "keep"}]
    assert "report_objects" in saved


def test_after_seal_replays_when_spoken_search_raises(monkeypatch) -> None:
    app, _, _ = isolated_app()
    worker = app.state.survey_workflow.pricing_worker
    stub_pricing(worker)
    worker.identify_from_frames = lambda *args, **kwargs: {
        "titles": [],
        "copy_count": 0,
        "searches": [],
    }

    def boom(*args, **kwargs):
        raise KeyError("searches")

    worker.price_spoken_notes = boom
    replayed: dict[str, str] = {}

    def fake_replay(repository, survey_id, **kwargs):
        del repository, kwargs
        replayed["survey_id"] = str(survey_id)
        return {"copy_count": 2, "runs": [], "status": "empty"}

    monkeypatch.setattr("backend.app.workflows.models.replay_survey", fake_replay)
    with TestClient(app) as client:
        survey, _ = create_survey(client)
        survey_id = UUID(survey["survey_id"])
        result = worker.after_seal(app.state.survey_workflow.repository, survey_id)
    assert replayed["survey_id"] == str(survey_id)
    assert result["replayed"]["copy_count"] == 2
    saved = app.state.survey_workflow.repository.get_json(survey_id, "pricing")
    assert saved is not None
    assert "searches" in saved

