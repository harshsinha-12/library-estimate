"""Stage 4 pricing, spoken object costs, building reconstruction, and overview."""

from __future__ import annotations

import base64
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from statistics import median
from uuid import UUID, uuid4

from backend.app.domain.models import AssetCopy, InventoryResult, Observation
from backend.app.domain.repository import SurveyRepository
from backend.app.providers.pricing import log as pricing_log
from backend.app.providers.pricing.parse import is_shop_url, parse_spoken_cost
from backend.app.providers.pricing.queries import (
    template_object_query,
    template_query,
    title_from_ocr,
)
from backend.app.providers.pricing.schema import BATCH_SIZE, DEFAULT_SMALL_MODEL
from backend.app.providers.pricing.targets import (
    MAX_SEARCHES_PER_OBJECT,
    already_priced,
    attach_frames,
    attempt_count,
    category_from_speech,
    fallback_targets,
    format_timeline,
    keys_match,
    object_key,
    plan_price_targets,
    priced_search,
    record_attempt,
    timed_visuals,
)
from backend.app.providers.pricing.vision_titles import describe_object, extract_book_titles
from backend.app.providers.pricing.web_search import search_book_price, search_price_batch
from backend.app.utils.clocks import utc_now
from backend.app.utils.hashing import sha256_bytes
from backend.app.workflows.identifiers import type_identifier
from backend.app.workflows.stage3 import TAXONOMY, asset_policy

RATES_PATH = Path(__file__).resolve().parents[1] / "data" / "demo_rebuild_rates_v1.json"
PIPELINE_VERSION = "stage4-price-v1"
DRAFT_REASON = "Web search citations are drafts until a technician confirms a physical offer"
_GENERIC_TITLE = re.compile(
    r"^(books on the bookshelf|unidentified book.*|isbn\s*\d+)$",
    re.IGNORECASE,
)


def load_rebuild_rates() -> dict:
    return json.loads(RATES_PATH.read_text(encoding="utf-8"))


class PricingWorker:
    def __init__(
        self,
        small_model: str = DEFAULT_SMALL_MODEL,
        extract_titles=extract_book_titles,
        describe_object=describe_object,
        web_search=search_book_price,
        web_search_batch=search_price_batch,
        plan_targets=plan_price_targets,
    ) -> None:
        self.small_model = small_model
        self.extract_titles = extract_titles
        self.describe_object = describe_object
        self.web_search = web_search
        self.web_search_batch = web_search_batch
        self.plan_targets = plan_targets

    def overview(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        survey = repository.get(survey_id)
        stage3 = repository.get_json(survey_id, "stage3") or {}
        inventory = repository.get_json(survey_id, "inventory") or {}
        state = self._state(repository, survey_id)
        copies = self._copy_rows(stage3, inventory, state, survey.geography.model_dump(mode="json"))
        repository.save_json(survey_id, "pricing", state)
        building = self._building(repository, survey_id, survey.geography.model_dump(mode="json"))
        rows = self._row_details(inventory, copies)
        eligible = [item for item in copies if item["eligible"]]
        priced = [item for item in eligible if item["valuation_status"] in {"quoted", "manual"}]
        contents = self._contents_range(copies, survey.geography.currency)
        unresolved = [
            item
            for item in copies
            if item["valuation_status"]
            in {"price_pending", "no_comparable", "unavailable", "requires_appraisal"}
            or item.get("identity_task")
        ]
        payload = {
            "survey_id": str(survey_id),
            "display_name": survey.display_name,
            "geography": survey.geography.model_dump(mode="json"),
            "city_market": (
                f"{survey.geography.city}, {survey.geography.country_code} · "
                f"{survey.geography.market}"
            ),
            "copy_count": len(copies),
            "edition_count": len(
                {item["edition_key"] for item in copies if item.get("edition_key")}
            ),
            "eligible_count": len(eligible),
            "priced_count": len(priced),
            "priced_eligible": {"numerator": len(priced), "denominator": len(eligible)},
            "unresolved_count": len(unresolved),
            "contents": contents,
            "building": building,
            "shelf_face_data_sizes": inventory.get("shelf_face_data_sizes") or [],
            "recapture": inventory.get("recapture") or [],
            "rows": rows,
            "copies": copies,
            "live_searches": state.get("live_searches") or [],
            "found_prices": state.get("found_prices") or [],
            "log": (state.get("log") or [])[-20:],
            "ledger": state["ledger"],
            "rate_table_version": building.get("rate_table_version"),
        }
        repository.save_json(survey_id, "overview", payload)
        ir = repository.get_json(survey_id, "ir") or {
            "schema_version": "1.0.0",
            "survey_id": str(survey_id),
        }
        ir["price_observations"] = state["observations"]
        ir["valuations"] = [
            item["valuation"] for item in copies if item.get("valuation") is not None
        ]
        ir["building_valuation"] = building
        repository.save_json(survey_id, "ir", ir)
        return payload

    def search_asset(self, repository: SurveyRepository, survey_id: UUID, asset_id: str) -> dict:
        survey = repository.get(survey_id)
        geography = survey.geography.model_dump(mode="json")
        stage3 = repository.get_json(survey_id, "stage3") or {}
        inventory = repository.get_json(survey_id, "inventory") or {}
        state = self._state(repository, survey_id)
        copies = self._copy_rows(stage3, inventory, state, geography)
        target = next((item for item in copies if item["asset_copy_id"] == asset_id), None)
        if target is None:
            raise ValueError("unknown physical asset")
        if not target["eligible"]:
            return {
                "asset_copy_id": asset_id,
                "status": target["valuation_status"],
                "reason": target["reason"],
                "search": None,
                "copies": self._refresh(repository, survey_id, state, geography, stage3, inventory),
            }
        if target["query"] is None:
            target["valuation_status"] = "price_pending"
            target["reason"] = "Identity is unresolved; scan a barcode or title page"
            return {
                "asset_copy_id": asset_id,
                "status": "price_pending",
                "reason": target["reason"],
                "search": None,
                "copies": self._refresh(repository, survey_id, state, geography, stage3, inventory),
            }
        search = self._search(
            repository,
            survey_id,
            state,
            geography,
            query=target["query"],
            kind=target["query_kind"],
            edition_key=target.get("edition_key") or target["asset_copy_id"],
            book_title=target.get("title"),
            isbn=target.get("isbn"),
            description=target.get("title"),
        )
        self._attach_drafts(state, search, geography)
        copies = self._refresh(repository, survey_id, state, geography, stage3, inventory)
        return {
            "asset_copy_id": asset_id,
            "status": "draft",
            "reason": DRAFT_REASON,
            "search": search,
            "drafts": [
                item
                for item in state["observations"]
                if item["search_id"] == search["search_id"] and item["review_status"] == "draft"
            ],
            "copies": copies,
        }

    def live_search(self, repository: SurveyRepository, survey_id: UUID, payload: dict) -> dict:
        category = str(payload.get("category") or "book").strip().lower() or "book"
        if category not in {"book", "serial"}:
            return self._object_search(repository, survey_id, payload, category)
        survey = repository.get(survey_id)
        geography = survey.geography.model_dump(mode="json")
        isbn = str(payload.get("isbn") or "").strip() or None
        barcode = str(payload.get("barcode") or "").strip() or None
        title = str(payload.get("title") or "").strip() or None
        if barcode and not isbn:
            typed = type_identifier(barcode, payload.get("identifier_kind"))
            if typed.valid and typed.kind.startswith("isbn"):
                isbn = typed.normalized
        jpeg = _jpeg_from_payload(payload)
        vision_titles: list[str] = []
        if jpeg:
            try:
                vision_titles = list(self.extract_titles(jpeg) or [])
            except (OSError, TypeError, ValueError):
                vision_titles = []
            title = title or (vision_titles[0] if vision_titles else None)
        title = title or title_from_ocr(payload.get("ocr_text"))
        description = " ".join(
            part
            for part in (
                title,
                str(payload.get("spoken_text") or "").strip(),
                str(payload.get("ocr_text") or "").strip(),
                *vision_titles,
            )
            if part
        )
        unresolved = {
            "status": "unresolved",
            "reason": (
                "No book name or ISBN yet. Point the camera at the cover or barcode "
                "and scan again."
            ),
            "title": title,
            "isbn": isbn,
            "query": None,
            "query_kind": None,
            "amount": None,
            "currency": geography["currency"],
            "listing_url": None,
            "listing_urls": [],
            "listing_count": 0,
            "parser": None,
            "small_model": self.small_model,
            "citations": [],
        }
        built = template_query(
            isbn=isbn,
            title=title,
            author=None,
            publisher=None,
            edition=None,
            country_code=geography["country_code"],
            city=geography.get("city"),
            currency=geography.get("currency"),
        )
        if built is None:
            seed = (title or description or "").strip()
            if len(seed) < 3:
                return unresolved
            query, kind = seed[:200], "name"
        else:
            query, kind = built
        state = self._state(repository, survey_id)
        key = object_key(kind=kind, title=title, isbn=isbn)
        cached = priced_search(state, key)
        if cached:
            return cached
        if already_priced(state, key):
            return unresolved | {"status": "draft", "reason": "Price already found for this copy"}
        if attempt_count(state, key) >= MAX_SEARCHES_PER_OBJECT:
            return {
                **unresolved,
                "reason": "No local physical comparable after 5 searches",
                "title": title,
                "query": query,
                "query_kind": kind,
            }
        search = self._search(
            repository,
            survey_id,
            state,
            geography,
            query=query,
            kind=kind,
            edition_key=f"live:{kind}:{sha256_bytes(query.encode())[:12]}",
            book_title=title,
            isbn=isbn,
            description=description,
        )
        physical = [
            item
            for item in search.get("citations") or []
            if item.get("offer_type") == "physical" and item.get("parsed_amount")
        ]
        chosen = physical[0] if physical else None
        result = {
            "status": "draft" if chosen else "unresolved",
            "reason": (
                DRAFT_REASON
                if chosen
                else "No local physical comparable. Scan the cover or barcode again."
            ),
            "title": title,
            "isbn": isbn,
            "query": search.get("query"),
            "query_kind": kind,
            "amount": chosen.get("parsed_amount") if chosen else None,
            "currency": (chosen or {}).get("parsed_currency") or geography["currency"],
            "listing_url": search.get("listing_url"),
            "listing_urls": (search.get("listing_urls") or [])[:5],
            "listing_count": min(len(search.get("listing_urls") or []), 5),
            "parser": search.get("parser"),
            "small_model": search.get("small_model") or self.small_model,
            "citations": search.get("citations") or [],
        }
        state.setdefault("live_searches", []).append(
            {**result, "occurred_at": utc_now().isoformat()}
        )
        state["live_searches"] = state["live_searches"][-40:]
        repository.save_json(survey_id, "pricing", state)
        self._record_log(
            repository,
            survey_id,
            "live_book_search",
            title=title,
            status=result["status"],
            parser=result.get("parser"),
            model=result.get("small_model"),
            listings=result.get("listing_count"),
        )
        return result

    def _object_search(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        payload: dict,
        category: str,
    ) -> dict:
        survey = repository.get(survey_id)
        geography = survey.geography.model_dump(mode="json")
        spoken = str(payload.get("spoken_text") or payload.get("ocr_text") or "").strip()
        label = str(payload.get("title") or payload.get("label") or "").strip()
        extra = ""
        jpeg = _jpeg_from_payload(payload)
        if jpeg:
            try:
                described = self.describe_object(jpeg, spoken=spoken, category=category) or {}
            except (OSError, TypeError, ValueError):
                described = {}
            label = label or str(described.get("label") or "").strip()
            extra = str(described.get("details") or "").strip()
            if extra and extra.lower() not in (label + spoken).lower():
                spoken = " ".join(part for part in (spoken, extra) if part)
        description = " ".join(
            part for part in (label, spoken, extra if jpeg else None, category) if part
        )
        built = template_object_query(
            label=label or None,
            category=category,
            spoken=spoken or None,
            country_code=geography["country_code"],
            city=geography.get("city"),
            currency=geography.get("currency"),
        )
        unresolved = {
            "status": "unresolved",
            "reason": (
                "Point the camera at the object and say what it is, then scan again."
            ),
            "title": label or None,
            "isbn": None,
            "query": None,
            "query_kind": "object",
            "amount": None,
            "currency": geography["currency"],
            "listing_url": None,
            "listing_urls": [],
            "listing_count": 0,
            "parser": None,
            "small_model": self.small_model,
            "citations": [],
            "category": category,
        }
        if built is None:
            seed = (description or label or spoken or category or "").strip()
            if len(seed) < 3:
                return unresolved
            query, kind = seed[:200], "object"
        else:
            query, kind = built
        state = self._state(repository, survey_id)
        key = object_key(kind=kind, title=label or spoken, category=category)
        cached = priced_search(state, key)
        if cached:
            return cached
        if already_priced(state, key):
            return unresolved | {
                "status": "draft",
                "title": label or spoken or category,
                "reason": "Price already found for this object",
            }
        if attempt_count(state, key) >= MAX_SEARCHES_PER_OBJECT:
            return {
                **unresolved,
                "title": label or spoken or category,
                "query": query,
                "reason": "No local physical comparable after 5 searches",
            }
        search = self._search(
            repository,
            survey_id,
            state,
            geography,
            query=query,
            kind=kind,
            edition_key=f"live:{kind}:{sha256_bytes(query.encode())[:12]}",
            book_title=label or spoken,
            description=description,
        )
        physical = [
            item
            for item in search.get("citations") or []
            if item.get("offer_type") == "physical" and item.get("parsed_amount")
        ]
        chosen = physical[0] if physical else None
        result = {
            "status": "draft" if chosen else "unresolved",
            "reason": (
                DRAFT_REASON
                if chosen
                else "No local physical comparable. Capture the object again."
            ),
            "title": label or spoken or category,
            "isbn": None,
            "query": search.get("query"),
            "query_kind": kind,
            "amount": chosen.get("parsed_amount") if chosen else None,
            "currency": (chosen or {}).get("parsed_currency") or geography["currency"],
            "listing_url": search.get("listing_url"),
            "listing_urls": (search.get("listing_urls") or [])[:5],
            "listing_count": min(len(search.get("listing_urls") or []), 5),
            "parser": search.get("parser"),
            "small_model": search.get("small_model") or self.small_model,
            "citations": search.get("citations") or [],
            "category": category,
        }
        asset_id = str(payload.get("asset_copy_id") or "").strip() or None
        if chosen and asset_id:
            state["observations"].append(
                self._observation(
                    survey_id,
                    geography,
                    query=search.get("query") or query,
                    kind=kind,
                    edition_key=asset_id,
                    citation=chosen,
                    review_status="draft",
                    asset_copy_id=asset_id,
                    search_id=search.get("search_id"),
                )
            )
        state.setdefault("live_searches", []).append(
            {**result, "occurred_at": utc_now().isoformat()}
        )
        state["live_searches"] = state["live_searches"][-40:]
        repository.save_json(survey_id, "pricing", state)
        return result

    def identify_from_frames(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        stage3 = repository.get_json(survey_id, "stage3") or {}
        assets = [item for item in stage3.get("assets") or [] if item.get("category") == "book"]
        identities = list(stage3.get("identities") or [])
        identified = {item.get("asset_copy_id") for item in identities if item.get("title")}
        unread = [item for item in assets if item["asset_copy_id"] not in identified]
        titles: list[str] = []
        seen: set[str] = set()
        frame_paths = _frame_paths(repository, survey_id)
        for path in frame_paths[:12]:
            try:
                jpeg = repository.get_bytes(survey_id, path)
            except FileNotFoundError:
                continue
            try:
                extracted = self.extract_titles(jpeg) or []
            except (OSError, TypeError, ValueError):
                extracted = []
            for title in extracted:
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                titles.append(title)
        survey = repository.get(survey_id)
        geography = survey.geography.model_dump(mode="json")
        state = self._state(repository, survey_id)
        searches = []
        jobs: list[dict] = []
        for title in titles[:8]:
            key = object_key(kind="book", title=title)
            if already_priced(state, key):
                cached = priced_search(state, key)
                if cached:
                    searches.append(cached)
                continue
            if attempt_count(state, key) >= MAX_SEARCHES_PER_OBJECT:
                continue
            jobs.append(
                {
                    "query": title,
                    "kind": "name",
                    "edition_key": f"live:name:{sha256_bytes(title.encode())[:12]}",
                    "book_title": title,
                    "isbn": None,
                    "description": title,
                }
            )
        if jobs:
            searches.extend(self._search_many(repository, survey_id, state, geography, jobs))
        if unread and titles:
            for asset, title in zip(unread, titles, strict=False):
                identities.append(
                    {
                        "asset_copy_id": asset["asset_copy_id"],
                        "title": title,
                        "author": "",
                        "status": "vision_title",
                        "evidence_ref": "shelf_scans/frames",
                    }
                )
            assigned = {item["asset_copy_id"] for item in identities if item.get("title")}
            for item in stage3.get("queue") or []:
                if (
                    item.get("kind") == "unread_spine"
                    and item.get("asset_copy_id") in assigned
                    and item.get("status") == "open"
                ):
                    item["status"] = "closed"
                    item["message"] = "Title read from shelf frame"
            stage3["identities"] = identities
            repository.save_json(survey_id, "stage3", stage3)
        if titles:
            self._promote_titles(repository, survey_id, titles, frame_paths[:1])
            stage3 = repository.get_json(survey_id, "stage3") or {}
            assets = [item for item in stage3.get("assets") or [] if item.get("category") == "book"]
        self._record_log(
            repository,
            survey_id,
            "identify_from_frames",
            titles=len(titles),
            frames=len(frame_paths),
            copy_count=len(assets),
            searches=len(searches),
        )
        return {"titles": titles, "searches": searches, "copy_count": len(assets)}

    def price_spoken_notes(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        stage3 = repository.get_json(survey_id, "stage3") or {}
        notes = list(stage3.get("notes") or [])
        poses = _room_poses(repository, survey_id)
        try:
            payload = json.loads(repository.get_bytes(survey_id, "other_assets/marks.json"))
            marks = payload if isinstance(payload, list) else []
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            marks = []
        visuals = timed_visuals(poses, marks)
        timeline = format_timeline(notes, visuals)
        state = self._state(repository, survey_id)
        priced_names = [
            str(item.get("title") or "")
            for item in state.get("found_prices") or []
            if item.get("amount")
        ]
        planned: list[dict] = []
        if timeline:
            try:
                planned = list(self.plan_targets(timeline, already=priced_names) or [])
            except (OSError, TypeError, ValueError):
                planned = []
        if not planned:
            planned = fallback_targets(notes, visuals)
        planned = attach_frames(planned, notes, visuals)
        searches = []
        seen: set[str] = set()
        for item in planned:
            name = str(item.get("name") or "").strip()
            kind = str(item.get("kind") or "object")
            category = str(item.get("category") or ("book" if kind == "book" else "object"))
            if not name or category == "cup":
                continue
            key = object_key(kind=kind, title=name, category=category)
            if any(keys_match(key, other) for other in seen):
                continue
            seen.add(key)
            if already_priced(state, key) or attempt_count(state, key) >= MAX_SEARCHES_PER_OBJECT:
                cached = priced_search(state, key)
                if cached:
                    searches.append(cached)
                continue
            encoded = _jpeg_at(repository, survey_id, item.get("frame_path"))
            description = " ".join(
                part
                for part in (name, item.get("description"), item.get("speech"), timeline[:400])
                if part
            )
            payload = {
                "title": name,
                "spoken_text": str(item.get("speech") or item.get("description") or name),
                "ocr_text": description,
                "image_base64": encoded,
                "asset_copy_id": item.get("asset_copy_id"),
            }
            if kind == "book":
                result = self.live_search(repository, survey_id, payload)
            else:
                result = self._object_search(repository, survey_id, payload, category)
            searches.append(result)
            state = self._state(repository, survey_id)
            if len(searches) >= 8:
                break
        self._record_log(
            repository,
            survey_id,
            "spoken_object_search",
            searches=len(searches),
            timeline_lines=timeline.count("\n") + (1 if timeline else 0),
        )
        return {"searches": searches, "timeline": timeline, "targets": planned}

    def after_seal(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        pricing_log.info(
            "pricing_after_seal start",
            survey_id=str(survey_id),
            model=self.small_model,
        )
        identified = self.identify_from_frames(repository, survey_id)
        spoken = self.price_spoken_notes(repository, survey_id)
        self._promote_searches(repository, survey_id)
        queued = self.queue(repository, survey_id)
        overview = self.overview(repository, survey_id)
        replayed = self._replay_sample(repository, survey_id)
        return {
            "identified": identified,
            "spoken": spoken,
            "queued": queued,
            "overview_copies": len(overview.get("copies") or []),
            "replayed": replayed,
        }

    def queue(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        self.identify_from_frames(repository, survey_id)
        survey = repository.get(survey_id)
        geography = survey.geography.model_dump(mode="json")
        stage3 = repository.get_json(survey_id, "stage3") or {}
        inventory = repository.get_json(survey_id, "inventory") or {}
        state = self._state(repository, survey_id)
        copies = self._copy_rows(stage3, inventory, state, geography)
        queued = []
        seen: set[str] = set()
        jobs: list[dict] = []
        for copy in copies:
            if not copy["eligible"] or copy["query"] is None:
                continue
            key = copy.get("edition_key") or copy["asset_copy_id"]
            lookup = object_key(
                kind=copy["query_kind"],
                title=copy.get("title"),
                isbn=copy.get("isbn"),
            )
            cache_id = f"{key}|{geography['market']}|{copy['query']}"
            if cache_id in seen:
                continue
            seen.add(cache_id)
            if already_priced(state, lookup):
                prior = priced_search(state, lookup)
                if prior:
                    queued.append(prior)
                continue
            if attempt_count(state, lookup) >= MAX_SEARCHES_PER_OBJECT:
                continue
            jobs.append(
                {
                    "query": copy["query"],
                    "kind": copy["query_kind"],
                    "edition_key": key,
                    "book_title": copy.get("title"),
                    "isbn": copy.get("isbn"),
                    "description": copy.get("title"),
                }
            )
        queued.extend(self._search_many(repository, survey_id, state, geography, jobs))
        self._attach_drafts(state, None, geography)
        copies = self._refresh(repository, survey_id, state, geography, stage3, inventory)
        return {"queued": queued, "copies": copies, "ledger": state["ledger"]}

    def apply_observation(
        self, repository: SurveyRepository, survey_id: UUID, asset_id: str, payload: dict
    ) -> dict:
        survey = repository.get(survey_id)
        geography = survey.geography.model_dump(mode="json")
        stage3 = repository.get_json(survey_id, "stage3") or {}
        inventory = repository.get_json(survey_id, "inventory") or {}
        state = self._state(repository, survey_id)
        copies = self._copy_rows(stage3, inventory, state, geography)
        target = next((item for item in copies if item["asset_copy_id"] == asset_id), None)
        if target is None:
            raise ValueError("unknown physical asset")
        if not target["eligible"]:
            raise ValueError("this object is excluded from valuation")
        action = payload.get("action")
        if action == "confirm":
            observation = next(
                (
                    item
                    for item in state["observations"]
                    if item["price_observation_id"] == payload.get("price_observation_id")
                    and item["review_status"] == "draft"
                ),
                None,
            )
            if observation is None:
                raise ValueError("draft price observation is missing")
            if observation["offer_type"] != "physical":
                raise ValueError("only a physical-book offer can price a physical copy")
            if observation.get("parsed_amount") is None:
                raise ValueError("confirm a draft that includes a parsed physical price")
            confirmed = {
                **observation,
                "price_observation_id": str(uuid4()),
                "review_status": "accepted",
                "asset_copy_id": asset_id,
                "condition": payload.get("condition") or observation.get("condition"),
                "format": payload.get("format") or observation.get("format"),
            }
            state["observations"].append(confirmed)
        elif action == "reject":
            observation = next(
                (
                    item
                    for item in state["observations"]
                    if item["price_observation_id"] == payload.get("price_observation_id")
                ),
                None,
            )
            if observation is None:
                raise ValueError("price observation is missing")
            observation["review_status"] = "rejected"
        elif action == "manual":
            amount = payload.get("amount")
            reason = (payload.get("reason") or "").strip()
            if amount is None or not reason:
                raise ValueError("manual price requires an amount and a reason")
            state["observations"].append(
                self._observation(
                    survey_id,
                    geography,
                    query=payload.get("query") or "manual technician entry",
                    kind="manual",
                    edition_key=target.get("edition_key") or asset_id,
                    citation={
                        "title": "Manual replacement evidence",
                        "url": payload.get("source_url") or "",
                        "snippet": reason,
                        "offer_type": "physical",
                        "parsed_amount": str(amount),
                        "parsed_currency": payload.get("currency") or geography["currency"],
                    },
                    review_status="accepted",
                    asset_copy_id=asset_id,
                    condition=payload.get("condition"),
                    format=payload.get("format"),
                )
            )
        elif action == "no_comparable":
            state["observations"].append(
                {
                    "price_observation_id": str(uuid4()),
                    "asset_copy_id": asset_id,
                    "review_status": "rejected",
                    "offer_type": "unknown",
                    "reason": payload.get("reason") or "No local physical comparable",
                    "query": target.get("query") or "",
                    "market": geography["market"],
                }
            )
            target_reason = payload.get("reason") or "No local physical comparable"
            state.setdefault("no_comparable", {})[asset_id] = target_reason
        else:
            raise ValueError("unsupported price observation action")
        copies = self._refresh(repository, survey_id, state, geography, stage3, inventory)
        return {"asset_copy_id": asset_id, "copies": copies, "observations": state["observations"]}

    def _refresh(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        state: dict,
        geography: dict,
        stage3: dict,
        inventory: dict,
    ) -> list[dict]:
        repository.save_json(survey_id, "pricing", state)
        return self.overview(repository, survey_id)["copies"]

    def _state(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        stored = repository.get_json(survey_id, "pricing")
        if stored is not None:
            stored.setdefault("no_comparable", {})
            stored.setdefault("live_searches", [])
            stored.setdefault("found_prices", [])
            stored.setdefault("search_attempts", {})
            stored.setdefault("log", [])
            ledger = stored.setdefault("ledger", {"currency": "USD", "lines": []})
            ledger.pop("cap_usd", None)
            ledger.pop("spent_usd", None)
            ledger.setdefault("lines", [])
            return stored
        return {
            "schema_version": "stage4-v1",
            "pipeline_version": PIPELINE_VERSION,
            "observations": [],
            "searches": [],
            "live_searches": [],
            "found_prices": [],
            "search_attempts": {},
            "log": [],
            "no_comparable": {},
            "ledger": {"currency": "USD", "lines": []},
        }

    def _remember_found(
        self,
        state: dict,
        *,
        title: str | None,
        query: str,
        search_id: str,
        citations: list[dict],
        isbn: str | None = None,
    ) -> None:
        found = state.setdefault("found_prices", [])
        existing = {
            (
                str(item.get("title") or "").lower(),
                str(item.get("isbn") or ""),
                item.get("url"),
                str(item.get("amount")),
            )
            for item in found
        }
        for citation in citations:
            amount = citation.get("parsed_amount")
            url = citation.get("url") or ""
            if not amount:
                continue
            if citation.get("offer_type") != "physical" and not is_shop_url(url):
                continue
            key = (
                str(title or citation.get("title") or "").lower(),
                str(isbn or ""),
                citation.get("url"),
                str(amount),
            )
            if key in existing:
                continue
            existing.add(key)
            found.append(
                {
                    "title": title or citation.get("title"),
                    "isbn": isbn,
                    "amount": str(amount),
                    "currency": citation.get("parsed_currency"),
                    "url": citation.get("url"),
                    "offer_type": citation.get("offer_type"),
                    "format": citation.get("format"),
                    "query": query,
                    "search_id": search_id,
                    "found_at": utc_now().isoformat(),
                }
            )
        state["found_prices"] = found[-200:]

    def _record_log(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        event: str,
        **fields: object,
    ) -> None:
        state = self._state(repository, survey_id)
        line = {"event": event, "at": utc_now().isoformat()}
        for key, value in fields.items():
            if value is not None:
                line[key] = value
        state.setdefault("log", []).append(line)
        state["log"] = state["log"][-80:]
        repository.save_json(survey_id, "pricing", state)
        pricing_log.info(event, survey_id=str(survey_id), **fields)

    def _search(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        state: dict,
        geography: dict,
        *,
        query: str,
        kind: str,
        edition_key: str,
        book_title: str | None = None,
        isbn: str | None = None,
        description: str | None = None,
    ) -> dict:
        stored = self._search_many(
            repository,
            survey_id,
            state,
            geography,
            [
                {
                    "query": query,
                    "kind": kind,
                    "edition_key": edition_key,
                    "book_title": book_title,
                    "isbn": isbn,
                    "description": description,
                }
            ],
        )
        return stored[0]

    def _search_many(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        state: dict,
        geography: dict,
        jobs: list[dict],
    ) -> list[dict]:
        results: list[dict] = []
        pending: list[dict] = []
        for job in jobs:
            lookup = object_key(
                kind=job["kind"],
                title=job.get("book_title") or job["query"],
                category="object" if job["kind"] == "object" else None,
                isbn=job.get("isbn"),
            )
            job["lookup"] = lookup
            if already_priced(state, lookup):
                prior = priced_search(state, lookup)
                if prior and prior.get("search_id") and prior.get("edition_key"):
                    results.append(prior)
                    continue
                results.append(
                    self._empty_search(job, geography, prior=prior, parser="openai_web_search")
                )
                continue
            if attempt_count(state, lookup) >= MAX_SEARCHES_PER_OBJECT:
                results.append(self._empty_search(job, geography, parser="skipped"))
                continue
            pending.append(job)
            results.append(job)
        for offset in range(0, len(pending), BATCH_SIZE):
            chunk = pending[offset : offset + BATCH_SIZE]
            for job in chunk:
                record_attempt(state, job["lookup"])
            parsed_rows = self._run_batch(chunk, geography)
            for job, parsed in zip(chunk, parsed_rows, strict=False):
                stored = self._store_search(
                    repository, survey_id, state, geography, job, parsed
                )
                for index, item in enumerate(results):
                    if item is job:
                        results[index] = stored
                        break
        return [
            item if item.get("search_id") else self._empty_search(item, geography)
            for item in results
        ]

    def _run_batch(self, jobs: list[dict], geography: dict) -> list[dict | None]:
        payload = [
            {
                "title": job.get("book_title") or job["query"],
                "isbn": job.get("isbn"),
                "kind": job["kind"],
                "description": job.get("description"),
            }
            for job in jobs
        ]
        try:
            if len(payload) == 1:
                row = self.web_search(
                    payload[0]["title"],
                    geography=geography,
                    isbn=payload[0]["isbn"],
                    kind=payload[0]["kind"],
                    description=payload[0]["description"],
                )
                return [row]
            return list(self.web_search_batch(payload, geography=geography) or [])
        except (OSError, TypeError, ValueError):
            return [None] * len(jobs)

    def _empty_search(
        self,
        job: dict,
        geography: dict,
        *,
        prior: dict | None = None,
        parser: str = "openai_web_search",
    ) -> dict:
        listing = (prior or {}).get("listing_url") or ""
        return {
            "search_id": sha256_bytes(
                f"found|{job.get('lookup') or job.get('query')}".encode()
            )[:16],
            "edition_key": job["edition_key"],
            "query_kind": job["kind"],
            "query": job.get("book_title") or job["query"],
            "market": geography["market"],
            "listing_url": listing,
            "citations": (prior or {}).get("citations") or [],
            "html_sha256": (prior or {}).get("html_sha256") or "",
            "listing_urls": (prior or {}).get("listing_urls") or [],
            "listing_count": min(len((prior or {}).get("listing_urls") or []), 5),
            "parser": (prior or {}).get("parser") or parser,
            "small_model": (prior or {}).get("small_model") or self.small_model,
            "from_cache": True,
            "country_code": geography["country_code"],
            "isbn": job.get("isbn"),
            "title": job.get("book_title"),
        }

    def _store_search(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        state: dict,
        geography: dict,
        job: dict,
        parsed: dict | None,
    ) -> dict:
        parsed = parsed or {
            "query": job.get("book_title") or job["query"],
            "market": geography["market"],
            "listing_url": "",
            "citations": [],
            "listing_urls": [],
            "html_sha256": "",
            "listing_count": 0,
            "parser": "openai_web_search",
            "small_model": self.small_model,
        }
        listing = parsed.get("listing_url") or ""
        search = {
            "search_id": sha256_bytes(
                f"{job['edition_key']}|{geography['market']}|{job['query']}".encode()
            )[:16],
            "edition_key": job["edition_key"],
            "query_kind": job["kind"],
            "query": parsed.get("query") or job.get("book_title") or job["query"],
            "market": parsed.get("market") or geography["market"],
            "listing_url": listing,
            "citations": parsed.get("citations") or [],
            "html_sha256": parsed.get("html_sha256") or "",
            "listing_urls": parsed.get("listing_urls") or [],
            "listing_count": min(len(parsed.get("listing_urls") or []), 5),
            "parser": parsed.get("parser") or "openai_web_search",
            "small_model": parsed.get("small_model") or self.small_model,
            "from_cache": False,
            "country_code": geography["country_code"],
            "isbn": job.get("isbn"),
            "title": job.get("book_title"),
        }
        if search["search_id"] not in {item["search_id"] for item in state["searches"]}:
            state["searches"].append(search)
        self._remember_found(
            state,
            title=job.get("book_title"),
            query=job["query"],
            search_id=search["search_id"],
            citations=search.get("citations") or [],
            isbn=job.get("isbn"),
        )
        state["ledger"]["lines"].append(
            {
                "kind": "openai_web_search",
                "query": job["query"],
                "market": geography["market"],
                "cached": False,
                "occurred_at": utc_now().isoformat(),
                "edition_key": job["edition_key"],
            }
        )
        job_key = f"{survey_id}:price:{search['search_id']}:{PIPELINE_VERSION}"
        repository.save_job(
            job_key,
            {
                "job_key": job_key,
                "survey_id": str(survey_id),
                "stage": "price",
                "input_hash": search["search_id"],
                "pipeline_version": PIPELINE_VERSION,
                "result": search,
            },
        )
        return search

    def _attach_drafts(self, state: dict, search: dict | None, geography: dict) -> None:
        searches = [search] if search is not None else state["searches"]
        existing = {
            (item.get("search_id"), item.get("source_url"), item.get("parsed_amount"))
            for item in state["observations"]
        }
        for item in searches:
            if item is None:
                continue
            for citation in item.get("citations") or []:
                if not citation.get("parsed_amount"):
                    continue
                key = (item["search_id"], citation.get("url"), citation.get("parsed_amount"))
                if key in existing:
                    continue
                state["observations"].append(
                    self._observation(
                        None,
                        geography,
                        query=item["query"],
                        kind=item["query_kind"],
                        edition_key=item["edition_key"],
                        citation=citation,
                        review_status="draft",
                        search_id=item["search_id"],
                    )
                )
                existing.add(key)

    def _observation(
        self,
        survey_id,
        geography: dict,
        *,
        query: str,
        kind: str,
        edition_key: str,
        citation: dict,
        review_status: str,
        asset_copy_id: str | None = None,
        search_id: str | None = None,
        condition: str | None = None,
        format: str | None = None,
    ) -> dict:
        amount = citation.get("parsed_amount")
        evidence = sha256_bytes(
            f"{query}|{citation.get('url')}|{amount}|{citation.get('snippet')}".encode()
        )
        return {
            "schema_version": "1.0.0",
            "price_observation_id": str(uuid4()),
            "book_edition_id": edition_key,
            "asset_copy_id": asset_copy_id,
            "search_id": search_id,
            "query": query,
            "query_kind": kind,
            "market": geography["market"],
            "currency": citation.get("parsed_currency") or geography["currency"],
            "source_url": citation.get("url") or "",
            "listing_url": citation.get("listing_url") or citation.get("url"),
            "observed_at": utc_now().isoformat(),
            "amount": float(amount) if amount is not None else None,
            "parsed_amount": amount,
            "shipping": None,
            "condition": condition,
            "format": format,
            "offer_type": citation.get("offer_type") or "unknown",
            "review_status": review_status,
            "title": citation.get("title"),
            "snippet": citation.get("snippet"),
            "evidence_hash": evidence,
            "reason": citation.get("snippet"),
        }

    def _copy_rows(
        self, stage3: dict, inventory: dict, state: dict, geography: dict
    ) -> list[dict]:
        assets = list(stage3.get("assets") or inventory.get("asset_copies") or [])
        evidence_by_observation = {
            item.get("observation_id"): item.get("evidence_ref")
            for item in inventory.get("observations") or []
        }
        identities = {item["asset_copy_id"]: item for item in stage3.get("identities") or []}
        notes = list(stage3.get("notes") or [])
        queue = [item for item in stage3.get("queue") or [] if item.get("status") == "open"]
        rows = []
        for asset in assets:
            asset_id = asset["asset_copy_id"]
            category = asset.get("category") or "book"
            policy = (
                {
                    "valuation_required": asset.get("valuation_required", True),
                    "requires_appraisal": asset.get("requires_appraisal", False),
                    "excluded": asset.get("excluded", False),
                }
                if "excluded" in asset or "valuation_required" in asset
                else asset_policy(category, bool(asset.get("requires_appraisal")))
            )
            identity = identities.get(asset_id)
            identity_task = next(
                (
                    item["message"]
                    for item in queue
                    if item.get("asset_copy_id") == asset_id
                    and item["kind"]
                    in {"unread_spine", "rescan_barcode", "manual_identity", "catalog_review"}
                ),
                None,
            )
            query, query_kind, edition_key = self._query_for(asset, identity, geography)
            existing_spoken = next(
                (
                    item
                    for item in state["observations"]
                    if item.get("asset_copy_id") == asset_id and item.get("query_kind") == "spoken"
                ),
                None,
            )
            spoken = None
            if existing_spoken is None:
                spoken = self._spoken_for(asset, notes, geography)
                if spoken is not None:
                    state["observations"].append(spoken)
            elif existing_spoken.get("review_status") == "draft":
                spoken = existing_spoken
            owned = [
                item
                for item in state["observations"]
                if item.get("review_status") == "accepted"
                and item.get("offer_type") == "physical"
                and item.get("asset_copy_id") == asset_id
                and item.get("parsed_amount") is not None
            ]
            comparable = owned
            drafts = [
                item
                for item in state["observations"]
                if item.get("review_status") == "draft"
                and (
                    item.get("book_edition_id") == edition_key
                    or item.get("asset_copy_id") == asset_id
                )
            ]
            eligible = bool(policy["valuation_required"]) and not policy["excluded"]
            status = "excluded"
            reason = "Object is counted and excluded from valuation"
            valuation = None
            if policy["excluded"] or not eligible:
                status = "excluded"
            elif policy["requires_appraisal"] and not owned and not spoken:
                status = "requires_appraisal"
                reason = "Appraisal required; book search is not used"
                if identity_task:
                    status = "price_pending"
                    reason = identity_task
            elif owned:
                amounts = [Decimal(str(item["parsed_amount"])) for item in owned]
                low, high, central = min(amounts), max(amounts), Decimal(str(median(amounts)))
                status = "manual" if owned[-1].get("query_kind") == "manual" else "quoted"
                reason = "Technician-confirmed physical replacement evidence"
                valuation = {
                    "valuation_id": f"val_{asset_id}",
                    "asset_copy_id": asset_id,
                    "basis": "replacement_cost",
                    "amount": {
                        "value": float(central),
                        "unit": owned[-1]["currency"],
                        "status": "ok",
                        "confidence": 0.7 if len(owned) == 1 else 0.85,
                        "interval": {
                            "low": float(low),
                            "high": float(high),
                            "level": 0.8,
                        },
                        "method": "confirmed-web-search-or-manual",
                        "evidence_refs": [item["price_observation_id"] for item in owned],
                        "run_id": PIPELINE_VERSION,
                    },
                    "currency": owned[-1]["currency"],
                    "price_observation_refs": [item["price_observation_id"] for item in owned],
                }
            elif asset_id in state.get("no_comparable", {}):
                status = "no_comparable"
                reason = state["no_comparable"][asset_id]
            elif spoken:
                status = "price_pending"
                reason = (
                    "Spoken or tagged cost is a draft assertion and is not a confirmed price"
                )
                if spoken is not None and spoken not in drafts:
                    drafts = [*drafts, spoken]
            elif identity_task and query is None:
                status = "price_pending"
                reason = identity_task
            elif query is None:
                status = "price_pending"
                reason = "Unresolved identity; leave unpriced"
            elif drafts and not any(
                item.get("offer_type") == "physical" and item.get("parsed_amount")
                for item in drafts
            ):
                status = "no_comparable"
                reason = "Results were eBook, rental, bundle, or had no physical price"
            elif drafts:
                status = "price_pending"
                reason = "Web search drafts need human confirmation before they price this copy"
            else:
                status = "price_pending"
                reason = "Search by validated ISBN, otherwise by recognized name"
            search = next(
                (
                    item
                    for item in state["searches"]
                    if item.get("edition_key") == edition_key
                    or item.get("query") == query
                ),
                None,
            )
            rows.append(
                {
                    "asset_copy_id": asset_id,
                    "category": category,
                    "label": asset.get("label"),
                    "slot": asset.get("slot"),
                    "face_id": asset.get("face_id"),
                    "row_id": asset.get("row_id"),
                    "isbn": (
                        identity.get("normalized")
                        if identity and identity.get("usable_for_isbn_price_query")
                        else None
                    ),
                    "title": (identity or {}).get("title")
                    or ((identity or {}).get("catalog") or {}).get("title"),
                    "eligible": eligible,
                    "requires_appraisal": policy["requires_appraisal"],
                    "excluded": policy["excluded"],
                    "query": query,
                    "query_kind": query_kind,
                    "edition_key": edition_key,
                    "identity_task": identity_task,
                    "identity_status": (
                        "isbn"
                        if query_kind == "isbn"
                        else (
                            "name"
                            if query_kind == "name"
                            else ("unresolved" if category == "book" else category)
                        )
                    ),
                    "condition": (owned or drafts or [None])[0].get("condition")
                    if (owned or drafts)
                    else None,
                    "valuation_status": status,
                    "reason": reason,
                    "valuation": valuation,
                    "draft_count": len(drafts),
                    "confirmed_count": len(comparable),
                    "search_id": None if search is None else search.get("search_id"),
                    "listing_url": None if search is None else search.get("listing_url"),
                    "evidence_refs": asset.get("observation_refs") or [],
                    "evidence_paths": sorted({
                        evidence_by_observation.get(ref) or ref
                        for ref in asset.get("observation_refs") or []
                        if evidence_by_observation.get(ref) or "/" in ref
                    } | ({asset["evidence_ref"]} if asset.get("evidence_ref") else set())),
                    "actions": self._actions(status, eligible, query, identity_task, category),
                }
            )
        rows.extend(self._rows_from_searches(rows, state, geography))
        return rows

    def _rows_from_searches(
        self, existing: list[dict], state: dict, geography: dict
    ) -> list[dict]:
        known = []
        for row in existing:
            title = str(row.get("title") or row.get("label") or "").strip()
            if not title:
                continue
            known.append(
                object_key(
                    kind=row.get("query_kind") or row.get("category") or "object",
                    title=title,
                    category=row.get("category"),
                )
            )
        extra = []
        for item in state.get("live_searches") or []:
            title = str(item.get("title") or item.get("query") or "").strip()
            if len(title) < 3 or _GENERIC_TITLE.match(title):
                continue
            category = _search_category(item)
            kind = item.get("query_kind") or ("name" if category == "book" else "object")
            key = object_key(kind="object", title=title, category=category)
            matched = next(
                (
                    row
                    for row in existing
                    if keys_match(
                        key,
                        object_key(
                            kind="object",
                            title=str(row.get("title") or row.get("label") or ""),
                            category=row.get("category"),
                        ),
                    )
                    or str(row.get("title") or row.get("label") or "").strip().lower()
                    == title.lower()
                ),
                None,
            )
            amount = item.get("amount")
            if matched is not None:
                if item.get("listing_url") and not matched.get("listing_url"):
                    matched["listing_url"] = item.get("listing_url")
                if item.get("query") and (
                    not matched.get("query") or "paperback" in str(matched.get("query") or "")
                ):
                    matched["query"] = item.get("query")
                    matched["query_kind"] = kind
                if amount is not None and not (
                    ((matched.get("valuation") or {}).get("amount") or {}).get("value")
                ):
                    matched["valuation"] = {
                        "valuation_id": f"val_{matched['asset_copy_id']}",
                        "asset_copy_id": matched["asset_copy_id"],
                        "basis": "replacement_cost",
                        "amount": {
                            "value": amount,
                            "unit": item.get("currency") or geography.get("currency"),
                            "status": "draft",
                            "confidence": 0.45,
                            "interval": None,
                            "method": "live-web-search-draft",
                            "evidence_refs": [],
                            "run_id": PIPELINE_VERSION,
                        },
                        "currency": item.get("currency") or geography.get("currency"),
                        "price_observation_refs": [],
                    }
                    matched["draft_count"] = max(int(matched.get("draft_count") or 0), 1)
                    matched["reason"] = item.get("reason") or DRAFT_REASON
                    if matched.get("valuation_status") in {None, "price_pending"}:
                        matched["valuation_status"] = "price_pending"
                continue
            if any(keys_match(key, stored) for stored in known):
                continue
            known.append(key)
            extra.append(
                {
                    "asset_copy_id": _found_copy_id(category, title),
                    "category": category,
                    "label": title,
                    "slot": None,
                    "face_id": None,
                    "row_id": None,
                    "isbn": item.get("isbn"),
                    "title": title,
                    "eligible": True,
                    "requires_appraisal": False,
                    "excluded": False,
                    "query": item.get("query") or title,
                    "query_kind": kind,
                    "edition_key": None,
                    "identity_task": None,
                    "identity_status": "name" if category == "book" else category,
                    "condition": None,
                    "valuation_status": (
                        "price_pending" if item.get("status") == "draft" else (
                            item.get("status") or "price_pending"
                        )
                    ),
                    "reason": item.get("reason") or DRAFT_REASON,
                    "valuation": None
                    if amount is None
                    else {
                        "valuation_id": f"val_{_found_copy_id(category, title)}",
                        "asset_copy_id": _found_copy_id(category, title),
                        "basis": "replacement_cost",
                        "amount": {
                            "value": amount,
                            "unit": item.get("currency") or geography.get("currency"),
                            "status": "draft",
                            "confidence": 0.45,
                            "interval": None,
                            "method": "live-web-search-draft",
                            "evidence_refs": [],
                            "run_id": PIPELINE_VERSION,
                        },
                        "currency": item.get("currency") or geography.get("currency"),
                        "price_observation_refs": [],
                    },
                    "draft_count": 1 if amount is not None else 0,
                    "confirmed_count": 0,
                    "search_id": item.get("search_id"),
                    "listing_url": item.get("listing_url"),
                    "evidence_refs": [],
                    "evidence_paths": [],
                    "actions": ["confirm_or_manual"],
                }
            )
        return extra

    def _promote_titles(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        titles: list[str],
        frame_paths: list[str],
    ) -> None:
        evidence = next((path for path in frame_paths if path), None)
        for title in titles:
            if _GENERIC_TITLE.match(title):
                continue
            self._ensure_found_copy(
                repository,
                survey_id,
                title=title,
                category="book",
                evidence_ref=evidence,
            )

    def _promote_searches(self, repository: SurveyRepository, survey_id: UUID) -> None:
        state = self._state(repository, survey_id)
        evidence = next(iter(_frame_paths(repository, survey_id)), None)
        for item in state.get("live_searches") or []:
            title = str(item.get("title") or item.get("query") or "").strip()
            if len(title) < 3 or _GENERIC_TITLE.match(title):
                continue
            self._ensure_found_copy(
                repository,
                survey_id,
                title=title,
                category=_search_category(item),
                evidence_ref=evidence,
            )

    def _ensure_found_copy(
        self,
        repository: SurveyRepository,
        survey_id: UUID,
        *,
        title: str,
        category: str,
        evidence_ref: str | None,
    ) -> str:
        asset_id = _found_copy_id(category, title)
        policy = asset_policy(category)
        stage3 = repository.get_json(survey_id, "stage3") or {
            "assets": [],
            "identities": [],
            "notes": [],
            "queue": [],
        }
        assets = list(stage3.get("assets") or [])
        if not any(item.get("asset_copy_id") == asset_id for item in assets):
            assets.append(
                {
                    "asset_copy_id": asset_id,
                    "category": category,
                    "label": title,
                    "observation_refs": [],
                    "evidence_ref": evidence_ref,
                    "valuation_required": policy["valuation_required"],
                    "requires_appraisal": policy["requires_appraisal"],
                    "excluded": policy["excluded"],
                }
            )
            stage3["assets"] = assets
        identities = list(stage3.get("identities") or [])
        if title and not any(item.get("asset_copy_id") == asset_id for item in identities):
            identities.append(
                {
                    "asset_copy_id": asset_id,
                    "title": title,
                    "author": "",
                    "status": "vision_title" if category == "book" else "spoken_object",
                    "evidence_ref": evidence_ref or "pricing/live_searches",
                }
            )
            stage3["identities"] = identities
        repository.save_json(survey_id, "stage3", stage3)
        inventory = self._inventory_payload(repository, survey_id)
        observations = list(inventory.get("observations") or [])
        copies = list(inventory.get("asset_copies") or [])
        obs_id = f"obs_{asset_id[6:]}" if asset_id.startswith("found_") else f"obs_{asset_id}"
        if evidence_ref and not any(
            item.get("observation_id") == obs_id for item in observations
        ):
            observations.append(
                Observation(
                    observation_id=obs_id,
                    evidence_ref=evidence_ref,
                    monotonic_seconds=0,
                    category=category,
                    confidence=0.55,
                    asset_copy_id=asset_id,
                    pass_id="B" if category == "book" else "A",
                ).model_dump(mode="json")
            )
        if not any(item.get("asset_copy_id") == asset_id for item in copies):
            copies.append(
                AssetCopy(
                    asset_copy_id=asset_id,
                    category=category,
                    observation_refs=[obs_id] if evidence_ref else [],
                    valuation_required=policy["valuation_required"],
                    requires_appraisal=policy["requires_appraisal"],
                ).model_dump(mode="json")
            )
        inventory["observations"] = observations
        inventory["asset_copies"] = copies
        inventory["status"] = "partial"
        repository.save_json(
            survey_id,
            "inventory",
            InventoryResult.model_validate(inventory).model_dump(mode="json"),
        )
        return asset_id

    def _inventory_payload(self, repository: SurveyRepository, survey_id: UUID) -> dict:
        stored = repository.get_json(survey_id, "inventory")
        if stored:
            return stored
        return InventoryResult(
            survey_id=survey_id,
            status="partial",
            pipeline_version=PIPELINE_VERSION,
            run_id=f"price_{uuid4().hex[:12]}",
        ).model_dump(mode="json")

    def _replay_sample(self, repository: SurveyRepository, survey_id: UUID) -> list[dict]:
        if not (
            os.getenv("ANTHROPIC_API_KEY", "").strip()
            or os.getenv("OPENAI_API_KEY", "").strip()
        ):
            return []
        from backend.app.workflows.models import ModelReplayError, replay_asset

        inventory = repository.get_json(survey_id, "inventory") or {}
        ran: list[dict] = []
        for asset in (inventory.get("asset_copies") or [])[:2]:
            try:
                ran.append(
                    replay_asset(repository, survey_id, asset["asset_copy_id"])
                )
            except (
                ModelReplayError,
                OSError,
                RuntimeError,
                ValueError,
                KeyError,
                TypeError,
            ) as error:
                pricing_log.warning(
                    "replay_sample failed",
                    survey_id=str(survey_id),
                    asset_copy_id=asset.get("asset_copy_id"),
                    error=error.__class__.__name__,
                )
        return ran

    def _actions(
        self,
        status: str,
        eligible: bool,
        query: str | None,
        identity_task: str | None,
        category: str,
    ) -> list[str]:
        actions = []
        if identity_task:
            actions.append("rescan_barcode" if "barcode" in identity_task.lower() else "pass_c")
        if eligible and query and status in {"price_pending", "no_comparable", "unavailable"}:
            actions.append("search_prices")
        if eligible and status == "price_pending":
            actions.append("confirm_or_manual")
        if category != "book":
            actions.append("bind_spoken_cost")
        if not actions:
            actions.append("review")
        return actions

    def _query_for(
        self, asset: dict, identity: dict | None, geography: dict
    ) -> tuple[str | None, str | None, str | None]:
        isbn = None
        title = None
        author = None
        publisher = None
        edition = None
        if identity and identity.get("usable_for_isbn_price_query") and identity.get("valid"):
            isbn = identity.get("normalized")
            catalog = identity.get("catalog") or {}
            title = catalog.get("title")
            authors = catalog.get("authors") or []
            author = authors[0] if authors else None
            publisher = catalog.get("publisher")
            edition = catalog.get("edition")
        elif identity and identity.get("title"):
            title = identity.get("title")
            catalog = identity.get("catalog") or {}
            authors = catalog.get("authors") or identity.get("authors") or []
            if isinstance(authors, list) and authors:
                author = authors[0] if isinstance(authors[0], str) else None
            elif isinstance(catalog.get("candidates"), list) and catalog["candidates"]:
                title = title or catalog["candidates"][0].get("title")
                cand_authors = catalog["candidates"][0].get("authors") or []
                author = cand_authors[0] if cand_authors else author
        elif asset.get("isbn"):
            # Inventory ISBN hints are Stage B OCR and are never a price query key.
            isbn = None
        title = title or asset.get("label")
        category = str(asset.get("category") or "book")
        if category != "book":
            built = template_object_query(
                label=title or category,
                category=category,
                spoken=None,
                country_code=geography["country_code"],
                city=geography.get("city"),
                currency=geography.get("currency"),
            )
            if built is None:
                return None, None, asset.get("book_edition_ref") or asset["asset_copy_id"]
            query, kind = built
            return query, kind, asset.get("book_edition_ref") or asset["asset_copy_id"]
        built = template_query(
            isbn=isbn,
            title=title,
            author=author,
            publisher=publisher,
            edition=edition,
            country_code=geography["country_code"],
            city=geography.get("city"),
            currency=geography.get("currency"),
        )
        if built is None:
            return None, None, asset.get("book_edition_ref") or asset["asset_copy_id"]
        query, kind = built
        edition_key = (
            f"edition_{isbn}" if isbn else "title_" + sha256_bytes(query.encode())[:12]
        )
        return query, kind, edition_key

    def _spoken_for(self, asset: dict, notes: list[dict], geography: dict) -> dict | None:
        texts = []
        if asset.get("stated_cost") is not None:
            texts.append(
                f"This {asset.get('label') or asset['category']} cost us "
                f"{asset['stated_cost']} {asset.get('stated_currency') or geography['currency']}"
            )
        for note in notes:
            if note.get("asset_copy_id") == asset["asset_copy_id"]:
                texts.append(note.get("text") or "")
        for text in texts:
            parsed = parse_spoken_cost(text, default_currency=geography["currency"])
            if parsed is None:
                continue
            amount, currency = parsed
            citation = {
                "title": "Operator-stated replacement cost",
                "url": asset.get("evidence_ref")
                or (asset.get("observation_refs") or ["operator_mark"])[0],
                "snippet": text,
                "offer_type": "physical",
                "parsed_amount": str(amount),
                "parsed_currency": currency,
            }
            return self._observation(
                None,
                geography,
                query="operator stated cost",
                kind="spoken",
                edition_key=asset["asset_copy_id"],
                citation=citation,
                review_status="draft",
                asset_copy_id=asset["asset_copy_id"],
            )
        return None

    def _row_details(self, inventory: dict, copies: list[dict]) -> list[dict]:
        faces = inventory.get("shelf_face_data_sizes") or []
        recapture = set(inventory.get("recapture") or [])
        grouped: dict[tuple[str, str], list[dict]] = {}
        for copy in copies:
            if not copy.get("face_id") or not copy.get("row_id"):
                continue
            grouped.setdefault((copy["face_id"], copy["row_id"]), []).append(copy)
        rows = []
        for face in faces:
            for row in face.get("rows") or []:
                key = (face["shelf_face_id"], row["row_id"])
                detected = grouped.get(key, [])
                detected.sort(key=lambda item: (item.get("slot") is None, item.get("slot") or 0))
                actual = row.get("actual_count")
                rows.append(
                    {
                        "face_id": face["shelf_face_id"],
                        "row_id": row["row_id"],
                        "coverage": row.get("coverage"),
                        "coverage_status": row.get("status"),
                        "detected_count": len(detected) if detected else row.get("copy_count", 0),
                        "actual_count": actual,
                        "detected_actual": {
                            "numerator": len(detected) if detected else row.get("copy_count", 0),
                            "denominator": actual,
                        },
                        "recapture": f"{face['shelf_face_id']}/{row['row_id']}" in recapture
                        or row.get("status") == "partial",
                        "copies": detected,
                    }
                )
        if not rows:
            by_row: dict[tuple[str | None, str | None], list[dict]] = {}
            for copy in copies:
                by_row.setdefault((copy.get("face_id"), copy.get("row_id")), []).append(copy)
            for (face_id, row_id), detected in by_row.items():
                rows.append(
                    {
                        "face_id": face_id,
                        "row_id": row_id,
                        "coverage": None,
                        "coverage_status": "ok",
                        "detected_count": len(detected),
                        "actual_count": None,
                        "detected_actual": {"numerator": len(detected), "denominator": None},
                        "recapture": False,
                        "copies": detected,
                    }
                )
        return rows

    def _contents_range(self, copies: list[dict], currency: str) -> dict:
        lows: list[Decimal] = []
        highs: list[Decimal] = []
        centrals: list[Decimal] = []
        for copy in copies:
            if copy.get("valuation_status") not in {"quoted", "manual"}:
                continue
            valuation = copy.get("valuation")
            if valuation is None or valuation.get("currency") != currency:
                continue
            amount = valuation["amount"]
            interval = amount.get("interval") or {}
            central = Decimal(str(amount["value"]))
            centrals.append(central)
            lows.append(Decimal(str(interval.get("low", central))))
            highs.append(Decimal(str(interval.get("high", central))))
        if not centrals:
            return {
                "currency": currency,
                "low": None,
                "central": None,
                "high": None,
                "status": "unavailable",
                "basis": "replacement_cost",
                "note": (
                    "No confirmed physical-copy prices yet. "
                    "Unresolved books stay visible and unpriced."
                ),
            }
        return {
            "currency": currency,
            "low": float(sum(lows)),
            "central": float(sum(centrals)),
            "high": float(sum(highs)),
            "confirmed_copies": len(centrals),
            "status": "estimated",
            "basis": "replacement_cost",
            "note": (
                "Sum of technician-confirmed physical replacement evidence. "
                "Drafts are excluded."
            ),
        }

    def _building(self, repository: SurveyRepository, survey_id: UUID, geography: dict) -> dict:
        rates = load_rebuild_rates()
        country = geography["country_code"]
        table = rates["rates"].get(country)
        area = None
        method = None
        if repository.exists_bytes(survey_id, "derived/geometry.json"):
            geometry = json.loads(repository.get_bytes(survey_id, "derived/geometry.json"))
            area = geometry.get("floor_area_estimate_square_metres")
            method = geometry.get("floor_area_method")
        if table is None or area is None:
            return {
                "building_valuation_id": f"building_{survey_id}",
                "basis": "replacement_cost",
                "status": "unavailable",
                "rate_table_version": rates["table_id"],
                "disclaimer": rates["disclaimer"],
                "reason": "Missing floor area or rebuild rate for this country",
                "floor_area": None
                if area is None
                else {
                    "value": area,
                    "unit": "m2",
                    "status": "ok",
                    "confidence": 0.7,
                    "interval": None,
                    "method": method or "roomplan",
                    "evidence_refs": ["derived/geometry.json"],
                    "run_id": PIPELINE_VERSION,
                },
                "amount": None,
                "currency": geography["currency"],
            }
        band = table["commercial_library"]
        medium = Decimal(str(band["medium"])) * Decimal(str(area))
        low = Decimal(str(band["low"])) * Decimal(str(area))
        high = Decimal(str(band["premium"])) * Decimal(str(area))
        return {
            "building_valuation_id": f"building_{survey_id}",
            "basis": "replacement_cost",
            "status": "estimated",
            "rate_table_version": rates["table_id"],
            "disclaimer": rates["disclaimer"],
            "not_market_value": True,
            "country_code": country,
            "currency": table["currency"],
            "finish": "medium",
            "occupancy": "commercial_library",
            "floor_area": {
                "value": area,
                "unit": "m2",
                "status": "ok",
                "confidence": 0.7,
                "interval": None,
                "method": method or "roomplan",
                "evidence_refs": ["derived/geometry.json"],
                "run_id": PIPELINE_VERSION,
            },
            "amount": {
                "value": float(medium),
                "unit": table["currency"],
                "status": "ok",
                "confidence": 0.6,
                "interval": {"low": float(low), "high": float(high), "level": 0.8},
                "method": "floor_area_x_demo_rebuild_rates_v1",
                "evidence_refs": ["derived/geometry.json", rates["table_id"]],
                "run_id": PIPELINE_VERSION,
            },
            "rates": band,
        }


def _frame_paths(repository: SurveyRepository, survey_id: UUID) -> list[str]:
    crops: list[str] = []
    frames: list[str] = []
    room: list[str] = []
    for path in sorted(repository.uploads(survey_id)):
        lower = path.lower()
        if not lower.endswith((".jpg", ".jpeg")):
            continue
        if lower.startswith("shelf_scans/crops/"):
            crops.append(path)
        elif lower.startswith("shelf_scans/frames/"):
            frames.append(path)
        elif lower.startswith("roomplan/") and "/frames/" in lower:
            room.append(path)
    return crops + frames + room[-12:]


def _found_copy_id(category: str, title: str) -> str:
    return f"found_{sha256_bytes(f'{category}:{title.lower()}'.encode())[:12]}"


def _search_category(item: dict) -> str:
    raw = str(item.get("category") or "").strip().lower().replace(" ", "_")
    if raw in TAXONOMY:
        return raw
    kind = str(item.get("query_kind") or "")
    if kind in {"isbn", "name", "book"}:
        return "book"
    title = str(item.get("title") or item.get("query") or "").lower()
    if any(token in title for token in ("macbook", "ipad", "laptop", "computer")):
        return "computer"
    if "monitor" in title:
        return "monitor"
    if any(token in title for token in ("air conditioner", "air-conditioner")):
        return "appliance"
    if re.search(r"\bac\b", title):
        return "appliance"
    if any(token in title for token in ("bed", "cupboard", "wardrobe", "almirah", "table")):
        return "furniture"
    return "other"


def _jpeg_from_payload(payload: dict) -> bytes:
    raw = payload.get("image_base64") or payload.get("image")
    if not raw:
        return b""
    try:
        return base64.b64decode(str(raw).encode("ascii"), validate=False)
    except (ValueError, TypeError):
        return b""


def _jpeg_at(repository: SurveyRepository, survey_id: UUID, path: object) -> str | None:
    relative = str(path or "").strip()
    if not relative:
        return None
    try:
        jpeg = repository.get_bytes(survey_id, relative)
    except FileNotFoundError:
        return None
    if not jpeg:
        return None
    return base64.b64encode(jpeg).decode("ascii")


def _category_from_speech(text: str) -> str | None:
    return category_from_speech(text)


def _room_poses(repository: SurveyRepository, survey_id: UUID) -> list[dict]:
    try:
        return json.loads(repository.get_bytes(survey_id, "roomplan/raw/poses.json").decode())
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return []


def _nearest_frame(poses: list[dict], monotonic: object) -> str | None:
    if not poses:
        return None
    try:
        target = float(monotonic)
    except (TypeError, ValueError):
        target = None
    if target is None:
        return str(poses[0].get("image_path") or poses[0].get("imagePath") or "") or None
    best = min(
        poses,
        key=lambda item: abs(
            float(item.get("monotonic_seconds") or item.get("monotonicSeconds") or 0) - target
        ),
    )
    return str(best.get("image_path") or best.get("imagePath") or "") or None
