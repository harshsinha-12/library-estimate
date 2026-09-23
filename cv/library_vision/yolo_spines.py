"""Initial after-seal spine count and crops from YOLO 11x-seg.

Follows suxrobGM/bookshelf-scanner (YOLO 11x-seg, COCO class 73). Live Pass B
still draws Apple Vision rectangles. When YOLO returns books, those detections
are the initial count and the crops sent to Fable / Astra Extra / Jev.
Moondream2 is not used.
"""

from __future__ import annotations

import io
import os
from dataclasses import asdict, dataclass
from typing import Protocol

PIPELINE_NAME = "yolo11x-seg-book-spines"
COCO_BOOK_CLASS = 73
MAX_EDGE = 2560
LIVE_MAX_EDGE = 640
SPINE_ASPECT_THRESHOLD = 2.0
DEFAULT_CONFIDENCE = 0.25
DEFAULT_MODEL = "yolo11x-seg.pt"


@dataclass(slots=True)
class SpineCrop:
    slot: int
    x: float
    y: float
    width: float
    height: float
    confidence: float
    jpeg: bytes
    rotated: bool
    stacked: bool
    xyxy: tuple[float, float, float, float]
    source_path: str = ""

    def meta(self) -> dict:
        payload = asdict(self)
        payload.pop("jpeg")
        payload["xyxy"] = list(self.xyxy)
        payload["jpeg_bytes"] = len(self.jpeg)
        return payload


class SpineSegmenter(Protocol):
    def __call__(self, jpeg: bytes, *, source_path: str = "") -> list[SpineCrop]: ...


def yolo_enabled() -> bool:
    if os.getenv("YOLO_SPINE_DISABLED", "").strip() in {"1", "true", "yes"}:
        return False
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        return False
    return True


def should_rotate_spine(
    width: float, height: float, threshold: float = SPINE_ASPECT_THRESHOLD
) -> bool:
    if width <= 0:
        return False
    return (height / width) > threshold


def sort_spines_left_to_right(spines: list[SpineCrop]) -> list[SpineCrop]:
    ordered = sorted(spines, key=lambda item: (item.x, item.slot, item.y))
    for index, item in enumerate(ordered):
        item.slot = index
    return ordered


def crop_path(row_id: str, slot: int) -> str:
    safe_row = str(row_id).replace("/", "_")
    return f"derived/yolo-spines/{safe_row}_slot{int(slot)}.jpg"


def detections_path() -> str:
    return "derived/yolo-spines/detections.json"


def overlay_path(source_path: str) -> str:
    name = source_path.rsplit("/", 1)[-1] or "frame.jpg"
    if not name.lower().endswith((".jpg", ".jpeg", ".png")):
        name = f"{name}.jpg"
    return f"derived/yolo-spines/overlays/{name}"


def labeled_has_spines(labeled: dict | None) -> bool:
    if not isinstance(labeled, dict):
        return False
    for scan in labeled.get("passes") or []:
        if not isinstance(scan, dict):
            continue
        for row in scan.get("rows") or []:
            if isinstance(row, dict) and row.get("spines"):
                return True
    return False


def labeled_pass_from_spines(
    spines: list[SpineCrop],
    *,
    frame_path: str,
    pass_id: str,
    room_id: str = "room",
    shelf_id: str = "shelf",
    face_id: str = "shelf.face_A",
    row_id: str = "row_01",
    t: float = 1.0,
    face_normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> dict:
    ordered = sort_spines_left_to_right(list(spines))
    coverage = 0.85 if ordered else 0.0
    return {
        "pass_id": pass_id,
        "room_id": room_id,
        "shelf_id": shelf_id,
        "face_id": face_id,
        "face_normal": list(face_normal),
        "label": face_id,
        "min_x": 0.4,
        "min_z": 0.4,
        "max_x": 1.2,
        "max_z": 0.7,
        "capacity_m": 1.0,
        "evidence_bytes": sum(len(item.jpeg) for item in ordered),
        "t": t,
        "quality": {
            "blur": 0.1,
            "glare": 0.05,
            "speed": 0.1,
            "text_pixel_height": 22,
            "occlusion": 0.0,
        },
        "segmentation": PIPELINE_NAME,
        "source_frame": frame_path,
        "rows": [
            {
                "row_id": row_id,
                "coverage": coverage,
                "capacity_m": 1.0,
                "capture_status": "ok" if ordered else "partial",
                "spines": [
                    {
                        "observation_id": f"yolo_{row_id}_{item.slot}",
                        "slot": item.slot,
                        "x": round(0.4 + item.x * 0.8, 4),
                        "t": t,
                        "appearance": f"yolo-spine-{item.slot}",
                        "evidence_ref": crop_path(row_id, item.slot),
                        "readable": True,
                        "stacked": item.stacked,
                        "leaning": item.rotated,
                        "confidence": item.confidence,
                    }
                    for item in ordered
                ],
            }
        ],
    }


def attach_yolo_crops(labeled: dict, spines: list[SpineCrop], *, row_id: str | None = None) -> dict:
    """Keep existing iOS tracks; stamp YOLO crops onto matching slots."""
    merged = dict(labeled)
    passes = [dict(scan) for scan in merged.get("passes") or []]
    target_row = row_id
    attached = 0
    for scan in passes:
        rows = []
        for row in scan.get("rows") or []:
            row = dict(row)
            if target_row and row.get("row_id") != target_row:
                rows.append(row)
                continue
            existing = list(row.get("spines") or [])
            ordered_existing = sorted(
                existing,
                key=lambda item: (float(item.get("x", 0)), int(item.get("slot", 0))),
            )
            ordered_yolo = sort_spines_left_to_right(list(spines))
            updated: list[dict] = []
            for index, spine in enumerate(ordered_existing):
                item = dict(spine)
                slot = int(item.get("slot", index))
                crop = next(
                    (candidate for candidate in ordered_yolo if candidate.slot == slot),
                    None,
                )
                if crop is None and index < len(ordered_yolo):
                    crop = ordered_yolo[index]
                if crop is not None:
                    item["evidence_ref"] = crop_path(row.get("row_id") or "row_01", crop.slot)
                    item["appearance"] = item.get("appearance") or f"yolo-spine-{crop.slot}"
                    item["readable"] = True if item.get("readable") is None else item["readable"]
                    attached += 1
                updated.append(item)
            row["spines"] = updated
            rows.append(row)
            if target_row is None:
                target_row = row.get("row_id")
        scan["rows"] = rows
        scan["segmentation"] = PIPELINE_NAME
    merged["passes"] = passes
    merged["yolo_attached"] = attached
    return merged


def merge_yolo_into_labeled(labeled: dict | None, yolo_passes: list[dict]) -> dict | None:
    """YOLO is the initial after-seal count when it found spines, not only when N is larger."""
    if not yolo_passes:
        return labeled
    geometry = _geometry_from_labeled(labeled)
    passes = []
    for scan in yolo_passes:
        row = dict(scan)
        for key, value in geometry.items():
            if value not in (None, "", []) and not row.get(key):
                row[key] = value
        if geometry.get("face_id"):
            row["face_id"] = geometry["face_id"]
            row["label"] = geometry.get("label") or geometry["face_id"]
        passes.append(row)
    return {
        "passes": passes,
        "segmentation": PIPELINE_NAME,
        "count_source": "yolo",
    }


def _geometry_from_labeled(labeled: dict | None) -> dict:
    if not isinstance(labeled, dict):
        return {}
    for scan in labeled.get("passes") or []:
        if not isinstance(scan, dict):
            continue
        return {
            "room_id": scan.get("room_id"),
            "shelf_id": scan.get("shelf_id"),
            "face_id": scan.get("face_id"),
            "label": scan.get("label") or scan.get("face_id"),
            "face_normal": scan.get("face_normal"),
            "min_x": scan.get("min_x"),
            "min_z": scan.get("min_z"),
            "max_x": scan.get("max_x"),
            "max_z": scan.get("max_z"),
        }
    return {}


def _spines_from_pass(scan: dict) -> list[SpineCrop]:
    spines: list[SpineCrop] = []
    for row in scan.get("rows") or []:
        for item in row.get("spines") or []:
            spines.append(
                SpineCrop(
                    slot=int(item.get("slot", len(spines))),
                    x=float(item.get("x", 0)),
                    y=0.5,
                    width=0.04,
                    height=0.4,
                    confidence=float(item.get("confidence", 0.5)),
                    jpeg=b"",
                    rotated=bool(item.get("leaning")),
                    stacked=bool(item.get("stacked")),
                    xyxy=(0, 0, 0, 0),
                    source_path=str(scan.get("source_frame") or ""),
                )
            )
    return spines


def segment_book_spines(
    jpeg: bytes,
    *,
    source_path: str = "",
    match_source: bool = False,
    live: bool = False,
) -> list[SpineCrop]:
    """Run YOLO 11x-seg on a shelf JPEG. Returns [] if weights/runtime are unavailable."""
    if not jpeg or not yolo_enabled():
        return []
    predictor = UltralyticsBookSegmenter()
    return predictor(
        jpeg, source_path=source_path, match_source=match_source, live=live
    )


YOLO_INSTALL_HINT = (
    "On the Mac: pip install -e '.[yolo]' then restart uvicorn. "
    "The iPhone does not run ultralytics. Rebuild LibrarySurvey after."
)


def live_overlay(jpeg: bytes) -> dict:
    """Boxes for the phone camera overlay. Assist only — not inventory."""
    if not yolo_enabled():
        return {
            "enabled": False,
            "reason": "ultralytics_not_installed",
            "install": YOLO_INSTALL_HINT,
            "pipeline": PIPELINE_NAME,
            "count": 0,
            "boxes": [],
            "moondream2": False,
        }
    if not jpeg:
        return {
            "enabled": True,
            "reason": "no_frame",
            "install": None,
            "pipeline": PIPELINE_NAME,
            "count": 0,
            "boxes": [],
            "moondream2": False,
        }
    spines = segment_book_spines(jpeg, match_source=True, live=True)
    return {
        "enabled": True,
        "reason": None,
        "install": None,
        "pipeline": PIPELINE_NAME,
        "count": len(spines),
        "boxes": [_vision_box(item) for item in spines],
        "moondream2": False,
    }


def render_overlay_jpeg(
    jpeg: bytes, spines: list[SpineCrop], *, match_source: bool = False
) -> bytes:
    image = _load_rgb(jpeg, match_source=match_source).convert("RGBA")
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width, height = image.size
    for item in spines:
        x1 = int((item.x - item.width / 2) * width)
        y1 = int((item.y - item.height / 2) * height)
        x2 = int((item.x + item.width / 2) * width)
        y2 = int((item.y + item.height / 2) * height)
        draw.rectangle((x1, y1, x2, y2), outline=(0, 220, 0, 255), width=3, fill=(0, 220, 0, 70))
        draw.text((x1 + 2, max(0, y1 - 14)), f"book {item.confidence:.2f}", fill=(0, 255, 0, 255))
    return _to_jpeg(Image.alpha_composite(image, layer))


def _vision_box(crop: SpineCrop) -> dict:
    left = max(0.0, crop.x - crop.width / 2)
    top = max(0.0, crop.y - crop.height / 2)
    return {
        "x": round(left, 4),
        "y": round(max(0.0, 1.0 - (top + crop.height)), 4),
        "width": round(crop.width, 4),
        "height": round(crop.height, 4),
        "confidence": round(crop.confidence, 4),
        "label": "book",
        "title": None,
    }


class UltralyticsBookSegmenter:
    """Adapter around ultralytics YOLO11x-seg. Loads the model once per process."""

    _model = None

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = (
            (model_path or "").strip()
            or os.getenv("YOLO_SPINE_MODEL", "").strip()
            or DEFAULT_MODEL
        )
        self.book_class = int(os.getenv("YOLO_BOOK_CLASS", str(COCO_BOOK_CLASS)))
        self.confidence = float(os.getenv("YOLO_SPINE_CONFIDENCE", str(DEFAULT_CONFIDENCE)))

    def __call__(
        self,
        jpeg: bytes,
        *,
        source_path: str = "",
        match_source: bool = False,
        live: bool = False,
    ) -> list[SpineCrop]:
        edge = LIVE_MAX_EDGE if live else MAX_EDGE
        image = _load_rgb(jpeg, match_source=match_source, max_edge=edge)
        if image is None:
            return []
        enhanced = _preprocess(image, denoise=not live)
        model = self._load_model()
        kwargs = {
            "imgsz": LIVE_MAX_EDGE if live else max(32, int(enhanced.size[0])),
            "classes": [self.book_class],
            "retina_masks": not live,
            "conf": self.confidence,
            "verbose": False,
        }
        if _cuda_available():
            kwargs["half"] = True
        results = model.predict(enhanced, **kwargs)
        if not results:
            return []
        result = results[0]
        boxes = getattr(result, "boxes", None)
        masks = getattr(result, "masks", None)
        if boxes is None or len(boxes) == 0:
            return []
        spines: list[SpineCrop] = []
        mask_data = getattr(masks, "data", None) if masks is not None else None
        for index, box in enumerate(boxes):
            xyxy = _xyxy(box)
            conf = _confidence(box)
            mask = mask_data[index] if mask_data is not None else None
            width = max(1.0, xyxy[2] - xyxy[0])
            height = max(1.0, xyxy[3] - xyxy[1])
            rotated = should_rotate_spine(width, height)
            if live:
                crop_jpeg = b""
            else:
                crop = _mask_and_crop(enhanced, mask, xyxy)
                if rotated:
                    crop = crop.rotate(90, expand=True)
                crop_jpeg = _to_jpeg(crop)
            image_width, image_height = enhanced.size
            spines.append(
                SpineCrop(
                    slot=index,
                    x=_clamp((xyxy[0] + xyxy[2]) / 2 / image_width),
                    y=_clamp((xyxy[1] + xyxy[3]) / 2 / image_height),
                    width=_clamp(width / image_width),
                    height=_clamp(height / image_height),
                    confidence=conf,
                    jpeg=crop_jpeg,
                    rotated=rotated,
                    stacked=width > height * 1.4,
                    xyxy=xyxy,
                    source_path=source_path,
                )
            )
        return sort_spines_left_to_right(spines)

    def _load_model(self):
        if UltralyticsBookSegmenter._model is None:
            from ultralytics import YOLO

            UltralyticsBookSegmenter._model = YOLO(self.model_path, task="segment")
        return UltralyticsBookSegmenter._model


def _load_rgb(jpeg: bytes, *, match_source: bool = False, max_edge: int = MAX_EDGE):
    from PIL import Image

    image = Image.open(io.BytesIO(jpeg))
    image = image.convert("RGB")
    from PIL import ImageOps

    ImageOps.exif_transpose(image, in_place=True)
    if image.size[0] > max_edge or image.size[1] > max_edge:
        image.thumbnail((max_edge, max_edge))
    if not match_source and image.width > image.height:
        image = image.rotate(-90, expand=True)
    return image


def _preprocess(image, *, denoise: bool = True):
    from PIL import ImageEnhance

    enhanced = ImageEnhance.Contrast(image).enhance(1.5)
    enhanced = ImageEnhance.Brightness(enhanced).enhance(1.1)
    if not denoise:
        return enhanced
    try:
        import cv2
        import numpy as np
    except ImportError:
        return enhanced
    array = cv2.cvtColor(np.array(enhanced), cv2.COLOR_RGB2BGR)
    array = cv2.fastNlMeansDenoisingColored(array, None, 10, 10, 7, 21)
    from PIL import Image

    return Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB))


def _mask_and_crop(image, mask_tensor, xyxy: tuple[float, float, float, float]):
    from PIL import Image

    x1, y1, x2, y2 = (int(value) for value in xyxy)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = max(x1 + 1, x2), max(y1 + 1, y2)
    if mask_tensor is None:
        return image.crop((x1, y1, x2, y2))
    array = mask_tensor.detach().cpu().numpy() if hasattr(mask_tensor, "detach") else mask_tensor
    mask = Image.fromarray(array.astype("uint8") * 255)
    if mask.size != image.size:
        mask = mask.resize(image.size)
    masked = Image.new("RGB", image.size)
    masked.paste(image, mask=mask)
    return masked.crop((x1, y1, x2, y2))


def _to_jpeg(image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _xyxy(box) -> tuple[float, float, float, float]:
    raw = box.xyxy[0]
    values = raw.tolist() if hasattr(raw, "tolist") else list(raw)
    return float(values[0]), float(values[1]), float(values[2]), float(values[3])


def _confidence(box) -> float:
    conf = getattr(box, "conf", None)
    if conf is None:
        return 0.5
    value = conf[0] if hasattr(conf, "__getitem__") else conf
    if hasattr(value, "item"):
        value = value.item()
    return float(value)


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
