# Stage 1 device validation

Stage 1 is not complete until this checklist is run on a supported LiDAR iPhone or iPad. A simulator build proves compilation only; it does not prove RoomPlan capture, camera ownership, location behavior, or USDZ rendering.

## Setup

1. Run `make run-backend` on the Mac.
2. Set `AppConfiguration.defaultBackendURL` to the Mac's reachable local-network address when testing on a physical device.
3. Run `make ios-project`, open `ios/LibrarySurvey/LibrarySurvey.xcodeproj`, select the device, and provide the signing team.

## Required gate run

- [x] Create a survey and explicitly allow camera and microphone access (Home scan and uploaded audio).
- [x] Deny location access, enter country and city manually, and confirm `device/location.json` has `source: manual` with no coordinates (`69d0a6d6` and `0cbeda56`: `source: manual`, city `Bareilly `, null lat/lon). Operator confirmed the deny path.
- [x] Repeat with location allowed and confirm `source: gps` or `mixed` as appropriate (`74de486b` and `eb3f30fa`: `source: gps`, Bareilly, Uttar Pradesh, lat/lon present).
- [x] Pass the device check on a supported LiDAR device (Home reached `geometry` on iPhone 17 Pro).
- [x] Scan one room and confirm periodic RGB frames and camera transforms are present (`eb3f30fa`: 38 JPEGs and 38 poses).
- [x] Periodic sampling succeeded for `eb3f30fa`, `74de486b`, and Home; final-frame fallback was not needed for these runs.
- [x] Record one spoken note **and** one written note; confirm both use the same monotonic-clock basis (`eb3f30fa`: two written notes “This is my room” / “Testing things”, `audio/timing.json` start 0.19s after capture anchor, duration 37.9s).
- [x] Seal, quit the app, reopen the package, and verify every manifest SHA-256. Operator: force-quit still restores the last sealed survey (hash verify off the main thread, then sealed screen). That is intended; **Start Another Survey** starts a new one.
- [x] Confirm `checksums.sha256`, processed structure JSON, RoomPlan USDZ, tagged 2D SVG, audio, notes, frames, poses, and location files exist. `eb3f30fa` hashes: all 49 checksum entries match; `derived/plan.svg` exists; `generated/plan.svg` was **not** overwritten.
- [x] On the sealed/upload screen, confirm both previews stay visible at the top: tagged 2D plan and interactive 3D USDZ. Operator: 2D/3D remain after relaunch of the restored package.
- [x] Interrupt an upload, retry it, and confirm acknowledged files are not uploaded twice. Operator confirmed a Wi-Fi drop then Resume. Backend `uploaded_files` has unique paths per survey (no second write). `cf61c539` is an abandoned partial upload (`uploading`, 1 file) left behind when a new survey was started.
- [x] Confirm the backend state history contains Created → Capturing → Uploading → IngestValidation → Geometry (`69d0a6d6`, `0cbeda56`, `74de486b`, `eb3f30fa`).

Record the device model, iOS version, package identifier, backend test output, screenshots of 2D/3D, and any fallback used in `SESSION-RUN.md` before checking the Stage 1 gate.

## Current evidence and remaining device actions

Stage 1 device gate is closed on operator confirmation plus package inspection. Canonical sealed capture: `eb3f30fa` (GPS, notes, tagged plan, intact hashes, `derived/`). Manual geography: `69d0a6d6` / `0cbeda56`. GPS geography: `74de486b` / `eb3f30fa`.

Older Home/`74de486b` server copies still have an overwritten `generated/plan.svg` from the previous uvicorn process; leave those manifests alone. Transcription/TTS remains Stage 3.

Launching the app restores the last sealed survey on purpose. Use **Start Another Survey** for a new capture. `cf61c539` can stay `uploading` (1 file); it is not the current package.
