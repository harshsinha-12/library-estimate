"""Deterministic shelf-face counting, coverage, and physical-copy association."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import hypot
from uuid import uuid4

PIPELINE_VERSION = "shelf-count-v1"
COVERAGE_COMPLETE = 0.8
SLOT_MERGE = 0.035
FACE_NORMAL_DOT = 0.7
MOVED_GAP_SECONDS = 2.0


@dataclass(slots=True)
class QualityComponents:
    blur: float
    glare: float
    speed: float
    text_pixel_height: float
    occlusion: float

    def messages(self) -> list[str]:
        notes: list[str] = []
        if self.blur > 0.55:
            notes.append("Hold still — the frame is blurry")
        if self.glare > 0.35:
            notes.append("Tilt to reduce glare")
        if self.speed > 0.45:
            notes.append("Slow down the sweep")
        if 0 < self.text_pixel_height < 14:
            notes.append("Move closer — spine text is too small")
        if self.occlusion > 0.4:
            notes.append("Shelf face is occluded")
        return notes


@dataclass(slots=True)
class SpineDetection:
    observation_id: str
    pass_id: str
    room_id: str
    shelf_id: str
    face_id: str
    face_normal: tuple[float, float, float]
    row_id: str
    slot: int
    x: float
    t: float
    appearance: str
    isbn: str | None
    evidence_ref: str
    quality: QualityComponents
    coverage: float = 1.0
    occupied_m: float = 0.04
    evidence_bytes: int = 0


@dataclass(slots=True)
class RowCoverage:
    row_id: str
    coverage: float
    status: str
    recapture: bool
    spine_count: int


@dataclass(slots=True)
class CountResult:
    observations: list[dict]
    tracks: list[dict]
    asset_copies: list[dict]
    faces: list[dict]
    overlays: list[dict]
    recapture: list[str]
    status: str
    run_id: str
    pipeline_version: str = PIPELINE_VERSION


def quality_from_mapping(payload: dict) -> QualityComponents:
    return QualityComponents(
        blur=float(payload.get("blur", 0)),
        glare=float(payload.get("glare", 0)),
        speed=float(payload.get("speed", 0)),
        text_pixel_height=float(payload.get("text_pixel_height", 24)),
        occlusion=float(payload.get("occlusion", 0)),
    )


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _same_face(left: SpineDetection, right: SpineDetection) -> bool:
    if (left.room_id, left.shelf_id, left.face_id, left.row_id) != (
        right.room_id,
        right.shelf_id,
        right.face_id,
        right.row_id,
    ):
        return False
    return _dot(left.face_normal, right.face_normal) >= FACE_NORMAL_DOT


def _detections_from_labeled(payload: dict) -> list[SpineDetection]:
    detections: list[SpineDetection] = []
    for scan in payload.get("passes", []):
        normal = tuple(scan.get("face_normal") or (0.0, 0.0, 1.0))
        if len(normal) != 3:
            normal = (0.0, 0.0, 1.0)
        quality = quality_from_mapping(scan.get("quality") or {})
        for row in scan.get("rows", []):
            coverage = float(row.get("coverage", 0))
            for spine in row.get("spines", []):
                detections.append(
                    SpineDetection(
                        observation_id=spine.get("observation_id") or f"obs_{uuid4().hex[:10]}",
                        pass_id=scan["pass_id"],
                        room_id=scan.get("room_id", "room"),
                        shelf_id=scan.get("shelf_id", "shelf"),
                        face_id=scan["face_id"],
                        face_normal=(float(normal[0]), float(normal[1]), float(normal[2])),
                        row_id=row["row_id"],
                        slot=int(spine["slot"]),
                        x=float(spine["x"]),
                        t=float(spine.get("t", scan.get("t", 0))),
                        appearance=str(spine.get("appearance") or spine.get("slot")),
                        isbn=spine.get("isbn"),
                        evidence_ref=(
                            spine.get("evidence_ref") or scan.get("evidence_ref") or "shelf"
                        ),
                        quality=quality,
                        coverage=coverage,
                        occupied_m=float(spine.get("occupied_m", 0.04)),
                        evidence_bytes=int(
                            spine.get("evidence_bytes", scan.get("evidence_bytes", 0))
                        ),
                    )
                )
    return detections


def _track_within_pass(detections: list[SpineDetection]) -> dict[str, str]:
    observation_to_track: dict[str, str] = {}
    grouped: dict[tuple[str, str, str, str, str], list[SpineDetection]] = defaultdict(list)
    for item in detections:
        grouped[(item.pass_id, item.room_id, item.shelf_id, item.face_id, item.row_id)].append(item)
    for members in grouped.values():
        ordered = sorted(members, key=lambda item: (item.t, item.x))
        current: list[SpineDetection] = []
        tracks: list[list[SpineDetection]] = []
        for item in ordered:
            if not current:
                current = [item]
                continue
            previous = current[-1]
            if (
                item.slot == previous.slot
                and abs(item.x - previous.x) <= SLOT_MERGE
                and abs(item.t - previous.t) <= 1.5
            ):
                current.append(item)
            else:
                tracks.append(current)
                current = [item]
        if current:
            tracks.append(current)
        for track in tracks:
            track_id = f"track_{uuid4().hex[:10]}"
            for item in track:
                observation_to_track[item.observation_id] = track_id
    return observation_to_track


def _associate(detections: list[SpineDetection], tracks: dict[str, str]) -> list[dict]:
    copies: list[dict] = []
    used: set[str] = set()
    ordered = sorted(detections, key=lambda item: (item.face_id, item.row_id, item.x, item.t))
    for index, item in enumerate(ordered):
        if item.observation_id in used:
            continue
        members = [item]
        used.add(item.observation_id)
        possibly_moved = False
        for other in ordered[index + 1 :]:
            if other.observation_id in used:
                continue
            isbn_match = bool(item.isbn and other.isbn and item.isbn == other.isbn)
            spatial = (
                _same_face(item, other)
                and abs(item.x - other.x) <= SLOT_MERGE
                and item.slot == other.slot
            )
            if spatial:
                # ISBN is never the merge key; spatial + face membership is.
                members.append(other)
                used.add(other.observation_id)
                continue
            if isbn_match and not spatial:
                jump = hypot(item.x - other.x, 0)
                if jump > SLOT_MERGE and abs(other.t - item.t) >= MOVED_GAP_SECONDS:
                    possibly_moved = True
        copies.append(
            {
                "asset_copy_id": f"copy_{uuid4().hex[:10]}",
                "category": "book",
                "observation_refs": [member.observation_id for member in members],
                "valuation_required": True,
                "requires_appraisal": False,
                "possibly_moved": possibly_moved,
                "book_edition_ref": None,
                "room_id": item.room_id,
                "shelf_id": item.shelf_id,
                "face_id": item.face_id,
                "row_id": item.row_id,
                "slot": item.slot,
                "isbn": item.isbn,
                "track_ids": list({tracks[member.observation_id] for member in members}),
            }
        )
    return copies


def _row_status(coverage: float, spine_count: int) -> RowCoverage:
    if coverage < COVERAGE_COMPLETE:
        return RowCoverage(
            row_id="",
            coverage=coverage,
            status="partial",
            recapture=True,
            spine_count=spine_count,
        )
    return RowCoverage(
        row_id="",
        coverage=coverage,
        status="ok",
        recapture=False,
        spine_count=spine_count,
    )


def count_labeled_shelf(payload: dict, *, run_id: str | None = None) -> CountResult:
    detections = _detections_from_labeled(payload)
    tracks = _track_within_pass(detections)
    copies = _associate(detections, tracks)
    run = run_id or f"run_{PIPELINE_VERSION}_{uuid4().hex[:8]}"

    observations = [
        {
            "observation_id": item.observation_id,
            "evidence_ref": item.evidence_ref,
            "monotonic_seconds": item.t,
            "category": "book",
            "confidence": max(0.0, min(1.0, 1.0 - item.quality.blur)),
            "track_id": tracks.get(item.observation_id),
            "room_id": item.room_id,
            "shelf_id": item.shelf_id,
            "face_id": item.face_id,
            "row_id": item.row_id,
            "slot": item.slot,
            "x": item.x,
            "face_normal": list(item.face_normal),
            "isbn": item.isbn,
            "pass_id": item.pass_id,
        }
        for item in detections
    ]
    track_rows: dict[str, list[str]] = defaultdict(list)
    track_meta: dict[str, SpineDetection] = {}
    for item in detections:
        track_id = tracks[item.observation_id]
        track_rows[track_id].append(item.observation_id)
        track_meta[track_id] = item
    track_payload = [
        {
            "track_id": track_id,
            "observation_refs": refs,
            "status": "ok",
            "face_id": track_meta[track_id].face_id,
            "row_id": track_meta[track_id].row_id,
        }
        for track_id, refs in track_rows.items()
    ]

    declared_rows: dict[tuple[str, str], dict] = {}
    for scan in payload.get("passes", []):
        for row in scan.get("rows", []):
            key = (scan["face_id"], row["row_id"])
            declared_rows[key] = {
                "face_id": scan["face_id"],
                "row_id": row["row_id"],
                "coverage": float(row.get("coverage", 0)),
                "capacity_m": float(row.get("capacity_m", scan.get("capacity_m", 1.0))),
                "actual_count": row.get("actual_count"),
                "capture_status": row.get("capture_status"),
                "label": scan.get("label") or scan["face_id"],
                "min_x": float(scan.get("min_x", 0.4)),
                "min_z": float(scan.get("min_z", 0.4)),
                "max_x": float(scan.get("max_x", 1.2)),
                "max_z": float(scan.get("max_z", 0.7)),
                "evidence_bytes": int(scan.get("evidence_bytes", 0)),
                "placement": "operator" if scan.get("placement") == "operator" else "unregistered",
            }

    copies_by_face_row: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for copy in copies:
        copies_by_face_row[(copy["face_id"], copy["row_id"])].append(copy)

    faces: dict[str, dict] = {}
    recapture: list[str] = []
    overall = "ok"
    for (face_id, row_id), meta in declared_rows.items():
        row_copies = copies_by_face_row.get((face_id, row_id), [])
        coverage = meta["coverage"]
        status_row = _row_status(coverage, len(row_copies))
        if (
            meta.get("capture_status") == "partial"
            or meta.get("actual_count") is not None
            and int(meta["actual_count"]) != len(row_copies)
        ):
            status_row.status = "partial"
            status_row.recapture = True
        status_row.row_id = row_id
        if status_row.recapture:
            recapture.append(f"{face_id}/{row_id}")
            overall = "partial"
        occupied = sum(
            0.04
            for copy in row_copies
        )
        if status_row.recapture:
            # Never report a silent zero: uncovered rows stay partial with an interval.
            count_value = len(row_copies)
            count_status = "partial"
            unresolved = max(
                1 if count_value == 0 else 0,
                max(0, int(meta.get("actual_count") or 0) - count_value),
            )
        else:
            count_value = len(row_copies)
            count_status = "ok"
            unresolved = 0
        face = faces.setdefault(
            face_id,
            {
                "shelf_face_id": face_id,
                "rows": [],
                "occupied": 0.0,
                "capacity": 0.0,
                "copies": 0,
                "unresolved": 0,
                "evidence_bytes": 0,
                "status": "ok",
                "label": meta["label"],
                "min_x": meta["min_x"],
                "min_z": meta["min_z"],
                "max_x": meta["max_x"],
                "max_z": meta["max_z"],
                "placement": meta.get("placement") or "unregistered",
            },
        )
        face["rows"].append(
            {
                "row_id": row_id,
                "coverage": coverage,
                "status": count_status,
                "copy_count": count_value,
                "actual_count": meta.get("actual_count"),
                "count_interval": {
                    "low": (
                        count_value if count_status == "ok"
                        else max(
                            0, min(count_value, int(meta.get("actual_count") or count_value)) - 1
                        )
                    ),
                    "high": (
                        count_value if count_status == "ok"
                        else max(count_value, int(meta.get("actual_count") or 0)) + 1
                    ),
                },
            }
        )
        face["occupied"] += occupied
        face["capacity"] += float(meta["capacity_m"])
        face["copies"] += count_value
        face["unresolved"] += unresolved
        face["evidence_bytes"] += int(meta["evidence_bytes"])
        if count_status != "ok":
            face["status"] = "partial"

    def measurement(value, unit, status, confidence, method, interval=None, extra_refs=None):
        return {
            "value": value,
            "unit": unit,
            "status": status,
            "confidence": confidence,
            "interval": interval,
            "method": method,
            "evidence_refs": extra_refs or ["shelf_scan"],
            "run_id": run,
        }

    face_payload = []
    overlays = []
    for face_id, face in faces.items():
        capacity = face["capacity"] or 1.0
        fill = min(1.0, face["occupied"] / capacity)
        count_interval = {
            "low": max(0, face["copies"] - 1) if face["copies"] else 0,
            "high": face["copies"] + max(1, face["unresolved"]),
            "level": 0.95,
        }
        face_payload.append(
            {
                "shelf_face_id": face_id,
                "occupied_length": measurement(
                    round(face["occupied"], 3), "m", face["status"], 0.8, PIPELINE_VERSION
                ),
                "capacity_length": measurement(
                    round(capacity, 3), "m", "ok", 0.9, PIPELINE_VERSION
                ),
                "fill_ratio": measurement(
                    round(fill, 3), "ratio", face["status"], 0.8, PIPELINE_VERSION
                ),
                "copy_count": measurement(
                    face["copies"],
                    "count",
                    face["status"],
                    0.86,
                    PIPELINE_VERSION,
                    interval=count_interval,
                ),
                "unresolved_count": measurement(
                    face["unresolved"], "count", face["status"], 0.9, PIPELINE_VERSION
                ),
                "evidence_bytes": measurement(
                    face["evidence_bytes"], "byte", "ok", 1.0, "package-bytes"
                ),
                "rows": face["rows"],
            }
        )
        overlays.append(
            {
                "face_id": face_id,
                "label": face["label"],
                "min_x": face["min_x"],
                "min_z": face["min_z"],
                "max_x": face["max_x"],
                "max_z": face["max_z"],
                "copy_count_label": (
                    f"{face['copies']} copies"
                    if face["status"] == "ok"
                    else f"{face['copies']} copies · partial"
                ),
                "fill_label": f"{int(fill * 100)}% fill",
                "status": face["status"],
                "placement": "operator" if face.get("placement") == "operator" else "unregistered",
            }
        )

    if not faces:
        overall = "partial"
    return CountResult(
        observations=observations,
        tracks=track_payload,
        asset_copies=copies,
        faces=face_payload,
        overlays=overlays,
        recapture=sorted(set(recapture)),
        status=overall,
        run_id=run,
    )
