"""Pass C identity, non-book policy, note binding, and exception review."""

from __future__ import annotations

import json
import os
from math import dist
from uuid import UUID, uuid4

from backend.app.domain.repository import SurveyRepository
from backend.app.providers.catalog.chain import CatalogChain
from backend.app.providers.pricing.queries import title_from_ocr
from backend.app.providers.voice import transcribe_segments
from backend.app.utils.hashing import sha256_bytes
from backend.app.workflows.identifiers import type_identifier

TAXONOMY = frozenset(
    {
        "book",
        "serial",
        "painting",
        "portrait",
        "sculpture",
        "computer",
        "monitor",
        "printer",
        "furniture",
        "shelf",
        "appliance",
        "cup",
        "decorative_object",
        "other",
    }
)
APPRAISAL = {"painting", "portrait", "sculpture"}
EXCLUDED = {"cup"}
ASSET_COPY_FIELDS = {
    "asset_copy_id",
    "category",
    "observation_refs",
    "valuation_required",
    "requires_appraisal",
    "possibly_moved",
    "book_edition_ref",
    "room_id",
    "shelf_id",
    "face_id",
    "row_id",
    "slot",
    "isbn",
}


def asset_policy(category: str, high_value: bool = False) -> dict:
    if category not in TAXONOMY:
        raise ValueError(f"unknown asset category: {category}")
    appraisal = category in APPRAISAL or high_value
    return {
        "valuation_required": category not in EXCLUDED,
        "requires_appraisal": appraisal,
        "excluded": category in EXCLUDED,
    }


def _associate(note: dict, assets: list[dict]) -> tuple[str | None, str]:
    explicit = note.get("tapped_asset_id")
    ids = {asset["asset_copy_id"] for asset in assets}
    if explicit in ids:
        return explicit, "operator_tap"
    candidates = []
    time = note.get("monotonic_seconds")
    text = (note.get("text") or "").lower()
    inferred = {category for category in TAXONOMY if category in text}
    if "mug" in text or "coffee cup" in text:
        inferred.add("cup")
    if "table" in text or "desk" in text or "bed" in text or "wardrobe" in text:
        inferred.add("furniture")
    if "almirah" in text:
        inferred.add("furniture")
    if "photo frame" in text or "picture frame" in text:
        inferred.add("portrait")
    if "air conditioner" in text or " a/c" in text or text.endswith(" ac") or " ac " in text:
        inferred.add("appliance")
    hinted = set(note.get("category_hints") or []) | inferred
    for asset in assets:
        score = 0.0
        if (
            note.get("face_id")
            and note.get("face_id") == asset.get("face_id")
            and note.get("row_id") == asset.get("row_id")
            and note.get("slot") == asset.get("slot")
        ):
            score += 4
        if note.get("reticle_asset_id") == asset["asset_copy_id"]:
            score += 3
        if note.get("pose_asset_id") == asset["asset_copy_id"]:
            score += 2
        note_pose = note.get("camera_pose")
        asset_pose = asset.get("camera_pose")
        if (
            isinstance(note_pose, list)
            and isinstance(asset_pose, list)
            and len(note_pose) == 16
            and len(asset_pose) == 16
            and dist(
                [float(note_pose[i]) for i in (12, 13, 14)],
                [float(asset_pose[i]) for i in (12, 13, 14)],
            )
            <= 0.5
        ):
            score += 2
        if (
            time is not None
            and asset.get("monotonic_seconds") is not None
            and abs(float(time) - float(asset["monotonic_seconds"])) <= 3
        ):
            score += 1
        if asset["category"] in hinted:
            score += 1
        if score:
            candidates.append((score, asset["asset_copy_id"]))
    candidates.sort(reverse=True)
    if not candidates or (len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 2):
        return None, "ambiguous_or_no_target"
    return candidates[0][1], "contextual_association"


class Stage3Worker:
    def __init__(self, catalog: CatalogChain | None = None) -> None:
        self.catalog = catalog or CatalogChain()

    def process(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        existing = repository.get_json(survey_id, "stage3")
        if existing is not None:
            return existing
        inventory = repository.get_json(survey_id, "inventory") or {}
        assets = list(inventory.get("asset_copies") or [])
        for asset in assets:
            # Stage B OCR hints have no checksum authority.
            asset["isbn"] = None
        payload = self._read(repository, survey_id, "exceptions/pass-c.json", {})
        other = self._read(repository, survey_id, "other_assets/marks.json", [])
        notes = self._read(repository, survey_id, "notes/annotations.json", [])
        if (
            not isinstance(payload, dict)
            or not isinstance(other, list)
            or not isinstance(notes, list)
        ):
            raise ValueError("invalid Stage 3 package shape")
        queue: list[dict] = []
        speech_status = "absent"
        if repository.exists_bytes(survey_id, "audio/survey.m4a") and repository.exists_bytes(
            survey_id, "audio/timing.json"
        ):
            timing = self._read(repository, survey_id, "audio/timing.json", {})
            if os.getenv("OPENAI_API_KEY"):
                try:
                    notes.extend(
                        transcribe_segments(
                            repository.get_bytes(survey_id, "audio/survey.m4a"),
                            float(timing["started_monotonic_seconds"]),
                            model=os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe-diarize"),
                        )
                    )
                    speech_status = "transcribed"
                except (OSError, ValueError, KeyError):
                    speech_status = "failed"
                    queue.append(
                        self._queue(
                            "transcription",
                            None,
                            "Retry speech transcription or enter notes",
                            "audio/survey.m4a",
                        )
                    )
            else:
                speech_status = "unconfigured"
                queue.append(
                    self._queue(
                        "transcription",
                        None,
                        "Configure server-side OpenAI voice to transcribe",
                        "audio/survey.m4a",
                    )
                )
        identities: list[dict] = []
        damage: list[dict] = []
        for mark in other:
            category = mark["category"]
            policy = asset_policy(category, bool(mark.get("high_value")))
            asset_id = mark.get("asset_copy_id") or f"asset_{mark['id']}"
            if asset_id in {asset["asset_copy_id"] for asset in assets}:
                raise ValueError("duplicate physical asset ID")
            asset = {
                "asset_copy_id": asset_id,
                "category": category,
                "observation_refs": [mark.get("evidence_ref", "")],
                **policy,
                "monotonic_seconds": mark.get("monotonic_seconds"),
                "room_id": mark.get("room_id"),
                "label": mark.get("label"),
                "camera_pose": mark.get("camera_pose"),
                "evidence_ref": mark.get("evidence_ref"),
            }
            if mark.get("stated_cost") is not None:
                asset["stated_cost"] = mark["stated_cost"]
                asset["stated_currency"] = mark.get("stated_currency")
            assets.append(asset)
            if policy["requires_appraisal"]:
                queue.append(
                    self._queue(
                        "high_value", asset_id, "Appraisal required", mark.get("evidence_ref")
                    )
                )
        for scan in payload.get("scans", []):
            asset_id = scan.get("asset_copy_id")
            asset = next((item for item in assets if item["asset_copy_id"] == asset_id), None)
            if (
                asset is None
                and scan.get("face_id")
                and scan.get("row_id")
                and scan.get("slot") is not None
            ):
                matches = [
                    item
                    for item in assets
                    if item.get("face_id") == scan["face_id"]
                    and item.get("row_id") == scan["row_id"]
                    and item.get("slot") == scan["slot"]
                ]
                if len(matches) == 1:
                    asset = matches[0]
                    asset_id = asset["asset_copy_id"]
            if asset is None:
                queue.append(
                    self._queue(
                        "unbound_scan",
                        asset_id,
                        "Select a physical asset",
                        scan.get("evidence_ref"),
                    )
                )
                continue
            if scan.get("kind") == "damage":
                closeup = scan.get("closeup_ref")
                scale = scan.get("scale_ref")
                closeup_ok = bool(closeup and repository.exists_bytes(survey_id, closeup))
                scale_ok = bool(scale and repository.exists_bytes(survey_id, scale))
                finding = {
                    "damage_id": scan["id"],
                    "asset_copy_id": asset_id,
                    "type": scan.get("damage_type", "unknown"),
                    "severity_candidate": scan.get("severity", "unknown"),
                    "region": scan.get("region"),
                    "source": "operator_observation",
                    "confidence": scan.get("confidence", 0.5),
                    "closeup_ref": closeup,
                    "scale_ref": scale,
                    "status": "assertion_needs_review",
                }
                damage.append(finding)
                if not closeup_ok or not scale_ok:
                    queue.append(
                        self._queue(
                            "damage_closeup", asset_id, "Capture close-up and scale", closeup
                        )
                    )
            elif scan.get("kind") in {"barcode", "title_page"}:
                title = scan.get("title") or title_from_ocr(scan.get("ocr_text")) or ""
                raw = scan.get("barcode") or scan.get("printed_identifier")
                if raw:
                    typed = type_identifier(raw, scan.get("identifier_kind"))
                    identity = {
                        "asset_copy_id": asset_id,
                        "raw": typed.raw,
                        "normalized": typed.normalized,
                        "kind": typed.kind,
                        "valid": typed.valid,
                        "usable_for_isbn_price_query": False,
                        "reason": typed.reason,
                        "scope": scan.get("scope", "volume"),
                        "evidence_ref": scan.get("evidence_ref"),
                        "ocr_text": scan.get("ocr_text", ""),
                        "ocr_confidence": scan.get("ocr_confidence"),
                    }
                    if typed.valid and typed.kind.startswith("isbn"):
                        identity["catalog"] = self.catalog.resolve(
                            repository, survey_id, typed.normalized, title=title
                        )
                        if identity["catalog"]["status"] == "candidate":
                            identity["usable_for_isbn_price_query"] = True
                            asset["isbn"] = typed.normalized
                            asset["book_edition_ref"] = f"edition_{typed.normalized}"
                        else:
                            queue.append(
                                self._queue(
                                    "catalog_review",
                                    asset_id,
                                    identity["catalog"]["reason"],
                                    scan.get("evidence_ref"),
                                )
                            )
                    elif not typed.valid:
                        asset["isbn"] = None
                        queue.append(
                            self._queue(
                                "rescan_barcode",
                                asset_id,
                                typed.reason or "Invalid identifier",
                                scan.get("evidence_ref"),
                            )
                        )
                    identities.append(identity)
                elif title:
                    catalog = self.catalog.resolve_title(
                        repository, survey_id, title, scan.get("author") or ""
                    )
                    identities.append(
                        {
                            "asset_copy_id": asset_id,
                            "title": title,
                            "author": scan.get("author") or "",
                            "status": "manual_title_match",
                            "evidence_ref": scan.get("evidence_ref"),
                            "catalog": catalog,
                        }
                    )
                    queue.append(
                        self._queue(
                            "manual_identity",
                            asset_id,
                            "Confirm title, author, and edition",
                            scan.get("evidence_ref"),
                        )
                    )
        for asset in assets:
            if asset["category"] == "book" and not any(
                item["asset_copy_id"] == asset["asset_copy_id"] for item in identities
            ):
                queue.append(
                    self._queue(
                        "unread_spine", asset["asset_copy_id"], "Scan barcode or title page", None
                    )
                )
        assertions = []
        focus_events = payload.get("focus_events") or []
        for note in [*notes, *payload.get("notes", [])]:
            note_id = note.get("id") or note.get("note_id")
            if not note_id:
                continue
            if note.get("monotonic_seconds") is not None and focus_events:
                nearby = sorted(
                    (
                        (
                            abs(
                                float(note["monotonic_seconds"]) - float(event["monotonic_seconds"])
                            ),
                            event,
                        )
                        for event in focus_events
                    ),
                    key=lambda item: item[0],
                )
                if nearby[0][0] <= 5 and (len(nearby) == 1 or nearby[1][0] - nearby[0][0] >= 1):
                    focus = nearby[0][1]
                    if focus.get("asset_copy_id"):
                        note.setdefault("reticle_asset_id", focus["asset_copy_id"])
                    elif focus.get("face_id"):
                        for field in ("face_id", "row_id", "slot"):
                            note.setdefault(field, focus.get(field))
                    note.setdefault("camera_pose", focus.get("camera_pose"))
            bound, method = _associate(note, assets)
            assertion = {
                "note_id": str(note_id),
                "text": note.get("text", ""),
                "monotonic_seconds": note.get("monotonic_seconds"),
                "ended_monotonic_seconds": note.get("ended_monotonic_seconds"),
                "asset_copy_id": bound,
                "association_method": method,
                "status": "operator_assertion" if bound else "unbound",
            }
            related_damage = next((d for d in damage if d["asset_copy_id"] == bound), None)
            if related_damage:
                assertion["damage_id"] = related_damage["damage_id"]
                assertion["closeup_ref"] = related_damage["closeup_ref"]
            assertions.append(assertion)
            if bound is None:
                queue.append(
                    self._queue("unbound_note", None, "Bind note to an asset", str(note_id))
                )
            elif "damag" in assertion["text"].lower() and not any(
                d["asset_copy_id"] == bound for d in damage
            ):
                queue.append(
                    self._queue(
                        "damage_closeup", bound, "Capture damage close-up and scale", str(note_id)
                    )
                )
        editions = {}
        works = {}
        for identity in identities:
            catalog = identity.get("catalog") or {}
            if catalog.get("status") != "candidate" or not identity.get("valid"):
                continue
            isbn = identity["normalized"]
            edition_id = f"edition_{isbn}"
            work_key = (catalog.get("work_refs") or [catalog.get("title", isbn)])[0]
            work_id = "work_" + sha256_bytes(str(work_key).encode())[:12]
            works[work_id] = {
                "work_id": work_id,
                "title": catalog["title"],
                "authors": catalog.get("authors", []),
                "source": catalog["source"],
                "source_record_id": work_key,
            }
            editions[edition_id] = {
                "book_edition_id": edition_id,
                "work_ref": work_id,
                "isbn": isbn,
                "title": catalog["title"],
                "publisher": catalog.get("publisher"),
                "edition": catalog.get("edition"),
                "scope": identity.get("scope", "volume"),
                "catalog_source": catalog["source"],
                "evidence_ref": identity.get("evidence_ref"),
            }
        result = {
            "schema_version": "stage3-v1",
            "survey_id": str(survey_id),
            "taxonomy_version": "closed-v1",
            "assets": assets,
            "identities": identities,
            "book_editions": list(editions.values()),
            "works": list(works.values()),
            "damage": damage,
            "notes": assertions,
            "queue": queue,
            "decisions": [],
            "speech_status": speech_status,
        }
        repository.save_json(survey_id, "stage3", result)
        if inventory:
            inventory["asset_copies"] = [
                {key: value for key, value in asset.items() if key in ASSET_COPY_FIELDS}
                for asset in assets
                if asset["category"] == "book"
            ]
            repository.save_json(survey_id, "inventory", inventory)
        ir = repository.get_json(survey_id, "ir") or {
            "schema_version": "1.0.0",
            "survey_id": str(survey_id),
        }
        ir["asset_copies"] = assets
        ir["identifiers"] = identities
        ir["book_editions"] = list(editions.values())
        ir["works"] = list(works.values())
        ir["damage_observations"] = damage
        ir["notes"] = assertions
        repository.save_json(survey_id, "ir", ir)
        return result

    @staticmethod
    def _read(repository: SurveyRepository, survey_id: UUID, path: str, default):
        if not repository.exists_bytes(survey_id, path):
            return default
        return json.loads(repository.get_bytes(survey_id, path))

    @staticmethod
    def _queue(kind: str, asset_id: str | None, message: str, evidence_ref: str | None) -> dict:
        digest = sha256_bytes(f"{kind}|{asset_id}|{evidence_ref}|{message}".encode())[:12]
        return {
            "id": f"review_{kind}_{digest}",
            "kind": kind,
            "asset_copy_id": asset_id,
            "message": message,
            "evidence_ref": evidence_ref,
            "status": "open",
        }


def apply_review(repository: SurveyRepository, survey_id: UUID, decision: dict) -> dict:
    result = repository.get_json(survey_id, "stage3")
    if result is None:
        raise ValueError("Stage 3 review is unavailable")
    action = decision.get("action")
    if action not in {"bind_note", "rescan_barcode", "keep_unresolved"}:
        raise ValueError("unsupported review action")
    target = next(
        (item for item in result["queue"] if item["id"] == decision.get("queue_id")), None
    )
    if target is None or target["status"] != "open":
        raise ValueError("review item is missing or already decided")
    if action == "bind_note":
        asset_id = decision.get("asset_copy_id")
        if asset_id not in {asset["asset_copy_id"] for asset in result["assets"]}:
            raise ValueError("unknown physical asset")
        note = next(
            (item for item in result["notes"] if item["note_id"] == target["evidence_ref"]), None
        )
        if note is None:
            raise ValueError("queue item is not an unbound note")
        note.update(
            asset_copy_id=asset_id, association_method="human_review", status="operator_assertion"
        )
    target["status"] = (
        "resolved"
        if action == "bind_note"
        else ("rescan_requested" if action == "rescan_barcode" else "kept_unresolved")
    )
    result["decisions"].append(
        {
            "queue_id": target["id"],
            "action": action,
            "asset_copy_id": decision.get("asset_copy_id"),
            "reason": decision.get("reason", ""),
        }
    )
    repository.save_json(survey_id, "stage3", result)
    transition = {
        "schema_version": "1.0.0", "transition_id": str(uuid4()),
        "survey_id": str(survey_id), "policy_id": "human_review_v1",
        "state": {
            "queue_id": target["id"], "queue_kind": target["kind"],
            "asset_copy_id": decision.get("asset_copy_id") or target.get("asset_copy_id"),
            "evidence_ref": target.get("evidence_ref"),
            "operator_action": action,
        },
        "action": "recapture" if action == "rescan_barcode" else "human_review",
        "action_source": "human", "fable": None, "astra": None, "jev": None,
        "human_truth": None, "independent_outcome": None, "reward": None,
        "next_state_id": None, "cost_usd": 0.0, "elapsed_ms": 0,
    }
    repository.redis.rpush(
        f"{repository.key_prefix}:survey:{survey_id}:rl_transitions",
        json.dumps(transition, sort_keys=True),
    )
    return result
