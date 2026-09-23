"""Shelf, spine, quality, and tracking pipeline package."""

from cv.library_vision.pipeline import (
    PIPELINE_VERSION,
    QualityComponents,
    count_labeled_shelf,
    quality_from_mapping,
)
from cv.library_vision.yolo_spines import (
    SpineCrop,
    crop_path,
    merge_yolo_into_labeled,
    segment_book_spines,
    yolo_enabled,
)

__all__ = [
    "PIPELINE_VERSION",
    "QualityComponents",
    "SpineCrop",
    "count_labeled_shelf",
    "crop_path",
    "merge_yolo_into_labeled",
    "quality_from_mapping",
    "segment_book_spines",
    "yolo_enabled",
]
