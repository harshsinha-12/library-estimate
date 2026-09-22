import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS_ROOT = ROOT / "ios" / "LibrarySurvey"


def test_ios_stage_one_permissions_and_sources_exist() -> None:
    info_path = IOS_ROOT / "LibrarySurvey" / "Resources" / "Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)

    assert info["NSCameraUsageDescription"]
    assert info["NSMicrophoneUsageDescription"]
    assert info["NSLocationWhenInUseUsageDescription"]
    assert isinstance(info["CFBundleVersion"], str), (
        "CFBundleVersion must be a string; an integer crashes CFNetwork "
        "User-Agent init with -[__NSCFNumber length]"
    )

    expected_sources = {
        "Capture/FrameSampler.swift",
        "Capture/RoomCaptureContainer.swift",
        "Capture/RoomCaptureStore.swift",
        "Capture/CameraSessionCoordinator.swift",
        "Export/CapturePackageWriter.swift",
        "Export/FloorPlanLayout.swift",
        "Export/RoomPlanSVGRenderer.swift",
        "Views/TaggedFloorPlanView.swift",
        "Services/LocationService.swift",
        "Services/SurveyUploadService.swift",
        "Views/ShelfMapView.swift",
        "Views/ShelfPassView.swift",
        "Capture/ShelfCaptureStore.swift",
        "Capture/LiveQualityAnalyzer.swift",
        "Services/LivePriceAssist.swift",
        "Services/AstraLiveAssist.swift",
        "Views/OverviewView.swift",
        "Views/InventoryView.swift",
        "Views/PriceEvidenceView.swift",
        "Views/ReportView.swift",
        "Models/Stage4Models.swift",
    }
    source_root = IOS_ROOT / "LibrarySurvey"
    assert all((source_root / relative).is_file() for relative in expected_sources)
    assist = (source_root / "Services/LivePriceAssist.swift").read_text(encoding="utf-8")
    assert "static func identities(from jpeg: Data)" in assist
    assert "func considerObject(" in assist
    analyzer = (source_root / "Capture/LiveQualityAnalyzer.swift").read_text(encoding="utf-8")
    assert "maximumAspectRatio = 1.05" in analyzer
    shelf = (source_root / "Views/ShelfPassView.swift").read_text(encoding="utf-8")
    capturing = shelf.split("if store.capturing")[1].split("} else {")[0]
    assert "Finish face" in capturing
    assert "Point out object" not in capturing
    assert "coverageHeatmap" not in capturing
    store = (source_root / "Capture/ShelfCaptureStore.swift").read_text(encoding="utf-8")
    assert "shelf_scans/crops/" in store
    room = (source_root / "Views/RoomPassView.swift").read_text(encoding="utf-8")
    bar = room.split("private var capturingBar")[1].split("private var status")[0]
    assert "Finish Room Scan" in bar
    assert "Point out object" not in bar
    assert "Written note" not in bar
    assert "Possible book" not in room
    assert "startFace" not in room
    assert "ingestCurrentFrame" not in room
    price = (source_root / "Views/PriceEvidenceView.swift").read_text(encoding="utf-8")
    assert "Run Fable, Astra, and Jev" in price
    assert "model-replay" in price
    assert "Results for this named copy appear here" in price
    assert "after seal" in price.lower() or "After seal" in price
    report = (source_root / "Views/ReportView.swift").read_text(encoding="utf-8")
    assert "modelRuns" in report
    assert "Fable, Astra, and Jev" in report
    assert "after seal" in report.lower() or "After seal" in report
    live = (source_root / "Services/AstraLiveAssist.swift").read_text(encoding="utf-8")
    assert "astra-live" in live
    assert "maxCalls = 6" in live
    assert "not inventory" in live.lower() or "assist_metadata" in live
    assert "astraLive" in shelf or "AstraLiveSession" in shelf
    exception = (source_root / "Views/ExceptionPassView.swift").read_text(encoding="utf-8")
    assert "AstraLiveSession" in exception
    assert "astra-live" in exception or "astraLive" in exception
    inventory = (source_root / "Views/InventoryView.swift").read_text(encoding="utf-8")
    assert "after seal" in inventory.lower()
    project = (IOS_ROOT / "LibrarySurvey.xcodeproj" / "project.pbxproj").read_text(
        encoding="utf-8"
    )
    assert "AstraLiveAssist.swift" in project


def test_ios_camera_session_is_sequential() -> None:
    source_root = IOS_ROOT / "LibrarySurvey"
    coordinator = (source_root / "Capture/CameraSessionCoordinator.swift").read_text(
        encoding="utf-8"
    )
    assert "enum Owner" in coordinator
    assert "case roomPlan" in coordinator
    assert "case shelfAR" in coordinator
    assert "case stillCamera" in coordinator
    assert "func tryAcquire" in coordinator
    assert "Optical zoom is not available" in coordinator

    room_store = (source_root / "Capture/RoomCaptureStore.swift").read_text(encoding="utf-8")
    assert "func releaseGeometrySession" in room_store
    assert "sequentialFinalFrame" in room_store
    assert "Sequential fallback" in room_store

    room_container = (source_root / "Capture/RoomCaptureContainer.swift").read_text(
        encoding="utf-8"
    )
    assert "dismantleUIView" in room_container
    assert "arSession.pause" in room_container

    shelf_container = (source_root / "Capture/ShelfCameraContainer.swift").read_text(
        encoding="utf-8"
    )
    assert "automaticallyConfiguredSession = false" in shelf_container
    assert "dismantleUIView" in shelf_container
    assert "tryAcquire(.shelfAR)" in shelf_container

    shelf_store = (source_root / "Capture/ShelfCaptureStore.swift").read_text(encoding="utf-8")
    assert "ARSession()" not in shelf_store
    assert "func releaseCamera" in shelf_store
    assert "operatorKind" in shelf_store or "placement: box.placement" in shelf_store

    room = (source_root / "Views/RoomPassView.swift").read_text(encoding="utf-8")
    assert "UIImagePicker" not in room
    assert "shelves.attach" not in room
    assert "Optical zoom is not available" in room
    assert "Close-ups are a later still" in room

    exception = (source_root / "Views/ExceptionPassView.swift").read_text(encoding="utf-8")
    assert "tryAcquire(.stillCamera)" in exception
    assert "geometrySessionActive" in exception
    assert "UIImagePickerController" in exception

    device = (source_root / "Views/DeviceCheckView.swift").read_text(encoding="utf-8")
    assert "Sequential fallback" in device
    assert "second AVCaptureSession" in device

    writer = (source_root / "Export/CapturePackageWriter.swift").read_text(encoding="utf-8")
    assert '"camera_ownership": "sequential"' in writer
    assert "rgb_evidence_mode" in writer
    assert "shelf_footprints" in writer

    map_view = (source_root / "Views/ShelfMapView.swift").read_text(encoding="utf-8")
    assert "operator-placed" in map_view
    assert "unregistered overlay" in map_view
    assert "setFootprint" in map_view

    project = (IOS_ROOT / "LibrarySurvey.xcodeproj" / "project.pbxproj").read_text(
        encoding="utf-8"
    )
    assert "CameraSessionCoordinator.swift" in project
