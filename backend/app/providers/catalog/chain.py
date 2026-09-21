"""Identity-only catalog chain. Sale information is deliberately discarded."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

from backend.app.domain.repository import SurveyRepository
from backend.app.utils.hashing import sha256_bytes


def _json_url(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "LibrarySurvey/0.1 (catalog identity)"})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read(2_000_000))


class CatalogChain:
    def __init__(self, fetch_json=_json_url) -> None:
        self.fetch_json = fetch_json

    def resolve(
        self, repository: SurveyRepository, survey_id, isbn: str, *, title: str | None = None
    ) -> dict:
        key = f"catalog:{isbn}"
        cached = repository.get_json(survey_id, key)
        if cached is not None:
            return {**cached, "from_cache": True}
        conflicts = []
        for source, url in (
            ("open_library", f"https://openlibrary.org/isbn/{quote(isbn)}.json"),
            ("google_books", f"https://www.googleapis.com/books/v1/volumes?q=isbn:{quote(isbn)}"),
        ):
            try:
                raw = self.fetch_json(url)
                candidate = self._normalize(source, raw, isbn)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if candidate is None:
                continue
            if title and not self._compatible(title, candidate["title"]):
                candidate["status"] = "metadata_conflict"
                candidate["reason"] = "catalog title conflicts with captured text"
                conflicts.append(candidate)
                continue
            else:
                candidate["status"] = "candidate"
                candidate["reason"] = "valid identifier; catalog metadata requires review"
            candidate["retrieved_at"] = datetime.now(UTC).isoformat()
            candidate["raw_response_sha256"] = sha256_bytes(
                json.dumps(raw, sort_keys=True).encode()
            )
            repository.save_json(survey_id, key, candidate)
            return candidate
        if conflicts:
            return {**conflicts[0], "alternate_conflicts": conflicts[1:]}
        return {
            "status": "manual",
            "isbn": isbn,
            "reason": "catalog sources unavailable or no match",
        }

    def resolve_title(
        self, repository: SurveyRepository, survey_id, title: str, author: str = ""
    ) -> dict:
        cache_key = "catalog:title:" + sha256_bytes(f"{title}|{author}".lower().encode())
        cached = repository.get_json(survey_id, cache_key)
        if cached is not None:
            return {**cached, "from_cache": True}
        for source, url in (
            (
                "open_library",
                f"https://openlibrary.org/search.json?title={quote(title)}&author={quote(author)}&limit=3",
            ),
            (
                "google_books",
                f"https://www.googleapis.com/books/v1/volumes?q=intitle:{quote(title)}+inauthor:{quote(author)}&maxResults=3",
            ),
        ):
            try:
                raw = self.fetch_json(url)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if source == "open_library":
                rows = raw.get("docs") or []
                candidates = [
                    {
                        "source": source,
                        "source_record_id": row.get("key"),
                        "title": row.get("title"),
                        "authors": row.get("author_name", []),
                        "publisher": (row.get("publisher") or [""])[0],
                        "edition": None,
                    }
                    for row in rows
                    if row.get("title")
                ]
            else:
                candidates = [
                    {
                        "source": source,
                        "source_record_id": row.get("id"),
                        "title": row.get("volumeInfo", {}).get("title"),
                        "authors": row.get("volumeInfo", {}).get("authors", []),
                        "publisher": row.get("volumeInfo", {}).get("publisher", ""),
                        "edition": row.get("volumeInfo", {}).get("subtitle"),
                    }
                    for row in raw.get("items", [])
                    if row.get("volumeInfo", {}).get("title")
                ]
            compatible = [item for item in candidates if self._compatible(title, item["title"])]
            if compatible:
                result = {
                    "status": "candidate_needs_edition_review",
                    "query_title": title,
                    "candidates": compatible,
                    "raw_response_sha256": sha256_bytes(json.dumps(raw, sort_keys=True).encode()),
                    "retrieved_at": datetime.now(UTC).isoformat(),
                }
                repository.save_json(survey_id, cache_key, result)
                return result
        return {"status": "manual", "query_title": title, "candidates": []}

    @staticmethod
    def _compatible(observed: str, catalog: str) -> bool:
        words = {part.lower() for part in observed.split() if len(part) > 2}
        return not words or len(words.intersection(catalog.lower().split())) >= max(
            1, (2 * len(words) + 2) // 3
        )

    @staticmethod
    def _normalize(source: str, raw: dict, isbn: str) -> dict | None:
        if source == "open_library":
            if not raw.get("title"):
                return None
            return {
                "source": source,
                "source_record_id": raw.get("key", ""),
                "isbn": isbn,
                "title": raw["title"],
                "authors": [entry.get("key", "") for entry in raw.get("authors", [])],
                "publisher": ", ".join(raw.get("publishers", [])),
                "edition": raw.get("edition_name"),
                "work_refs": [entry.get("key", "") for entry in raw.get("works", [])],
            }
        items = raw.get("items") or []
        for item in items:
            info = item.get("volumeInfo") or {}
            identifiers = info.get("industryIdentifiers") or []
            if isbn not in {entry.get("identifier", "").replace("-", "") for entry in identifiers}:
                continue
            if not info.get("title"):
                continue
            return {
                "source": source,
                "source_record_id": item.get("id", ""),
                "isbn": isbn,
                "title": info["title"],
                "authors": info.get("authors", []),
                "publisher": info.get("publisher", ""),
                "edition": info.get("subtitle"),
                "work_refs": [],
            }
        return None
