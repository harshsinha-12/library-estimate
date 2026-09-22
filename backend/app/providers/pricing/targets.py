"""Align transcript windows with video frames and list unique items to price."""

from __future__ import annotations

import re

from backend.app.providers.pricing.schema import DEFAULT_SMALL_MODEL
from backend.app.providers.pricing.small_model import complete_json

MAX_SEARCHES_PER_OBJECT = 5
FRAME_MATCH_PAD = 1.5
FRAME_MATCH_MAX = 4.0
_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "is",
        "my",
        "our",
        "that",
        "the",
        "there",
        "this",
        "those",
        "with",
    }
)
TARGET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "kind", "category", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["book", "object"]},
                    "category": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                },
            },
        }
    },
}


def speech_span(note: dict) -> tuple[float | None, float | None]:
    start = _as_float(note.get("monotonic_seconds"))
    end = _as_float(note.get("ended_monotonic_seconds"))
    if start is None:
        return None, None
    if end is None or end < start:
        end = start
    return start, end


def timed_visuals(poses: list[dict], marks: list[dict] | None = None) -> list[dict]:
    rows: list[dict] = []
    for pose in poses or []:
        path = str(pose.get("image_path") or pose.get("imagePath") or "").strip()
        moment = _as_float(pose.get("monotonic_seconds") or pose.get("monotonicSeconds"))
        if path and moment is not None:
            rows.append({"t": moment, "path": path, "kind": "video"})
    for mark in marks or []:
        path = str(mark.get("evidence_ref") or "").strip()
        moment = _as_float(mark.get("monotonic_seconds"))
        if path and moment is not None:
            rows.append({"t": moment, "path": path, "kind": "mark"})
    return rows


def frames_in_span(
    visuals: list[dict],
    start: float | None,
    end: float | None,
    *,
    pad: float = FRAME_MATCH_PAD,
    limit: int = 3,
) -> list[dict]:
    if not visuals:
        return []
    if start is None:
        return visuals[:1]
    finish = end if end is not None else start
    lo, hi = start - pad, finish + pad
    inside = [item for item in visuals if lo <= float(item["t"]) <= hi]
    mid = (start + finish) / 2
    pool = inside or visuals
    ranked = sorted(pool, key=lambda item: abs(float(item["t"]) - mid))
    if not inside:
        if abs(float(ranked[0]["t"]) - mid) > FRAME_MATCH_MAX:
            return []
        return ranked[:1]
    return ranked[:limit]


def category_from_speech(text: str | None) -> str | None:
    lower = f" {str(text or '').lower()} "
    if "mug" in lower or "coffee cup" in lower:
        return "cup"
    if "air conditioner" in lower or " a/c" in lower or " ac " in lower or "an ac" in lower:
        return "appliance"
    if "monitor" in lower:
        return "monitor"
    if "painting" in lower:
        return "painting"
    if "portrait" in lower or "photo frame" in lower or "picture frame" in lower:
        return "portrait"
    if any(word in lower for word in ("bed", "wardrobe", "cupboard", "almirah", "table", "desk")):
        return "furniture"
    if any(word in lower for word in ("macbook", "laptop", "computer")):
        return "computer"
    if any(word in lower for word in ("book", "isbn", "paperback", "hardcover", "handbook")):
        return "book"
    return None


def object_key(
    *,
    kind: str | None,
    title: str | None,
    category: str | None = None,
    isbn: str | None = None,
) -> str:
    if isbn:
        return f"isbn:{isbn}"
    bucket = (
        "book"
        if (kind or "") in {"name", "isbn", "book", "serial"}
        else (category or kind or "object")
    )
    return f"{bucket}:{_normalize_name(title)}"


def keys_match(left: str, right: str) -> bool:
    if left == right:
        return True
    kind_a, name_a = _split_key(left)
    kind_b, name_b = _split_key(right)
    if kind_a != kind_b or not name_a or not name_b:
        return False
    if kind_a == "isbn":
        return name_a == name_b
    if name_a == name_b:
        return True
    if kind_a == "book" and min(len(name_a), len(name_b)) < 8:
        return False
    return name_a in name_b or name_b in name_a


def already_priced(state: dict, key: str) -> bool:
    for item in state.get("found_prices") or []:
        if not item.get("amount"):
            continue
        if any(keys_match(candidate, key) for candidate in _item_keys(item)):
            return True
    for item in state.get("live_searches") or []:
        if not item.get("amount"):
            continue
        if any(keys_match(candidate, key) for candidate in _item_keys(item)):
            return True
    return False


def priced_search(state: dict, key: str) -> dict | None:
    for item in reversed(state.get("live_searches") or []):
        if not item.get("amount"):
            continue
        if any(keys_match(candidate, key) for candidate in _item_keys(item)):
            return item
    for item in reversed(state.get("searches") or []):
        citations = item.get("citations") or []
        if not any(row.get("parsed_amount") for row in citations):
            continue
        if any(keys_match(candidate, key) for candidate in _item_keys(item)):
            return item
    return None


def _item_keys(item: dict) -> list[str]:
    keys = []
    isbn = item.get("isbn")
    title = item.get("title") or item.get("query")
    if isbn:
        keys.append(object_key(kind="isbn", title=title, isbn=isbn))
    if title:
        keys.append(
            object_key(
                kind=item.get("query_kind") or "book",
                title=title,
                category=item.get("category"),
            )
        )
        keys.append(object_key(kind="book", title=title))
    return keys


def attempt_count(state: dict, key: str) -> int:
    total = 0
    for stored, count in (state.get("search_attempts") or {}).items():
        if keys_match(str(stored), key):
            total += int(count)
    return total


def record_attempt(state: dict, key: str) -> None:
    attempts = state.setdefault("search_attempts", {})
    for stored in list(attempts):
        if keys_match(str(stored), key):
            attempts[stored] = int(attempts[stored]) + 1
            return
    attempts[key] = 1


def format_timeline(notes: list[dict], visuals: list[dict]) -> str:
    lines: list[str] = []
    for index, note in enumerate(notes, start=1):
        text = str(note.get("text") or "").strip()
        if len(text) < 3:
            continue
        start, end = speech_span(note)
        matched = frames_in_span(visuals, start, end)
        paths = ",".join(item["path"] for item in matched) or "none"
        clock = "unknown"
        if start is not None:
            clock = f"{start:.1f}-{end:.1f}" if end is not None else f"{start:.1f}"
        lines.append(f"{index}. t={clock} speech={text[:180]} frames={paths}")
    return "\n".join(lines)


def fallback_targets(notes: list[dict], visuals: list[dict]) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for note in notes:
        text = str(note.get("text") or "").strip()
        category = category_from_speech(text)
        if not text or category == "cup":
            continue
        if category is None:
            continue
        start, end = speech_span(note)
        matched = frames_in_span(visuals, start, end)
        kind = "book" if category in {"book", "serial"} else "object"
        name = text[:80]
        key = object_key(kind=kind, title=name, category=category)
        if any(keys_match(key, other) for other in seen):
            continue
        seen.add(key)
        items.append(
            {
                "name": name,
                "kind": kind,
                "category": category,
                "description": text,
                "frame_path": matched[0]["path"] if matched else None,
                "speech": text,
            }
        )
    return items


def plan_price_targets(
    timeline: str,
    *,
    already: list[str] | None = None,
    model: str = DEFAULT_SMALL_MODEL,
) -> list[dict]:
    if not (timeline or "").strip():
        return []
    priced = ", ".join(already or []) or "none"
    prompt = (
        "List unique physical items that still need a replacement-cost web search. "
        "Deduplicate the same object even if speech mentions it twice or two nearby "
        "frames show it. Skip mugs, cups, and anything already priced. "
        "Use the capture-clock timeline: speech is aligned to the video frames shown "
        "at the same monotonic time.\n"
        f"already_priced={priced}\n"
        f"timeline:\n{timeline[:4000]}\n"
        "Return JSON {items:[{name, kind, category, description}]}."
    )
    parsed = complete_json(
        prompt,
        schema=TARGET_SCHEMA,
        schema_name="price_targets",
        model=model,
        reason=(
            "List unique physical items from the capture timeline that still need "
            "a replacement-cost web search"
        ),
    )
    rows = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return []
    items: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        kind = str(row.get("kind") or "object").strip() or "object"
        if kind not in {"book", "object"} or len(name) < 3:
            continue
        category = (
            str(row.get("category") or "").strip()
            or ("book" if kind == "book" else "object")
        )
        if category == "cup":
            continue
        key = object_key(kind=kind, title=name, category=category)
        if any(keys_match(key, other) for other in seen):
            continue
        seen.add(key)
        items.append(
            {
                "name": name[:160],
                "kind": kind,
                "category": category,
                "description": str(row.get("description") or name)[:400],
            }
        )
    return items[:12]


def attach_frames(items: list[dict], notes: list[dict], visuals: list[dict]) -> list[dict]:
    for item in items:
        if item.get("frame_path"):
            continue
        needle = _normalize_name(item.get("name"))
        matched_note = next(
            (
                note
                for note in notes
                if needle and needle in _normalize_name(note.get("text"))
            ),
            None,
        )
        start, end = speech_span(matched_note) if matched_note else (None, None)
        frames = frames_in_span(visuals, start, end)
        if frames:
            item["frame_path"] = frames[0]["path"]
            item["speech"] = str((matched_note or {}).get("text") or item.get("description") or "")
    return items


def _normalize_name(value: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return " ".join(word for word in text.split() if word not in _STOP)


def _split_key(key: str) -> tuple[str, str]:
    kind, _, name = str(key).partition(":")
    return kind, name


def _as_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
