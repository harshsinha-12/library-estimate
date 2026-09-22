"""Cited survey snapshot and readable PDF export."""

# pyright: reportMissingModuleSource=false

from __future__ import annotations

import json
from io import BytesIO
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from backend.app.domain.repository import SurveyRepository
from backend.app.providers.usage import usage_for_survey
from backend.app.utils.clocks import utc_now
from backend.app.utils.json_codec import canonical_json_bytes
from backend.app.workflows.astra_live import list_astra_live


def build_report_snapshot(repository: SurveyRepository, survey_id: UUID) -> dict:
    survey = repository.get(survey_id)
    if not survey.package_hash:
        raise ValueError("report requires a sealed survey")
    inventory = repository.get_json(survey_id, "inventory") or {}
    overview = repository.get_json(survey_id, "overview") or {}
    pricing = repository.get_json(survey_id, "pricing") or {}
    if not (overview.get("copies") or []):
        overview = {
            **overview,
            "copies": [],
            "live_searches": pricing.get("live_searches") or overview.get("live_searches") or [],
        }
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
        "model_pipelines": _model_pipelines(model_runs, astra_live),
        "astra_live": astra_live,
        "spend": usage_for_survey(repository, survey_id),
        "limitations": [
            "Physical copies and prices require operator review where evidence is incomplete.",
            "Web prices are draft evidence until confirmed; eBooks and rentals are excluded.",
            "Building figures use demo replacement-cost rates, not market sale value.",
            "Model agreement is not independent ground truth.",
            "After seal, Fable (A) and Astra replay (B) run on every AssetCopy independently. "
            "Jev scores A vs B and does not write count or price. "
            "Astra-live during Pass B/C is sampled assist metadata, not Pipeline B.",
            "Configured default model IDs are not a live confirmation of provider access.",
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


def _model_pipelines(model_runs: list[dict], astra_live: dict | None = None) -> dict:
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


def render_pdf(report: dict) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=(8.5 * inch, 11 * inch), rightMargin=48,
        leftMargin=48, topMargin=50, bottomMargin=50,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SurveyTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=20, leading=24, textColor=colors.HexColor("#183A34"),
        alignment=TA_CENTER, spaceAfter=16,
    ))
    styles.add(ParagraphStyle(
        name="SurveyBody", parent=styles["BodyText"], fontSize=9,
        leading=13, spaceAfter=7,
    ))
    story = [
        Paragraph("Library survey report", styles["SurveyTitle"]),
        Paragraph(_safe(report["property"]["display_name"]), styles["Heading2"]),
        Paragraph(f"Survey ID: {_safe(report['survey_id'])}", styles["SurveyBody"]),
        Paragraph(f"Generated: {_safe(report['generated_at'])}", styles["SurveyBody"]),
        Paragraph(f"Sealed package SHA-256: {_safe(report['package_hash'])}", styles["SurveyBody"]),
        Paragraph(
            f"Inventory pipeline: {_safe(report['inventory'].get('pipeline_version'))} | "
            f"Building rate table: {_safe(report['valuation'].get('rate_table_version'))} | "
            "Routing policy: route_v0_log_only",
            styles["SurveyBody"],
        ),
        Spacer(1, 10),
    ]
    inventory = report["inventory"]
    copies = inventory.get("asset_copies") or []
    rows = _unique_copy_rows(report["valuation"].get("copies") or [])
    spending = report["spend"]
    valuation = report["valuation"]
    contents = valuation.get("contents") or {}
    building = valuation.get("building") or {}
    books = [row for row in rows if row.get("category") == "book"]
    objects = [row for row in rows if row.get("category") != "book"]
    priced = [
        row for row in rows
        if row.get("valuation_status") in {"quoted", "manual"} and row.get("valuation")
    ]
    summary = [
        ["Recorded copies", str(len(copies) or len(rows))],
        ["Books / other objects", f"{len(books)} / {len(objects)}"],
        [
            "Priced / eligible",
            (
                f"{(valuation.get('priced_eligible') or {}).get('numerator', len(priced))}"
                f" / {(valuation.get('priced_eligible') or {}).get('denominator', 0)}"
            ),
        ],
        ["Inventory status", str(inventory.get("status") or "unavailable")],
        ["Market", str(valuation.get("city_market") or report["property"]["geography"])],
        ["Contents range", _money_range(contents)],
        ["Building reconstruction", _building_line(building)],
        ["Estimated provider cost (USD)", str(spending["estimated_cost_usd"])],
        ["Unpriced provider calls", str(spending["unpriced_calls"])],
    ]
    story.append(Paragraph("Summary", styles["Heading2"]))
    story.append(_table(summary))
    story.append(Spacer(1, 14))
    story.extend(_model_section(report, styles))
    story.append(Paragraph("Books and prices", styles["Heading2"]))
    story.extend(_copy_blocks(books, styles, empty="No book copies were recorded."))
    story.append(Paragraph("Other objects and prices", styles["Heading2"]))
    story.extend(_copy_blocks(objects, styles, empty="No non-book objects were recorded."))
    story.append(Paragraph("Limitations", styles["Heading2"]))
    for item in report["limitations"]:
        story.append(Paragraph(f"- {_safe(item)}", styles["SurveyBody"]))
    document.build(story)
    return buffer.getvalue()


def _model_section(report: dict, styles) -> list:
    pipelines = report.get("model_pipelines") or _model_pipelines(report.get("model_runs") or [])
    blocks = [Paragraph("Fable, Astra, and Jev", styles["Heading2"])]
    blocks.append(_table([
        ["Fable (Pipeline A)", f"{pipelines['fable']['status']} — {pipelines['fable']['role']}"],
        ["Astra (Pipeline B)", f"{pipelines['astra']['status']} — {pipelines['astra']['role']}"],
        ["Jev", f"{pipelines['jev']['status']} — {pipelines['jev']['role']}"],
        [
            "Astra-live assist",
            f"{(pipelines.get('astra_live') or {}).get('status', 'not_run')} — "
            f"{(pipelines.get('astra_live') or {}).get('role', 'capture UX only')}",
        ],
    ]))
    if pipelines.get("note"):
        blocks.append(Paragraph(_safe(pipelines["note"]), styles["SurveyBody"]))
    for run in _unique_model_runs(report.get("model_runs") or []):
        decision = run.get("decision") or {}
        assessments = run.get("assessments") or {}
        fable = assessments.get("fable") or {}
        astra = assessments.get("astra_replay") or assessments.get("astra") or {}
        jev = run.get("jev") or {}
        comparison = run.get("comparison") or {}
        failures = run.get("failures") or {}
        failure_line = (
            f" | Failures: {_safe(failures)}" if failures else ""
        )
        partial_line = " | Disclosed partial, human review" if run.get("partial") else ""
        blocks.append(KeepTogether([
            Paragraph(_safe(run.get("asset_copy_id")), styles["Heading3"]),
            Paragraph(
                f"Fable: {_safe(fable.get('category'))} / {_safe(fable.get('condition'))} "
                f"({_safe(fable.get('confidence'))}) | "
                f"Astra: {_safe(astra.get('category'))} / {_safe(astra.get('condition'))} "
                f"({_safe(astra.get('confidence'))}) | "
                f"Jev route: {_safe(comparison.get('chosen_route') or jev.get('choice'))} "
                f"({_safe(comparison.get('confidence') or jev.get('confidence'))}) | "
                f"Disagreement: {_safe(comparison.get('disagreement'))} | "
                f"Policy: {_safe(decision.get('action'))} ({_safe(decision.get('reason'))})"
                f"{failure_line}{partial_line}",
                styles["SurveyBody"],
            ),
        ]))
    return blocks


def _copy_blocks(rows: list[dict], styles, *, empty: str) -> list:
    if not rows:
        return [Paragraph(empty, styles["SurveyBody"])]
    blocks = []
    for row in rows[:150]:
        label = (
            row.get("title") or row.get("label") or row.get("asset_copy_id") or "Unknown"
        )
        status = row.get("valuation_status") or "pending"
        amount = ((row.get("valuation") or {}).get("amount") or {}).get("value")
        currency = (row.get("valuation") or {}).get("currency") or ""
        evidence = ", ".join(
            str(value)
            for value in (row.get("evidence_paths") or row.get("evidence_refs") or [])[:5]
        )
        listing = row.get("listing_url") or "No cited listing"
        confirmed = status in {"quoted", "manual"}
        status_label = (
            "confirmed" if confirmed else (
                "awaiting confirmation" if status == "price_pending" else status
            )
        )
        if amount is None:
            price_line = ""
        elif confirmed:
            price_line = f" | Confirmed price: {_safe(amount)} {_safe(currency)}"
        else:
            price_line = (
                f" | Web draft (not confirmed): {_safe(amount)} {_safe(currency)}"
            )
        blocks.append(KeepTogether([
            Paragraph(_safe(label), styles["Heading3"]),
            Paragraph(
                f"Copy: {_safe(row.get('asset_copy_id'))} | "
                f"Category: {_safe(row.get('category') or 'unknown')} | "
                f"Status: {_safe(status_label)}{price_line}",
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


def _money_range(contents: dict) -> str:
    if not contents:
        return "unavailable"
    currency = contents.get("currency") or ""
    low, central, high = contents.get("low"), contents.get("central"), contents.get("high")
    if central is None:
        return str(contents.get("note") or contents.get("status") or "unavailable")
    return f"{low}–{high} {currency} (center {central}; {contents.get('status')})"


def _building_line(building: dict) -> str:
    if not building:
        return "unavailable"
    amount = (building.get("amount") or {}).get("value")
    currency = building.get("currency") or ""
    area = (building.get("floor_area") or {}).get("value")
    if amount is None:
        return str(building.get("reason") or building.get("status") or "unavailable")
    return f"{amount} {currency} for {area} m² ({building.get('basis')})"


def _safe(value: object) -> str:
    from xml.sax.saxutils import escape

    return escape(str(value if value is not None else "unknown"))


def _table(rows: list[list[str]]) -> Table:
    table = Table(rows, colWidths=[220, 250], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF2EF")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#A8BDB5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


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
        if ((row.get("valuation") or {}).get("amount") or {}).get("value") is not None and not (
            ((prior.get("valuation") or {}).get("amount") or {}).get("value")
        ):
            prior["valuation"] = row.get("valuation")
            prior["draft_count"] = max(
                int(prior.get("draft_count") or 0),
                int(row.get("draft_count") or 0),
            )
            prior["reason"] = row.get("reason") or prior.get("reason")
    return [by_id[key] for key in order]


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
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        kind = item.get("query_kind") or "object"
        category = item.get("category") or ("book" if kind in {"isbn", "name", "book"} else "other")
        amount = item.get("amount")
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
                    if item.get("status") == "draft"
                    else (item.get("status") or "price_pending")
                ),
                "reason": item.get("reason") or "Web search draft",
                "draft_count": 1 if amount is not None else 0,
                "listing_url": item.get("listing_url"),
                "valuation": None
                if amount is None
                else {
                    "amount": {"value": amount},
                    "currency": item.get("currency"),
                },
            }
        )
    return rows
