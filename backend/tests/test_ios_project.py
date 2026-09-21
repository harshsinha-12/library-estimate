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
        "Views/PackagePreviewView.swift",
    }
    source_root = IOS_ROOT / "LibrarySurvey"
    assert all((source_root / relative).is_file() for relative in expected_sources)
