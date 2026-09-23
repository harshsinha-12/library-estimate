# Cable install — Library Survey on iPhone

Install the SwiftUI app on a physical iPhone over USB. These are the commands that worked on 2026-09-21 against **Harsh’s iPhone**.

## Prerequisites

- Xcode (iOS 17+ SDK)
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- LiDAR iPhone or iPad, unlocked, on a **data** USB cable
- On the phone: **Settings → Privacy & Security → Developer Mode → On** (restarts once)
- Apple Development identity on this Mac (Signing & Capabilities uses team `PH4KQ4LY92`)

## 1. Generate and open the project

```bash
cd "/Users/harshsinha/VS Code/library-roomplan/ios/LibrarySurvey"
xcodegen generate
open LibrarySurvey.xcodeproj
```

## 2. Confirm the phone is connected

```bash
xcrun xctrace list devices
```

You want a line like:

```text
Harsh’s iPhone (27.0) (00008150-000244892647401C)
```

The hex in parentheses is the device id. Unlock the phone and tap **Trust** if asked.

## 3. Build for the device

```bash
cd "/Users/harshsinha/VS Code/library-roomplan/ios/LibrarySurvey"

xcodebuild \
  -project LibrarySurvey.xcodeproj \
  -scheme LibrarySurvey \
  -destination 'id=00008150-000244892647401C' \
  -allowProvisioningUpdates \
  -allowProvisioningDeviceRegistration \
  DEVELOPMENT_TEAM=PH4KQ4LY92 \
  CODE_SIGN_STYLE=Automatic \
  -derivedDataPath /tmp/LibrarySurvey-dd \
  build
```

Replace the `id=` value if you are using a different phone.

## 4. Install over the cable

```bash
xcrun devicectl device install app \
  --device 00008150-000244892647401C \
  "/tmp/LibrarySurvey-dd/Build/Products/Debug-iphoneos/LibrarySurvey.app"
```

## 5. Launch

```bash
xcrun devicectl device process launch \
  --device 00008150-000244892647401C \
  dev.harshsinha.LibrarySurvey
```

Keep the phone unlocked for the first launch.

## If iOS blocks the app

**Settings → General → VPN & Device Management** → select the developer identity (**Harsh Sinha**) → **Trust**. Then open **Library Survey** again.

## From Xcode instead of the CLI

After `xcodegen generate` and `open LibrarySurvey.xcodeproj`:

1. Select the **LibrarySurvey** scheme.
2. Choose the connected iPhone as the destination (not a simulator).
3. **Signing & Capabilities** → Automatically manage signing → Team = your Apple ID.
4. Press **Run** (⌘R).

That compiles, signs, installs over USB, and launches.

## Backend URL on the phone

`127.0.0.1` on the iPhone is the phone itself. Capture and local sealing work without the backend. Uploads need this Mac’s LAN IP.

```bash
ipconfig getifaddr en0
```

Set that in `ios/LibrarySurvey/LibrarySurvey/Config/AppConfiguration.swift`:

```swift
static let defaultBackendURL = URL(string: "http://192.168.29.178:8000")!
```

Run as **one line**. If `--port 8000` is on the next line, the shell treats it as a new command and then the server is not running.

```bash
cd "/Users/harshsinha/VS Code/library-roomplan"
python3 -m uvicorn backend.app.main:production_app --factory --host 0.0.0.0 --port 8000
```

Or: `make run-backend`

Keep Mac and iPhone on the same Wi-Fi. The first upload may show **Local Network** — allow it (Settings → Privacy & Security → Local Network → Library Survey). If upload fails, the app stays open and shows the URL it tried.

`CFBundleVersion` in `Info.plist` / `project.yml` must stay the **string** `"1"`. An integer `1` crashes CFNetwork User-Agent init on the first HTTP call (`docs/upload-crash-handoff.md`).

After seal, the upload screen shows the tagged 2D plan (numbered colored walls, cm, compass N = scan +Z) and the 3D USDZ at the top. Do not hide them to work around the old crash; that abort was the bundle version, not SceneKit.

## One-shot copy-paste (this phone)

```bash
cd "/Users/harshsinha/VS Code/library-roomplan/ios/LibrarySurvey"
xcodegen generate
xcodebuild \
  -project LibrarySurvey.xcodeproj \
  -scheme LibrarySurvey \
  -destination 'id=00008150-000244892647401C' \
  -allowProvisioningUpdates \
  -allowProvisioningDeviceRegistration \
  DEVELOPMENT_TEAM=PH4KQ4LY92 \
  CODE_SIGN_STYLE=Automatic \
  -derivedDataPath /tmp/LibrarySurvey-dd \
  build
xcrun devicectl device install app \
  --device 00008150-000244892647401C \
  "/tmp/LibrarySurvey-dd/Build/Products/Debug-iphoneos/LibrarySurvey.app"
xcrun devicectl device process launch \
  --device 00008150-000244892647401C \
  dev.harshsinha.LibrarySurvey
```

## YOLO book boxes (Mac extra)

The phone draws the outlines. Ultralytics runs on the Mac, inside a virtual environment. Weights are `yolo11x-seg.pt` in the repo root (about 119 MB, not committed). Full steps: [README § Run](README.md#run).

```bash
cd "/Users/harshsinha/VS Code/library-roomplan"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[yolo]'
python -m uvicorn backend.app.main:production_app --factory --host 0.0.0.0 --port 8000
```

Rebuild and reinstall the iOS app (the steps above). In the app, set Backend URL to `http://<Mac-Wi-Fi-IP>:8000`. Open Pass B and point at a shelf. `Start sweep` is only required to track copies; the outline is a preview.

The phone never installs ultralytics. If the extra or the weights file is missing, Pass B says so.

