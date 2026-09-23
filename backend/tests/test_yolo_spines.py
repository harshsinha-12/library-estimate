from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import fakeredis

from backend.app.domain.models import SurveyGeography, SurveyRecord
from backend.app.domain.repository import SurveyRepository
from backend.app.storage.objects import MemoryObjectStore
from backend.app.utils.clocks import utc_now
from backend.app.workflows.models import replay_asset
from backend.app.workflows.vision import VisionWorker
from cv.library_vision.yolo_spines import (
    PIPELINE_NAME,
    SpineCrop,
    attach_yolo_crops,
    crop_path,
    labeled_has_spines,
    labeled_pass_from_spines,
    merge_yolo_into_labeled,
    should_rotate_spine,
    sort_spines_left_to_right,
    yolo_enabled,
)

TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb0043000806060706050807070709"
    "09080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c"
    "30313434341f27393d38323c2e333432ffc00011080001000103011100021101031101ffc4"
    "001f0000010501010101010100000000000000000102030405060708090a0bffc400b51000"
    "020103030204030505040000017d01020300041105122131410613516107227114328191a1"
    "082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445"
    "464748494a535455565758595a636465666768696a737475767778797a838485868788898a"
    "92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2"
    "d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffc4001f0100030101"
    "01010101010101000000000000000102030405060708090a0bffc400b51100020102040403"
    "040705040400010277000102031104052131061241510761711322328108144291a1b1c109"
    "233352f0156272d10a162434e125f11718191a262728292a35363738393a43444546474849"
    "4a535455565758595a636465666768696a737475767778797a82838485868788898a929394"
    "95969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5"
    "d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c03010002110311003f00"
    "f7fa28a2803fffd9"
)


def _spine(slot: int, x: float, jpeg: bytes = TINY_JPEG, **kwargs) -> SpineCrop:
    return SpineCrop(
        slot=slot,
        x=x,
        y=0.5,
        width=kwargs.get("width", 0.08),
        height=kwargs.get("height", 0.4),
        confidence=kwargs.get("confidence", 0.9),
        jpeg=jpeg,
        rotated=kwargs.get("rotated", False),
        stacked=kwargs.get("stacked", False),
        xyxy=kwargs.get("xyxy", (10.0, 10.0, 40.0, 200.0)),
        source_path=kwargs.get("source_path", "shelf_scans/frames/0001.jpg"),
    )


def test_tall_boxes_rotate_like_bookshelf_scanner() -> None:
    assert should_rotate_spine(20, 50) is True
    assert should_rotate_spine(80, 40) is False
    assert should_rotate_spine(0, 50) is False


def test_spines_are_ordered_left_to_right() -> None:
    ordered = sort_spines_left_to_right([_spine(9, 0.8), _spine(3, 0.2), _spine(1, 0.5)])
    assert [item.x for item in ordered] == [0.2, 0.5, 0.8]
    assert [item.slot for item in ordered] == [0, 1, 2]


def test_yolo_pass_becomes_labeled_when_device_has_no_spines() -> None:
    scan = labeled_pass_from_spines(
        [_spine(0, 0.2), _spine(1, 0.7)],
        frame_path="shelf_scans/frames/0001.jpg",
        pass_id="yolo_0",
    )
    merged = merge_yolo_into_labeled(None, [scan])
    assert merged is not None
    assert merged["segmentation"] == PIPELINE_NAME
    spines = merged["passes"][0]["rows"][0]["spines"]
    assert [item["evidence_ref"] for item in spines] == [
        "derived/yolo-spines/row_01_slot0.jpg",
        "derived/yolo-spines/row_01_slot1.jpg",
    ]
    assert labeled_has_spines(merged)


def test_yolo_attaches_crops_without_inventing_extra_copies() -> None:
    labeled = {
        "passes": [
            {
                "pass_id": "pass_a",
                "face_id": "shelf.face_A",
                "rows": [
                    {
                        "row_id": "row_01",
                        "spines": [
                            {"slot": 0, "x": 0.12, "evidence_ref": "frame_a"},
                            {"slot": 1, "x": 0.28, "evidence_ref": "frame_b"},
                        ],
                    }
                ],
            }
        ]
    }
    yolo = labeled_pass_from_spines(
        [_spine(0, 0.2), _spine(1, 0.55), _spine(2, 0.9)],
        frame_path="shelf_scans/frames/0001.jpg",
        pass_id="yolo_0",
    )
    merged = merge_yolo_into_labeled(labeled, [yolo])
    spines = merged["passes"][0]["rows"][0]["spines"]
    assert len(spines) == 2
    assert spines[0]["evidence_ref"] == crop_path("row_01", 0)
    assert spines[1]["evidence_ref"] == crop_path("row_01", 1)


def test_attach_keeps_unmatched_device_spines() -> None:
    labeled = {
        "passes": [
            {
                "rows": [
                    {
                        "row_id": "row_01",
                        "spines": [{"slot": 0, "x": 0.1, "evidence_ref": "keep-me"}],
                    }
                ]
            }
        ]
    }
    merged = attach_yolo_crops(labeled, [])
    assert merged["passes"][0]["rows"][0]["spines"][0]["evidence_ref"] == "keep-me"


def _repository():
    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True),
        MemoryObjectStore(),
        key_prefix=f"test:yolo:{uuid4().hex[:8]}",
    )
    survey_id = uuid4()
    repository.create(
        SurveyRecord(
            survey_id=survey_id,
            display_name="YOLO spines",
            geography=SurveyGeography(
                country_code="IN",
                region="Karnataka",
                city="Bengaluru",
                currency="INR",
                market="en-IN",
                source="manual",
            ),
            status="geometry",
            created_at=utc_now(),
            sealed_at=utc_now(),
            package_hash="b" * 64,
        )
    )
    return repository, survey_id


def _fake_segmenter(crops: list[SpineCrop]):
    def segment(jpeg: bytes, source_path: str = "") -> list[SpineCrop]:
        _ = jpeg
        return [
            SpineCrop(
                slot=item.slot,
                x=item.x,
                y=item.y,
                width=item.width,
                height=item.height,
                confidence=item.confidence,
                jpeg=item.jpeg,
                rotated=item.rotated,
                stacked=item.stacked,
                xyxy=item.xyxy,
                source_path=source_path,
            )
            for item in crops
        ]

    return segment


def test_vision_worker_segments_frames_and_writes_yolo_crops() -> None:
    repository, survey_id = _repository()
    from backend.app.domain.models import UploadedFile

    frame = TINY_JPEG + b"frame"
    repository.store_upload(
        survey_id,
        UploadedFile(
            path="shelf_scans/frames/0001.jpg",
            mime_type="image/jpeg",
            bytes=len(frame),
            sha256="a" * 64,
        ),
        frame,
    )
    worker = VisionWorker(segment_spines=_fake_segmenter([_spine(1, 0.7), _spine(0, 0.2)]))
    result = worker.process(repository, survey_id)
    assert result is not None
    assert len(result.asset_copies) == 2
    assert {copy.slot for copy in result.asset_copies} == {0, 1}
    left = crop_path("row_01", 0)
    right = crop_path("row_01", 1)
    assert repository.get_bytes(survey_id, left) == TINY_JPEG
    assert repository.get_bytes(survey_id, right) == TINY_JPEG
    detections = json.loads(repository.get_bytes(survey_id, "derived/yolo-spines/detections.json"))
    assert detections["moondream2"] is False
    assert detections["recognizer"] == "fable_astra_jev"
    assert detections["pipeline"] == PIPELINE_NAME
    evidence = {item.evidence_ref for item in result.observations}
    assert evidence == {left, right}
    again = worker.process(repository, survey_id)
    assert again is not None
    assert [copy.asset_copy_id for copy in again.asset_copies] == [
        copy.asset_copy_id for copy in result.asset_copies
    ]


def test_yolo_crops_go_to_fable_astra_then_jev() -> None:
    repository, survey_id = _repository()
    from backend.app.domain.models import UploadedFile

    frame = TINY_JPEG + b"shelf"
    repository.store_upload(
        survey_id,
        UploadedFile(
            path="shelf_scans/frames/0001.jpg",
            mime_type="image/jpeg",
            bytes=len(frame),
            sha256="c" * 64,
        ),
        frame,
    )
    VisionWorker(segment_spines=_fake_segmenter([_spine(0, 0.3)])).process(repository, survey_id)
    packages = []
    jev_states = []

    def provider(evidence):
        packages.append(json.loads(evidence))
        crop = "derived/yolo-spines/row_01_slot0.jpg"
        return {"model": "test-model"}, json.dumps(
            {
                "category": "book",
                "condition": "good",
                "damage": {"present": False, "types": [], "description": None},
                "identity_candidates": [],
                "recommended_action": "accept_candidate",
                "confidence": 0.95,
                "evidence_refs": [crop],
            }
        )

    inventory = repository.get_json(survey_id, "inventory") or {}
    copy_id = inventory["asset_copies"][0]["asset_copy_id"]
    result = replay_asset(
        repository,
        survey_id,
        copy_id,
        fable_call=provider,
        astra_call=provider,
        jev_call=lambda state: jev_states.append(state) or {
            "model": "jev-1.13.0",
            "answers": {
                "route": {
                    "choice": "accept_candidate",
                    "confidence": 0.96,
                    "probabilities": {
                        "accept_candidate": 0.96,
                        "recapture": 0.01,
                        "alternate_resolver": 0.01,
                        "human_review": 0.02,
                    },
                }
            },
        },
    )
    assert packages[0] == packages[1]
    assert packages[0]["evidence_refs"][0] == "derived/yolo-spines/row_01_slot0.jpg"
    assert packages[0]["media"][0]["evidence_ref"] == "derived/yolo-spines/row_01_slot0.jpg"
    assert "fable" in result["assessments"]
    assert "astra_replay" in result["assessments"]
    assert result["jev"]["choice"] == "accept_candidate"
    assert jev_states[0]["fable"]["category"] == "book"
    assert jev_states[0]["astra"]["category"] == "book"
    assert result["decision"]["action"] == "accept_candidate"


def test_replay_prefers_apple_vision_crop_over_yolo_mask() -> None:
    repository, survey_id = _repository()
    repository.save_json(
        survey_id,
        "inventory",
        {
            "asset_copies": [{
                "asset_copy_id": "copy-1", "category": "book",
                "observation_refs": ["obs-1"], "requires_appraisal": False,
                "row_id": "row_01", "slot": 0,
            }],
            "observations": [{
                "observation_id": "obs-1",
                "evidence_ref": "shelf_scans/crops/row_01_slot0.jpg",
            }],
        },
    )
    vision = TINY_JPEG + b"vision"
    yolo = TINY_JPEG + b"yolo"
    repository.put_bytes(survey_id, "shelf_scans/crops/row_01_slot0.jpg", vision, "image/jpeg")
    repository.put_bytes(survey_id, "derived/yolo-spines/row_01_slot0.jpg", yolo, "image/jpeg")
    packages = []

    def provider(evidence):
        packages.append(json.loads(evidence))
        refs = json.loads(evidence)["evidence_refs"]
        return {"model": "test-model"}, json.dumps({
            "category": "book", "condition": "good",
            "damage": {"present": False, "types": [], "description": None},
            "identity_candidates": [], "recommended_action": "accept_candidate",
            "confidence": 0.95, "evidence_refs": refs[:1],
        })

    replay_asset(
        repository, survey_id, "copy-1", fable_call=provider, astra_call=provider,
        jev_call=lambda _: {
            "model": "jev-1.13.0",
            "answers": {"route": {
                "choice": "accept_candidate", "confidence": 0.96,
                "probabilities": {
                    "accept_candidate": 0.96, "recapture": 0.01,
                    "alternate_resolver": 0.01, "human_review": 0.02,
                },
            }},
        },
    )
    assert packages[0]["evidence_refs"][0] == "shelf_scans/crops/row_01_slot0.jpg"
    assert "derived/yolo-spines/row_01_slot0.jpg" in packages[0]["evidence_refs"]
    assert packages[0]["media"][0]["evidence_ref"] == "shelf_scans/crops/row_01_slot0.jpg"


def test_yolo_module_does_not_load_moondream() -> None:
    import sys

    import cv.library_vision.yolo_spines as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "moondream" not in source.lower() or "Moondream2 is not used" in source
    assert "llama_cpp" not in source
    assert "MoondreamChatHandler" not in source
    assert not any(name.startswith("moondream") or "llama_cpp" in name for name in sys.modules)
    assert yolo_enabled() in {True, False}
