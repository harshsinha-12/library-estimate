"""Operator-facing §17 failure states. Unknown signals remain explicitly unverified."""

from __future__ import annotations

POLICY = (
    (
        "unsupported_roomplan",
        "RoomPlan unsupported or no LiDAR",
        "Use a supported device or mark the alternate capture partial.",
    ),
    ("incomplete_room", "Room loop or opening incomplete", "Rescan the named wall or doorway."),
    ("shelf_coverage", "Shelf face not fully covered", "Rescan the named row."),
    (
        "capture_quality",
        "Blur, glare, or text too small",
        "Slow down, change angle or light, and move closer.",
    ),
    ("invalid_barcode", "Barcode checksum failed", "Rescan the rear barcode and title page."),
    (
        "isbn_conflict",
        "ISBN conflicts with visible title",
        "Check the adjacent label and confirm the book.",
    ),
    ("no_isbn", "No usable ISBN", "Scan the title or copyright page, or keep unresolved."),
    (
        "same_isbn_copies",
        "Same ISBN in separate slots",
        "Keep both physical copy IDs unless spatial evidence conflicts.",
    ),
    (
        "repeat_pass",
        "Repeat pass may be duplicated",
        "Review spatial and visual evidence before merging.",
    ),
    ("moved_book", "Book may have moved", "Confirm one moved copy versus two copies."),
    ("unbound_audio", "Spoken note has no target", "Select the object the note describes."),
    ("location_fallback", "Manual location selected", "Check city and country before pricing."),
    (
        "location_mixed",
        "GPS and typed location conflict",
        "Confirm the market before pricing and building rates.",
    ),
    ("price_provider", "Price source unavailable", "Retry web search or add manual evidence."),
    ("ebook_only", "Only eBook or nonphysical offers", "Search for a physical format."),
    ("extreme_listing", "One extreme listing", "Add comparable offers or seek appraisal."),
    ("appraisal", "Potentially valuable art or rare book", "Request specialist appraisal."),
    (
        "model_disagreement",
        "Fable and Astra disagree",
        "Review the copy or capture targeted evidence.",
    ),
    (
        "deterministic_veto",
        "Models conflict with deterministic evidence",
        "Review the factual evidence and policy veto.",
    ),
    ("upload_interrupted", "Upload interrupted", "Resume the intact local package."),
    (
        "provider_failed",
        "Worker or model provider failed",
        "Review the partial result and rerun idempotently.",
    ),
    (
        "output_failed",
        "Schema or output write failed",
        "Preserve the run error, repair, and rerun.",
    ),
)


def failure_actions(survey: dict, overview: dict, stage3: dict, runs: list[dict]) -> list[dict]:
    """Only label a failure active when persisted survey evidence supports it."""
    copies = overview.get("copies") or []
    rows = overview.get("rows") or []
    queue = stage3.get("queue") or []
    kinds = {item.get("kind") for item in queue if item.get("status") == "open"}
    isbn_slots: dict[str, set[str]] = {}
    for copy in copies:
        if copy.get("isbn"):
            isbn_slots.setdefault(copy["isbn"], set()).add(copy["asset_copy_id"])
    active = {
        "shelf_coverage": any(row.get("recapture") for row in rows),
        "invalid_barcode": "rescan_barcode" in kinds,
        "isbn_conflict": "catalog_review" in kinds,
        "no_isbn": any(copy.get("category") == "book" and not copy.get("isbn") for copy in copies),
        "unbound_audio": "unbound_note" in kinds,
        "location_mixed": (survey.get("geography") or {}).get("source") == "mixed",
        "ebook_only": any(
            copy.get("valuation_status") == "no_comparable" and "eBook" in copy.get("reason", "")
            for copy in copies
        ),
        "appraisal": any(copy.get("requires_appraisal") for copy in copies),
        "model_disagreement": any((run.get("decision") or {}).get("disagreement") for run in runs),
        "deterministic_veto": any(
            (run.get("decision") or {}).get("reason") == "deterministic_veto" for run in runs
        ),
        "provider_failed": any(run.get("failures") for run in runs),
    }
    recorded = {
        "same_isbn_copies": any(len(ids) > 1 for ids in isbn_slots.values()),
        "location_fallback": (survey.get("geography") or {}).get("source") == "manual",
    }
    return [
        {
            "code": code,
            "failure": failure,
            "status": (
                "action_required"
                if active.get(code)
                else "recorded"
                if recorded.get(code)
                else "not_observed"
            ),
            "next_action": action,
        }
        for code, failure, action in POLICY
    ]
