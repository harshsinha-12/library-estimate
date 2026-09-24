"""Cited survey snapshot and readable PDF export."""

# pyright: reportMissingModuleSource=false

from __future__ import annotations

import json
from io import BytesIO
from uuid import UUID
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.app.domain.repository import SurveyRepository
from backend.app.providers.pricing.targets import keys_match, object_key
from backend.app.providers.usage import usage_for_survey
from backend.app.utils.clocks import utc_now
from backend.app.utils.json_codec import canonical_json_bytes
from backend.app.workflows.astra_live import list_astra_live
from backend.app.workflows.report_objects import (
    compact_price_records,
    records_fingerprint,
    structure_report_objects,
)

INK = colors.HexColor("#183A34")
INK_SOFT = colors.HexColor("#EAF2EF")
RULE = colors.HexColor("#A8BDB5")
STRIPE = colors.HexColor("#F4F8F6")
MUTED = colors.HexColor("#4D635E")
PAGE_WIDTH = 8.5 * inch - 96


def build_report_snapshot(repository: SurveyRepository, survey_id: UUID) -> dict:
    survey = repository.get(survey_id)
    if not survey.package_hash:
        raise ValueError("report requires a sealed survey")
    inventory = repository.get_json(survey_id, "inventory") or {}
    overview = dict(repository.get_json(survey_id, "overview") or {})
    pricing = repository.get_json(survey_id, "pricing") or {}
    overview["copies"] = overview.get("copies") or []
    overview["live_searches"] = (
        pricing.get("live_searches") or overview.get("live_searches") or []
    )
    overview["found_prices"] = (
        pricing.get("found_prices") or overview.get("found_prices") or []
    )
    overview["report_objects"] = _cached_report_objects(
        repository, survey_id, pricing, overview
    )
    stage3 = repository.get_json(survey_id, "stage3") or {}
    replay_summary = repository.get_json(survey_id, "model-replay-survey") or {}
    model_runs = [
        row for row in replay_summary.get("runs") or [] if isinstance(row, dict)
    ]
    if not model_runs:
        model_key = f"{repository.key_prefix}:survey:{survey_id}:model_runs"
        model_runs = [
            repository.get_json(survey_id, f"model-run:{raw}")
            for raw in sorted(repository.redis.smembers(model_key))
        ]
        model_runs = [row for row in model_runs if row]
    model_runs = [
        row
        for row in model_runs
        if not str(row.get("asset_copy_id") or "").startswith("found_")
    ]
    astra_live = list_astra_live(repository, survey_id)
    shelf_photo = stage3.get("source") == "llm_shelf_photo"
    report = {
        "schema_version": "1.0.0", "survey_id": str(survey_id),
        "generated_at": utc_now().isoformat(), "package_hash": survey.package_hash,
        "status": survey.status, "property": {
            "display_name": survey.display_name,
            "geography": survey.geography.model_dump(mode="json"),
        },
        "geometry": (
            json.loads(repository.get_bytes(survey_id, "derived/geometry.json"))
            if repository.exists_bytes(survey_id, "derived/geometry.json") else None
        ),
        "inventory": inventory,
        "review": stage3,
        "valuation": overview,
        "model_runs": model_runs,
        "model_pipelines": _model_pipelines(
            model_runs, astra_live, shelf_photo=shelf_photo
        ),
        "astra_live": astra_live,
        "spend": usage_for_survey(repository, survey_id),
        "limitations": [
            "Physical copies and prices require operator review where evidence is incomplete.",
            "Web prices are draft evidence until confirmed; eBooks and rentals are excluded.",
            "Building figures use demo replacement-cost rates, not market sale value.",
            "Model agreement is not independent ground truth.",
            (
                "This shelf-photo survey names books and looks up prices in one model call "
                "per photo. Fable, Astra Extra, and Jev are not scheduled on that path."
                if shelf_photo
                else
                "After seal, Fable (A) and Astra replay (B) run on every AssetCopy independently. "
                "Jev scores A vs B and does not write count or price. "
                "Astra-live during Pass B/C is sampled assist metadata, not Pipeline B."
            ),
            "Invertis live model IDs (logs/llm_calls.json): Pipeline A claude-fable-5.1 "
            "(Fable role), Pipeline B gpt-6-astra, Jev jev-latest / jev-1.13.0. "
            "Code defaults are still FABLE_MODEL/ASTRA_MODEL/JEV_MODEL.",
        ],
    }
    repository.put_bytes(
        survey_id, "derived/report.json", canonical_json_bytes(report), "application/json"
    )
    repository.save_json(survey_id, "report", report)
    return report


def build_report(repository: SurveyRepository, survey_id: UUID) -> tuple[dict, bytes]:
    report = build_report_snapshot(repository, survey_id)
    pdf = render_pdf(report)
    repository.put_bytes(survey_id, "derived/report.pdf", pdf, "application/pdf")
    return report, pdf


def _model_pipelines(
    model_runs: list[dict],
    astra_live: dict | None = None,
    *,
    shelf_photo: bool = False,
) -> dict:
    live_rows = (astra_live or {}).get("assists") or []
    live_ran = any(row.get("status") == "assist" for row in live_rows)
    if not model_runs:
        return {
            "fable": {
                "status": "not_run",
                "role": "Pipeline A. Anthropic batch assessment of a sealed copy.",
            },
            "astra": {
                "status": "not_run",
                "role": "Pipeline B. Independent OpenAI replay of the same evidence bytes.",
            },
            "jev": {
                "status": "not_run",
                "role": "Typed router over Fable and Astra. Policy still vetoes the action.",
            },
            "astra_live": {
                "status": "ran" if live_ran else "not_run",
                "role": "Capture UX assist on Pass B/C. Not Pipeline B and not inventory.",
            },
            "note": (
                "This survey priced each shelf photo in one language-model call. "
                "Fable, Astra Extra, and Jev were not scheduled. "
                "Astra-live is the Pass B/C camera assist, and this capture has no live sweep."
                if shelf_photo
                else
                "After seal, Fable and Astra replay run automatically on every AssetCopy. "
                "The inventory button is optional replay of the same sealed bytes. "
                "This survey has no sealed replay yet."
            ),
        }
    fable = any((row.get("assessments") or {}).get("fable") for row in model_runs)
    astra = any(
        (row.get("assessments") or {}).get("astra_replay")
        or (row.get("assessments") or {}).get("astra")
        for row in model_runs
    )
    jev = any(row.get("jev") for row in model_runs)
    failures = [
        row.get("failures") for row in model_runs if row.get("failures")
    ]
    partial = any(row.get("partial") for row in model_runs)
    note = (
        "Automatic A/B after seal is the pipeline. These models judge category and "
        "condition on a sealed copy; they are not the web-search pricer and they do "
        "not write count or price. Live policy is route_v0_log_only. "
        f"Disclosed partial: {partial}. Provider failures: {failures or 'none recorded'}."
        if not (fable and astra and jev) or partial
        else None
    )
    return {
        "fable": {
            "status": "ran" if fable else "failed_or_missing",
            "role": "Pipeline A. Anthropic batch assessment of a sealed copy.",
        },
        "astra": {
            "status": "ran" if astra else "failed_or_missing",
            "role": "Pipeline B. Independent OpenAI replay of the same evidence bytes.",
        },
        "jev": {
            "status": "ran" if jev else "failed_or_missing",
            "role": "Typed router over Fable and Astra. Policy still vetoes the action.",
        },
        "astra_live": {
            "status": "ran" if live_ran else "not_run",
            "role": "Capture UX assist on Pass B/C. Not Pipeline B and not inventory.",
        },
        "note": note,
    }


def _cached_report_objects(
    repository: SurveyRepository,
    survey_id: UUID,
    pricing: dict,
    overview: dict,
) -> list[dict]:
    records = compact_price_records(
        {
            **overview,
            "live_searches": (
                pricing.get("live_searches") or overview.get("live_searches") or []
            ),
            "found_prices": (
                pricing.get("found_prices") or overview.get("found_prices") or []
            ),
        }
    )
    digest = records_fingerprint(records)
    cached = pricing.get("report_objects")
    if (
        digest
        and pricing.get("report_objects_sha256") == digest
        and isinstance(cached, list)
    ):
        return cached
    structured = structure_report_objects(records)
    current = dict(repository.get_json(survey_id, "pricing") or {})
    durable_keys = (
        "searches",
        "live_searches",
        "found_prices",
        "observations",
        "ledger",
        "search_attempts",
        "no_comparable",
        "log",
        "schema_version",
        "pipeline_version",
    )
    durable = {key: current[key] for key in durable_keys if key in current}
    if not durable:
        durable = {key: pricing[key] for key in durable_keys if key in pricing}
    current.update(durable)
    current["report_objects"] = structured
    current["report_objects_sha256"] = digest
    survey = repository.get(survey_id)
    if survey.status == "ingest_validation" and "searches" not in durable:
        return structured
    repository.save_json(survey_id, "pricing", current)
    return structured


def render_pdf(report: dict) -> bytes:
    buffer = BytesIO()
    styles = _styles()
    inventory = report["inventory"]
    valuation = report["valuation"]
    copies = _unique_copy_rows(valuation.get("copies") or [])
    physical_books = [row for row in copies if row.get("category") == "book"]
    books = _unique_book_rows(physical_books, valuation)
    objects = valuation.get("report_objects") or _object_rows(copies, valuation)
    book_copies = sum(int(row.get("copy_count") or 1) for row in books)
    geography = report["property"]["geography"]
    market = valuation.get("city_market") or (
        f"{geography.get('city')}, {geography.get('country_code')} · {geography.get('market')}"
    )
    story = [
        Paragraph("Library survey report", styles["SurveyTitle"]),
        Paragraph(_safe(report["property"]["display_name"]), styles["SurveyProperty"]),
        Paragraph(
            f"{_safe(market)} · generated {_safe(_when(report['generated_at']))}",
            styles["SurveyMeta"],
        ),
        Paragraph(
            f"Survey {_safe(report['survey_id'])} · sealed {_safe(report['package_hash'])[:16]}…",
            styles["SurveyMeta"],
        ),
        Spacer(1, 10),
        *_section("Summary", styles),
        _kv_table([
            ["Recorded copies", str(len(inventory.get("asset_copies") or copies))],
            ["Unique book titles / copies", f"{len(books)} / {book_copies}"],
            ["Other objects", str(len(objects))],
            [
                "Confirmed / eligible books",
                (
                    f"{(valuation.get('priced_eligible') or {}).get('numerator', 0)}"
                    f" / {(valuation.get('priced_eligible') or {}).get('denominator', 0)}"
                ),
            ],
            ["Inventory status", str(inventory.get("status") or "unavailable")],
            ["Contents range", _money_range(valuation.get("contents") or {})],
            ["Building reconstruction", _building_line(valuation.get("building") or {})],
            ["Estimated provider cost", f"USD {report['spend']['estimated_cost_usd']}"],
            ["Unpriced provider calls", str(report["spend"]["unpriced_calls"])],
            [
                "Pipeline / rate table",
                (
                    f"{inventory.get('pipeline_version') or 'unavailable'} · "
                    f"{valuation.get('rate_table_version') or 'unavailable'}"
                ),
            ],
        ]),
        Spacer(1, 14),
        *_section("Other objects and prices", styles),
        *_item_table(
            objects, styles, empty="No non-book object prices were found in Redis.",
            kind="object",
        ),
        Spacer(1, 14),
        *_section("Books and prices", styles),
        *_item_table(books, styles, empty="No book copies were recorded.", kind="book"),
        Spacer(1, 14),
        *_model_section(report, styles),
        Spacer(1, 14),
        *_section("Limitations", styles),
    ]
    for item in report["limitations"]:
        story.append(Paragraph(f"• {_safe(item)}", styles["SurveyBody"]))
    document = SimpleDocTemplate(
        buffer, pagesize=(8.5 * inch, 11 * inch),
        rightMargin=48, leftMargin=48, topMargin=56, bottomMargin=48,
        title=str(report["property"]["display_name"]),
        author="Library survey",
    )
    document.build(story, onFirstPage=_draw_page, onLaterPages=_draw_page)
    return buffer.getvalue()


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SurveyTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, leading=26, textColor=INK, alignment=TA_LEFT, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="SurveyProperty", parent=styles["Heading2"], fontName="Helvetica",
        fontSize=13, leading=16, textColor=INK, spaceBefore=0, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SurveyHeading", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, leading=15, textColor=INK, spaceBefore=0, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="SurveyMeta", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=11, textColor=MUTED, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="SurveyBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=12, textColor=INK, spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="Cell", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=11, textColor=INK, spaceAfter=0,
    ))
    styles.add(ParagraphStyle(
        name="CellMuted", parent=styles["Cell"], textColor=MUTED,
    ))
    styles.add(ParagraphStyle(
        name="HeadCell", parent=styles["Cell"], fontName="Helvetica-Bold",
        textColor=colors.white,
    ))
    return styles


def _section(title: str, styles) -> list:
    return [
        Paragraph(_safe(title), styles["SurveyHeading"]),
        HRFlowable(width="100%", thickness=0.8, color=RULE, spaceAfter=8),
    ]


def _draw_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, 11 * inch - 28, 8.5 * inch, 28, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(48, 11 * inch - 18, "LIBRARY SURVEY")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(8.5 * inch - 48, 11 * inch - 18, f"Page {doc.page}")
    canvas.setFillColor(RULE)
    canvas.rect(48, 32, PAGE_WIDTH, 0.6, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(
        48, 20, "Replacement-cost evidence. Draft prices need operator confirmation."
    )
    canvas.restoreState()


def _model_section(report: dict, styles) -> list:
    pipelines = report.get("model_pipelines") or _model_pipelines(report.get("model_runs") or [])
    blocks = [*_section("Fable, Astra Extra, and Jev", styles)]
    blocks.append(_kv_table([
        ["Fable (Pipeline A)", f"{pipelines['fable']['status']} — {pipelines['fable']['role']}"],
        [
            "Astra Extra (Pipeline B)",
            f"{pipelines['astra']['status']} — {pipelines['astra']['role']}",
        ],
        ["Jev", f"{pipelines['jev']['status']} — {pipelines['jev']['role']}"],
        [
            "Astra-live Extra assist",
            f"{(pipelines.get('astra_live') or {}).get('status', 'not_run')} — "
            f"{(pipelines.get('astra_live') or {}).get('role', 'capture UX only')}",
        ],
    ]))
    if pipelines.get("note"):
        blocks.append(Spacer(1, 6))
        blocks.append(Paragraph(_safe(pipelines["note"]), styles["SurveyBody"]))
    runs = _unique_model_runs(report.get("model_runs") or [])
    if not runs:
        return blocks
    header = [
        Paragraph("Copy", styles["HeadCell"]),
        Paragraph("Fable", styles["HeadCell"]),
        Paragraph("Astra Extra", styles["HeadCell"]),
        Paragraph("Jev / policy", styles["HeadCell"]),
    ]
    data = [header]
    for run in runs[:80]:
        decision = run.get("decision") or {}
        assessments = run.get("assessments") or {}
        fable = assessments.get("fable") or {}
        astra = assessments.get("astra_replay") or assessments.get("astra") or {}
        jev = run.get("jev") or {}
        comparison = run.get("comparison") or {}
        failures = run.get("failures") or {}
        policy = (
            f"{decision.get('action') or 'unknown'} "
            f"({decision.get('reason') or 'n/a'})"
        )
        if failures:
            policy += f" · {failures}"
        if run.get("partial"):
            policy += " · partial"
        route = comparison.get("chosen_route") or jev.get("choice") or "unknown"
        data.append([
            Paragraph(_safe(run.get("asset_copy_id")), styles["Cell"]),
            Paragraph(_assess_cell(fable), styles["Cell"]),
            Paragraph(_assess_cell(astra), styles["Cell"]),
            Paragraph(_safe(f"{route} · {policy}"), styles["CellMuted"]),
        ])
    table = Table(data, colWidths=[92, 108, 108, 208], hAlign="LEFT", repeatRows=1)
    table.setStyle(_grid_style())
    blocks.append(Spacer(1, 8))
    blocks.append(table)
    return blocks


def _assess_cell(assessment: dict) -> str:
    if not assessment:
        return "—"
    confidence = assessment.get("confidence")
    try:
        shown = f"{float(confidence):.2f}"
    except (TypeError, ValueError):
        shown = "—"
    return _safe(
        f"{assessment.get('category') or 'unknown'} / "
        f"{assessment.get('condition') or 'unknown'} ({shown})"
    )


def _item_table(rows: list[dict], styles, *, empty: str, kind: str) -> list:
    if not rows:
        return [Paragraph(empty, styles["SurveyBody"])]
    if kind == "object":
        header = ["Object", "Category", "Status", "Price", "Listing"]
        widths = [130, 70, 88, 78, 150]
    else:
        header = ["Title", "Qty", "Status", "Price", "Listing"]
        widths = [150, 40, 88, 78, 160]
    data = [[Paragraph(label, styles["HeadCell"]) for label in header]]
    for row in rows[:150]:
        label = row.get("title") or row.get("label") or row.get("asset_copy_id") or "Unknown"
        listing = row.get("listing_url") or ""
        listing_cell = (
            f'<link href="{_safe(listing)}">{_safe(_short_url(listing))}</link>'
            if listing else "No cited listing"
        )
        if kind == "object":
            cells = [
                _safe(label),
                _safe(row.get("category") or "other"),
                _safe(_status_label(row)),
                _safe(_row_price(row)),
                listing_cell,
            ]
        else:
            cells = [
                _safe(label),
                _safe(row.get("copy_count") or 1),
                _safe(_status_label(row)),
                _safe(_row_price(row)),
                listing_cell,
            ]
        data.append([Paragraph(text, styles["Cell"]) for text in cells])
    table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(_grid_style())
    return [table]


def _object_rows(copies: list[dict], valuation: dict) -> list[dict]:
    from_copies = [row for row in copies if row.get("category") not in {"book", "serial"}]
    from_redis = [
        row for row in _rows_from_pricing(valuation)
        if row.get("category") not in {"book", "serial"}
    ]
    from_found = _rows_from_found_prices(valuation)
    return _unique_object_rows(from_copies + from_redis + from_found)


def _unique_object_rows(rows: list[dict]) -> list[dict]:
    by_title: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        title = str(row.get("title") or row.get("label") or "").strip().lower()
        key = title or str(row.get("asset_copy_id") or len(order))
        if key not in by_title:
            by_title[key] = dict(row)
            order.append(key)
            continue
        prior = by_title[key]
        if row.get("listing_url") and not prior.get("listing_url"):
            prior["listing_url"] = row.get("listing_url")
        if row.get("query") and (
            not prior.get("query") or "paperback" in str(prior.get("query") or "")
        ):
            prior["query"] = row.get("query")
            prior["query_kind"] = row.get("query_kind") or prior.get("query_kind")
        if row.get("category") and (
            not prior.get("category") or prior.get("category") == "other"
        ):
            prior["category"] = row.get("category")
        if _amount_of(row) is not None and _amount_of(prior) is None:
            prior["valuation"] = row.get("valuation")
            prior["draft_count"] = max(
                int(prior.get("draft_count") or 0), int(row.get("draft_count") or 0),
            )
            prior["reason"] = row.get("reason") or prior.get("reason")
            prior["valuation_status"] = (
                row.get("valuation_status") or prior.get("valuation_status")
            )
    return [by_title[key] for key in order]


def _rows_from_found_prices(valuation: dict) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for item in valuation.get("found_prices") or []:
        title = str(item.get("title") or "").strip()
        kind = str(item.get("query_kind") or "object")
        category = str(item.get("category") or "")
        if len(title) < 3 or title.lower() in seen:
            continue
        if kind in {"isbn", "name", "book"} or category in {"book", "serial", "cup"}:
            continue
        if kind != "object" and category in {"", "other"}:
            continue
        if item.get("offer_type") not in {None, "physical"}:
            continue
        amount = item.get("amount")
        if amount is None:
            continue
        seen.add(title.lower())
        rows.append(
            {
                "asset_copy_id": f"found-{len(rows) + 1}",
                "category": category or "other",
                "label": title,
                "title": title,
                "query": item.get("query") or title,
                "query_kind": kind,
                "valuation_status": "price_pending",
                "reason": "Redis found_prices draft",
                "draft_count": 1,
                "listing_url": item.get("url") or item.get("listing_url"),
                "valuation": {
                    "amount": {"value": amount},
                    "currency": item.get("currency"),
                },
            }
        )
    return rows


def _copy_blocks(rows: list[dict], styles, *, empty: str) -> list:
    if not rows:
        return [Paragraph(empty, styles["SurveyBody"])]
    blocks = []
    for row in rows[:150]:
        label = (
            row.get("title") or row.get("label") or row.get("asset_copy_id") or "Unknown"
        )
        amount = _amount_of(row)
        currency = (row.get("valuation") or {}).get("currency") or ""
        evidence = ", ".join(
            str(value)
            for value in (row.get("evidence_paths") or row.get("evidence_refs") or [])[:5]
        )
        listing = row.get("listing_url") or "No cited listing"
        if amount is None:
            price_line = ""
        elif row.get("valuation_status") in {"quoted", "manual"}:
            price_line = f" | Confirmed price: {_safe(_money(amount, currency))}"
        else:
            price_line = (
                f" | Web draft (not confirmed): {_safe(_money(amount, currency))}"
            )
        blocks.append(KeepTogether([
            Paragraph(_safe(label), styles["SurveyHeading"]),
            Paragraph(
                f"Copy: {_safe(row.get('asset_copy_id'))} | "
                f"Category: {_safe(row.get('category') or 'unknown')} | "
                f"Status: {_safe(_status_label(row))}{price_line}",
                styles["SurveyBody"],
            ),
            Paragraph(
                f"ISBN: {_safe(row.get('isbn') or 'none')} | "
                f"Query: {_safe(row.get('query') or 'not built')} "
                f"({_safe(row.get('query_kind') or 'none')}) | "
                f"Drafts: {_safe(row.get('draft_count') or 0)}",
                styles["SurveyBody"],
            ),
            Paragraph(
                f"Why: {_safe(row.get('reason') or 'no pricing reason')}",
                styles["SurveyBody"],
            ),
            Paragraph(f"Evidence: {_safe(evidence or 'none supplied')}", styles["SurveyBody"]),
            Paragraph(f"Listing: {_safe(listing)}", styles["SurveyBody"]),
        ]))
    return blocks


def _status_label(row: dict) -> str:
    status = row.get("valuation_status") or "pending"
    if status in {"quoted", "manual"}:
        return "confirmed"
    if status in {"price_pending", "draft"}:
        return "awaiting confirmation"
    return str(status)


def _row_price(row: dict) -> str:
    amount = _amount_of(row)
    currency = (row.get("valuation") or {}).get("currency") or ""
    if amount is None:
        return "—"
    label = _money(amount, currency)
    if row.get("valuation_status") in {"quoted", "manual"}:
        return label
    return f"{label} draft"


def _amount_of(row: dict) -> object:
    return ((row.get("valuation") or {}).get("amount") or {}).get("value")


def _money(amount: object, currency: str = "") -> str:
    try:
        number = float(str(amount).replace(",", ""))
    except (TypeError, ValueError):
        text = str(amount)
        return f"{text} {currency}".strip()
    if abs(number - round(number)) < 1e-6:
        formatted = f"{int(round(number)):,}"
    else:
        formatted = f"{number:,.2f}"
    return f"{formatted} {currency}".strip()


def _money_range(contents: dict) -> str:
    if not contents:
        return "unavailable"
    currency = contents.get("currency") or ""
    low, central, high = contents.get("low"), contents.get("central"), contents.get("high")
    if central is None:
        return str(contents.get("note") or contents.get("status") or "unavailable")
    return (
        f"{_money(low, currency)} – {_money(high, currency)} "
        f"(center {_money(central, currency)}; {contents.get('status')})"
    )


def _building_line(building: dict) -> str:
    if not building:
        return "unavailable"
    amount = (building.get("amount") or {}).get("value")
    currency = building.get("currency") or ""
    area = (building.get("floor_area") or {}).get("value")
    if amount is None:
        return str(building.get("reason") or building.get("status") or "unavailable")
    try:
        area_text = f"{float(area):.2f} m²"
    except (TypeError, ValueError):
        area_text = f"{area} m²"
    return f"{_money(amount, currency)} for {area_text} ({building.get('basis')})"


def _when(value: object) -> str:
    text = str(value or "")
    if "T" in text:
        return text.replace("T", " ").replace("+00:00", " UTC")[:22]
    return text or "unknown"


def _short_url(url: str) -> str:
    text = url.replace("https://", "").replace("http://", "")
    return text if len(text) <= 42 else text[:39] + "…"


def _safe(value: object) -> str:
    return escape(str(value if value is not None else "unknown"))


def _kv_label() -> ParagraphStyle:
    return ParagraphStyle(
        "KvLabel", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=INK,
        alignment=TA_LEFT,
    )


def _kv_value() -> ParagraphStyle:
    return ParagraphStyle(
        "KvValue", fontName="Helvetica", fontSize=8, leading=11, textColor=INK,
        alignment=TA_LEFT,
    )


def _kv_table(rows: list[list[str]]) -> Table:
    data = [
        [Paragraph(_safe(label), _kv_label()), Paragraph(_safe(value), _kv_value())]
        for label, value in rows
    ]
    table = Table(data, colWidths=[170, PAGE_WIDTH - 170], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), INK_SOFT),
        ("BACKGROUND", (1, 0), (1, -1), colors.white),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _grid_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
    ])


def _table(rows: list[list[str]]) -> Table:
    return _kv_table(rows)


def _unique_copy_rows(rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        title = str(row.get("title") or row.get("label") or "").strip().lower()
        key = str(row.get("asset_copy_id") or "") or f"{row.get('category')}:{title}"
        if key not in by_id:
            by_id[key] = dict(row)
            order.append(key)
            continue
        prior = by_id[key]
        if row.get("listing_url") and not prior.get("listing_url"):
            prior["listing_url"] = row.get("listing_url")
        if row.get("query") and (
            not prior.get("query") or "paperback" in str(prior.get("query") or "")
        ):
            prior["query"] = row.get("query")
            prior["query_kind"] = row.get("query_kind")
        if _amount_of(row) is not None and _amount_of(prior) is None:
            prior["valuation"] = row.get("valuation")
            prior["draft_count"] = max(
                int(prior.get("draft_count") or 0),
                int(row.get("draft_count") or 0),
            )
            prior["reason"] = row.get("reason") or prior.get("reason")
    return [by_id[key] for key in order]


def _unique_book_rows(rows: list[dict], valuation: dict) -> list[dict]:
    priced = list(valuation.get("live_searches") or []) + list(valuation.get("found_prices") or [])
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        key = _book_group_key(row)
        match = next((item for item in order if keys_match(item, key)), None)
        if match is None:
            grouped[key] = dict(row)
            grouped[key]["copy_count"] = 1
            order.append(key)
            _bind_book_draft(grouped[key], priced)
            continue
        prior = grouped[match]
        prior["copy_count"] = int(prior.get("copy_count") or 1) + 1
        if row.get("listing_url") and not prior.get("listing_url"):
            prior["listing_url"] = row.get("listing_url")
        if _amount_of(row) is not None and _amount_of(prior) is None:
            prior["valuation"] = row.get("valuation")
            prior["valuation_status"] = row.get("valuation_status") or prior.get("valuation_status")
        _bind_book_draft(prior, priced)
    return [grouped[key] for key in order]


def _book_group_key(row: dict) -> str:
    return object_key(
        kind=row.get("query_kind") or "book",
        title=row.get("title") or row.get("label"),
        category="book",
        isbn=row.get("isbn"),
    )


def _bind_book_draft(row: dict, priced: list[dict]) -> None:
    if _amount_of(row) is not None:
        return
    key = _book_group_key(row)
    for item in priced:
        amount = item.get("amount") or item.get("parsed_amount")
        if amount is None:
            continue
        other = object_key(
            kind=item.get("query_kind") or "book",
            title=item.get("title") or item.get("query"),
            category="book",
            isbn=item.get("isbn"),
        )
        if not keys_match(key, other):
            continue
        row["valuation"] = {
            "amount": {"value": amount},
            "currency": item.get("currency"),
        }
        row["listing_url"] = row.get("listing_url") or item.get("listing_url") or item.get("url")
        row["draft_count"] = max(int(row.get("draft_count") or 0), 1)
        if row.get("valuation_status") in {None, "price_pending", "pending"}:
            row["valuation_status"] = "price_pending"
        return


def _unique_model_runs(runs: list[dict]) -> list[dict]:
    by_copy: dict[str, dict] = {}
    order: list[str] = []
    for run in runs:
        key = str(run.get("asset_copy_id") or run.get("run_id") or len(order))
        if key not in by_copy:
            by_copy[key] = run
            order.append(key)
            continue
        prior = by_copy[key]
        if (run.get("assessments") or {}) and not (prior.get("assessments") or {}):
            by_copy[key] = run
    return [by_copy[key] for key in order]


def _rows_from_pricing(pricing: dict) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for item in pricing.get("live_searches") or []:
        title = str(item.get("title") or item.get("query") or "").strip()
        if len(title) < 3:
            continue
        lowered = title.lower()
        if lowered.startswith("unidentified book") or lowered.startswith("books on the"):
            continue
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        kind = item.get("query_kind") or "object"
        category = item.get("category") or (
            "book" if kind in {"isbn", "name", "book"} else "other"
        )
        amount = item.get("amount")
        listing = item.get("listing_url") or item.get("url")
        rows.append(
            {
                "asset_copy_id": f"search-{len(rows) + 1}",
                "category": category,
                "label": title,
                "title": title,
                "isbn": item.get("isbn"),
                "query": item.get("query") or title,
                "query_kind": kind,
                "valuation_status": (
                    "price_pending"
                    if item.get("status") in {"draft", "price_pending", None}
                    else (item.get("status") or "price_pending")
                ),
                "reason": item.get("reason") or "Web search draft",
                "draft_count": 1 if amount is not None else 0,
                "listing_url": listing,
                "valuation": None
                if amount is None
                else {
                    "amount": {"value": amount},
                    "currency": item.get("currency"),
                },
            }
        )
    return rows
