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
        "Views/OverviewView.swift",
        "Views/InventoryView.swift",
        "Views/PriceEvidenceView.swift",
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
