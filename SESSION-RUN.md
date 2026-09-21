# Current session run

**Date:** 2026-09-21  
**Status:** In progress  
**Scope:** Stage 1 capture/upload on device, tagged 2D + 3D on the sealed screen, credentials/contracts

## Completed in this session

- [x] Read the repository entry point in `README.md`.
- [x] Read the five-stage implementation guide in `IMPLEMENTATION.md`.
- [x] Reviewed the architecture outline and implementation-phase headings in `FINAL-PLAN.md`.
- [x] Confirmed, without printing secret values, that the following variables are present:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `REDIS_USERNAME`
  - `REDIS_PASSWORD`
  - `REDIS_HOST`
  - `REDIS_PORT`
- [x] Confirmed that the local environment file is not tracked by Git.
- [x] Recorded the model direction supplied for this implementation:
  - OpenAI for voice generation.
  - OpenAI for smaller-model tasks.
  - Preserve the documented Astra and Fable roles.
- [x] Created this session log and `CHECKPOINTS.md`.
- [x] Received approval to reuse the existing OpenAI API key.
- [x] Verified the current OpenAI TTS model and chose configurable defaults:
  - `gpt-4o-mini-tts` with the `marin` voice for speech.
  - `gpt-4o-mini` for focused smaller-model tasks.
- [x] Added canonical JSON Schemas for geography, capture packages, Survey IR, and normalized Fable/Astra assessments.
- [x] Added valid Stage 1 fixtures.
- [x] Implemented FastAPI survey creation, raw evidence upload, and package sealing.
- [x] Added safe relative-path validation, byte/MIME/SHA-256 verification, canonical package hashes, and persisted idempotency.
- [x] Added temporary Stage 1 SQLite metadata persistence and local evidence storage; user has since ruled out SQLite for the target backend.
- [x] Split configuration, utility helpers, persistence, workflows, provider contracts, and provider-specific response parsing into separate modules.
- [x] Added catalog and pricing fetcher interfaces for source-specific implementations.
- [x] Added strict Fable and Astra replay response validation before the future Jev boundary.
- [x] Added project commands for linting, tests, compilation, and local API startup.
- [x] Passed 9 backend/schema tests, Ruff, Python compilation, and `git diff --check`.
- [x] Expanded Survey IR to include every required entity family.
- [x] Added evidence-package, price-observation, review-decision, and RL-transition schemas plus validating fixtures.
- [x] Added a deterministic geometry worker that converts processed RoomPlan wall transforms into SVG and an explicitly estimated geometry summary.
- [x] Added persisted survey-state events and tested Created → Capturing → Uploading → IngestValidation → Geometry.
- [x] Added backend package reopen and SHA-256 revalidation after manifest persistence.
- [x] Added the SwiftUI `LibrarySurvey` app with:
  - Create Survey, consent, manual geography, and one-shot GPS reverse-geocoding.
  - Device checks for RoomPlan/LiDAR, storage, camera, microphone, location fallback, battery, and optional network.
  - RoomPlan Pass A on its AR session with periodic RGB/pose samples and a final-frame fallback.
  - Multi-room `StructureBuilder` normalization, portable structure JSON, and RoomPlan USDZ export.
  - Spoken-note audio and written notes on the monotonic capture clock.
  - Local package construction, `checksums.sha256`, manifest verification, and resumable per-file upload state.
  - In-app 2D SVG and interactive 3D USDZ previews.
- [x] Added XcodeGen configuration and generated the shared Xcode project.
- [x] Generated and integrated a 1024×1024 Library Survey app icon into the iOS asset catalog.
- [x] Added the physical-device gate checklist in `docs/stage-1-device-validation.md`.
- [x] Passed 13 backend/schema/static-iOS tests, Ruff, Python compilation, plist validation, `git diff --check`, and a generic iOS Simulator build.

## Decisions still required

- [x] Reuse the existing `OPENAI_API_KEY`.
- [x] Interpret “voice GTS” as OpenAI TTS and use `gpt-4o-mini-tts`.
- [ ] Confirm the exact provider model IDs/API access for the documented Fable and Astra roles before implementing their live calls.

## Implementation state

Stage 1 device gate is closed. Canonical package `eb3f30fa`. Stage 2 Redis + R2 + shelf count is gated. Do not treat SQLite as the target backend.

**Storage decision (2026-09-21):** Redis for structured survey state; Cloudflare R2 via the S3 API for sealed media. SQLite remains only as a preserved Stage 1 archive and is migrated on API startup.

## Verification performed

- Credential presence was checked using redacted presence/absence checks only.
- `git status --short` was inspected before creating these tracking files.
- No paid provider API request was made.
- No secrets were displayed or modified.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest`: 13 passed.
- `python3 -m ruff check backend`: passed.
- `python3 -m compileall -q backend`: passed.
- `git diff --check`: passed.
- `plutil -lint ios/LibrarySurvey/LibrarySurvey/Resources/Info.plist`: passed.
- Generic iOS Simulator `xcodebuild`: passed with code signing disabled.
- App-icon asset validation: 1024×1024 PNG, no alpha, compiled as `AppIcon` into `Assets.car`; iOS 27 SDK simulator build passed after integration.

## 2026-09-21 afternoon — upload unblocked, tagged 2D + 3D restored

Physical-device capture and ingest now have a successful end-to-end package. Stage 1 is still not gated: location-deny, note inspection, and interrupt/retry remain.

- **Upload crash (verified fixed).** First `URLSession` aborted with `-[__NSCFNumber length]` in CFNetwork User-Agent init because `CFBundleVersion` was an integer. It is now the string `"1"`. SceneKit/USDZ on the sealed screen was not the abort. Details in `docs/upload-crash-handoff.md`.
- **Idempotency.** Identical bytes at different paths (`roomplan/raw/room-data.json` and `roomplan/processed/structure.json`) need distinct keys. iOS uses `upload-{path}-{sha256}`.
- **Sealed Home survey.** `survey_id` `69d0a6d6-6ded-4280-bc07-eca057aa3f80`, display name Home, city `Bareilly ` (trailing space), `status` `geometry`, `package_hash` `0085335cf7251829dc4daf4cce41187066fe5c7bafd0d0aecb482e8338f5fe51`. Files under `data/runtime/uploads/69d0a6d6-6ded-4280-bc07-eca057aa3f80/`. This capture had five walls and no RoomPlan doors/windows.
- **2D/3D on the sealed screen.** Previews are visible at the top during upload again. 3D is a load-once SceneKit USDZ. 2D is a Cosmo-style tagged plan: numbered colored walls, centimetre lengths, openings as gaps on the parent wall, wall/door/window list, compass **N = scan +Z, not magnetic north**. Share uses a temp tagged SVG and does not rewrite the hashed package. Visual spec: `docs/cosmo-tagged-plan-reference.png`.
- **Code.** iOS `Export/FloorPlanLayout.swift`, `Views/TaggedFloorPlanView.swift`; backend `workflows/floor_plan.py` drives the tagged server-derived plan.
- **Tests.** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest backend/tests/test_geometry.py backend/tests/test_floor_plan.py backend/tests/test_ios_project.py backend/tests/test_survey_api.py`: 10 passed. Device build installed and launched (`devicectl` may need a 2s retry after “profile not trusted”).

## 2026-09-21 — Stage 1 recovery and integrity pass

- Home's server copy was checked against `checksums.sha256`: 28 files matched and `generated/plan.svg` failed. The SVG timestamp is later than the seal; geometry had overwritten a manifest-hashed input. The original Home package and manifest were left untouched.
- Geometry now writes `derived/plan.svg` and `derived/geometry.json`; uploads into `derived/` are rejected. A regression test seals an original SVG, runs geometry, restarts the API, and confirms the original bytes and idempotent seal replay. A second test retries an acknowledged upload after restart, uploads the remaining file, and reaches `geometry` without duplicate state events.
- iOS sealed-package reopen now verifies every manifest file off the main UI thread and displays an error on corruption. New recorded audio gets `audio/timing.json` with start/end monotonic seconds, matching the clock used by written notes. Transcription remains Stage 3.
- `make check`: 19 tests passed; Ruff and Python compilation passed. A signed build compiled, installed, and launched on the wired iPhone 17 Pro running iOS 27.0. A screenshot after launch showed a Create Survey form, not the sealed screen, so it cannot certify 2D/3D recovery. A fresh device run is still required for location denial/grant package inspection, spoken and written note timing, quit/reopen, 2D/3D screenshots, and interrupted upload retry.
- The user's observed dimensioned plan and GPS-fetch behavior are acknowledged, but no new GPS-sourced sealed package was available for inspection in this run. Stage 1 remains open.

## 2026-09-21 13:29 — verified survey `74de486b`

Inspected the package the phone just sealed (`GET` 200, `status: geometry`). This **does not close Stage 1**.

Passed against the files on disk and sqlite:

- Upload log: every manifest path 200, then `POST .../seal` 200. State events: created → capturing → uploading → ingest_validation → geometry.
- `device/location.json`: `source: gps`, city Bareilly, region Uttar Pradesh, lat/lon present. Closes the location-**granted** row.
- 30 RGB frames and 30 poses. Capture window 34s (`2026-09-21T07:58:35Z`–`07:59:09Z`).
- Spoken audio `audio/survey.m4a` (310 810 bytes) plus `audio/timing.json`: start 0.20s after `monotonic_anchor_seconds`, duration 31.4s. Same clock as the manifest.
- 40 of 41 `checksums.sha256` entries match the files and the manifest.

Failed / incomplete:

- `notes/annotations.json` is `[]`. No written note to compare clocks with.
- `generated/plan.svg` was overwritten after seal (manifest 1696 bytes / `f9b99bf3…`; disk 506 bytes / `8b40f03e…`, old beige untagged SVG). `derived/` was not created. Uvicorn PID **45457** is still the pre-`derived/` worker. Restart it before the next seal.
- RoomPlan on this pass is incomplete: 2 walls, USDZ 14 724 bytes.
- No interrupt/retry (each path uploaded once).
- No quit/reopen observation and no 2D/3D sealed-screen screenshot.

Related: `0cbeda56` is `source: manual` with null coordinates, but it is an earlier package (no `audio/timing.json`) and does not count as an observed deny-run on the current build.

## 2026-09-21 13:37 — verified survey `eb3f30fa`

Seal 200 on the restarted backend. `GET` `status: geometry`, display name My room, `package_hash` `3d5f5fd8…`.

Passed:

- All 49 `checksums.sha256` entries match. `derived/plan.svg` exists (tagged, 6 walls, 2 doors). `generated/plan.svg` still matches the manifest (not overwritten).
- GPS geography (Bareilly, Uttar Pradesh).
- 38 frames + 38 poses; 6 walls; USDZ 55 603 bytes; shoelace area ~12.7 m².
- Written notes: “This is my room”, “Testing things”. Spoken `audio/timing.json` starts 0.19s after the capture anchor (37.9s). Same monotonic clock.
- State path created → capturing → uploading → ingest_validation → geometry.

Still open for Stage 1: location-deny survey, quit/reopen, 2D/3D screenshots, interrupt/resume. Upload this time was uninterrupted.

## 2026-09-21 13:39 — operator closed remaining Stage 1 device rows

Operator: 2D/3D work; force-quit restores the last sealed survey; Wi-Fi interrupt then Resume was done once; location deny was done earlier.

Package evidence matches that:

- Reopen is `RootView.restoreSealedPackageIfNeeded` + off-main-thread SHA-256 of every manifest file. Restoring “the older one” is intended. **Start Another Survey** for a new capture.
- 2D/3D are on that restored sealed screen (`eb3f30fa` tagged plan + USDZ).
- Manual location: `69d0a6d6` and `0cbeda56` are `source: manual` with null coordinates. GPS: `74de486b` and `eb3f30fa`.
- Resume: `uploaded_files` has unique paths per survey (no second write of an ACK’d file). `cf61c539` remains `uploading` with 1 file from an earlier abandoned attempt.

Stage 1 clock checkbox is marked complete. Speech-to-text is still Stage 3.

## 2026-09-21 14:20 — Stage 2 Redis, R2, Pass B

R2 is used through the S3 API only. The `library-scanner` bucket stays private (no r2.dev public access, no custom domain). Server-side Access Key ID / Secret Access Key / endpoint / bucket names are in `.env.local`; they are not in the iOS app. The Cloudflare API token values are unused. Rotate the S3 keys after this demo, as planned.

Gate exercised:

- Live Redis ping/round-trip and R2 put/get/delete succeeded (no credentials logged).
- `make check`: 24 tests passed, including Redis-backed create/upload/seal/reopen/restart, no SQLite import in the runtime repository, sqlite archive preserved by migrator, labeled shelf count.
- Labeled shelf: reverse rescan does not double; two same-ISBN copies in different slots stay two `AssetCopy` rows; uncovered `row_03` is `partial` with a disclosed count interval, never a silent zero; face B walkaround stays separate; `possibly_moved` is set on identity match + spatial jump.
- Inventory and 2D overlay expose `ShelfFaceDataSize` (`GET /v1/surveys/{id}/inventory`, `GET /v1/surveys/{id}/shelves`, `data-shelf` on derived SVG). Vision jobs are idempotent on `survey_id + stage + input_hash + pipeline_version`.
- iOS Shelf Map + Pass B (quality overlay, coverage heatmap, recapture named rows, live assist count) compile in the generic Simulator build.

Run the API with `python3 -m uvicorn backend.app.main:production_app --factory --host 0.0.0.0 --port 8000`. First start migrates Stage 1 sqlite rows/files into Redis + R2 and leaves the sqlite file on disk.

## 2026-09-21 14:32 — phone upload to Redis + R2

Operator sealed a new survey from the phone against the production factory server. Create, every file upload, and seal returned 200 from `192.168.29.179`.

- `survey_id` `371ea852-15fd-4471-a1b0-92b70a777e8d` (`371EA852`), display name Test with Redis and Cloudflare
- `GET` status `geometry`; events created → capturing → uploading → ingest_validation → geometry
- Geography `gps`, city Bareilly
- Redis holds 53 uploaded-file records; R2 has 56 objects including `manifest.json`, `roomplan/model.usdz`, sampled JPEG frames, audio, and server `derived/plan.svg`

This is a device confirmation of the Stage 2 storage cutover. Pass B shelf content on a later capture is still the next operator check.

## Session update rule

Append a dated entry whenever a meaningful implementation unit is completed, tested, blocked, or deliberately deferred. Do not mark a stage complete until its documented exit criteria pass.

## 2026-09-21 15:31 IST — Stage 3 identity, pointing, voice, and damage gate

Stage 3 is gated. No Stage 4 Bing pricing or Stage 5 model/RL work was started.

- Pass C now writes hashed scans, close-ups, scale images, focus events, and notes into the sealed package. On-device Vision reads barcodes and text; provisional outlines on shelf RGB frames and captured images are tappable. A tap saves a zoomed crop and carries shelf face/row/slot, clock time, and camera pose into Pass C. `question.md`'s point-while-speaking interaction is represented by focus events; audio continues through shelf and exception capture until seal. These rectangle outlines are provisional candidates, not verified book recognition.
- Other assets can be pointed out during the RoomPlan pass or Pass B, or marked after them. The room pass has a pointed-object capture sheet for photo frames and other items while audio continues. Closed taxonomy and policy count a mug as excluded (`valuation_required=false`) and mark portrait/art for appraisal. Damage is stored as an operator assertion with a region, severity candidate, close-up, and scale; missing image bytes create a visible recapture item.
- Identifier typing covers ISBN-10/13 checksums, 979, ISSN, generic retail EAN rejection, library barcodes, and set/volume scope. A bad checksum clears the Stage B ISBN hint and has `usable_for_isbn_price_query=false`. AssetCopy IDs remain independent of ISBN. Catalog identity uses cache → Open Library → Google Books → manual; Google `saleInfo` is discarded. Title-only books remain candidates requiring edition review.
- OpenAI timed STT converts sealed audio to capture-clock segments; server-side TTS returns operator prompt MP3. Notes use explicit tap, reticle/focus history, shelf slot, camera pose, time, and semantic class. Ambiguous notes remain unbound. The review API and iOS screen expose bind note, rescan barcode, and keep unresolved actions.
- Gate fixture: `backend/tests/test_stage3_gate.py` passed 4/4. Its sealed API run checks invalid ISBN exclusion, no-ISBN copy ID across a new app instance using the same Redis/object store, a spoken portrait damage assertion linked to `portrait-1` and `closeups/damage.jpg`, mug counted/excluded, an open unresolved queue, review binding, and every demo taxonomy class. The STT result in this fixture is controlled; it tests clock/focus association independently of provider speech recognition.
- Live provider checks: authenticated Redis and private R2 round-trip passed. OpenAI STT on canonical `eb3f30fa` audio returned 6 timestamped segments, all aligned to the capture clock; no transcript was printed. OpenAI TTS produced 57,600 bytes for a prompt; a corrected MP3 frame-header check passed on a second prompt. Open Library returned identity metadata for a valid ISBN. Google Books returned HTTP 429 during a live read; the chain handles that as an unavailable source and exposes manual review. Mocked fallback and saleInfo stripping passed in tests. No catalog price was used.
- Final verification: `make check` passed (29 tests, Ruff, Python compilation). Generic iOS Simulator build passed with signing disabled; `git diff --check` passed. Physical-device accuracy of the new live outlines, zoom alignment, and spoken portrait interaction has not been observed in this run, so the gate evidence is the sealed fixture plus live storage/voice service checks, not a field trial.
- Credential handling correction: an initial sandboxed live-storage test failed at DNS resolution, and pytest expanded the `Settings` dataclass in its traceback. `Settings` now hides Redis and S3 credentials from `repr`, with a regression check. The affected credentials should be rotated because the earlier local tool output included them; no values are repeated here.

## 2026-09-21 15:46 IST — Stage 3 build installed for operator testing

- Signed the current `dev.harshsinha.LibrarySurvey` Debug build for the connected iPhone 17 Pro (`Harsh’s iPhone`), installed it with `devicectl`, and launched it. A device screenshot showed the app open on its sealed-survey screen.
- Restarted the production factory API on `0.0.0.0:8000` so it serves the Stage 3 code. Health returned 200, the Stage 3 review route returned 200, and the Mac's active Wi-Fi address is `192.168.29.178`, matching the app's backend field.
- The phone uploaded and sealed survey `fd8c3a9f-c720-4c2a-8ba1-b6a0f4df5ca2` (`Test New`, 72 package files). Backend status is `geometry`; Stage 3 speech status is `transcribed`. This capture has no counted assets because it contained no shelf/other-asset scans, and its review queue contains 10 items. A second seal request briefly returned 409 while the first was in `ingest_validation`; the phone subsequently displayed “Uploaded and accepted.”
- The installed capture UI, live book/object outlines, point-to-zoom alignment, and portrait association still need the operator's physical-device test with a new survey. The existing sealed survey is restored on launch; use **Start Another Survey** for that run.

## 2026-09-21 15:55 IST — 8–10 book row acceptance clarified

- Planning clarification only; no application code was inspected or changed in this pass. `IMPLEMENTATION.md` and `FINAL-PLAN.md` now require a single physical shelf row to be traced from live spine outlines and distinct `AssetCopy` records through evidenced ISBN/name or per-copy Pass C tasks, then reviewed local physical-book price ranges or explicit pending/no-comparable states, and finally a selectable inventory/report roster.
- The earlier Stage 2 storage/count fixture gate and Stage 3 identity/damage fixture gate remain recorded as passed. Their newly added physical-device row gates are open, so the Stage 2 and Stage 3 clock checkboxes in `CHECKPOINTS.md` were reopened. Stage 4 and Stage 5 remain open. No 8–10 book phone row, per-copy identity roster, or price roster has been verified yet; the previous phone survey had no shelf observations.
- Exit evidence required: manual actual row count; one outlined, stable physical-copy record per visible book; reverse sweep deduplication; per-copy identity evidence or explicit unresolved action; per-copy price evidence/status where eligible; detected/actual and priced/eligible numerators and denominators. Record the on-device result before closing the added gates.

## 2026-09-21 15:58 IST — Row walkthrough assigned to Stage 4

- Per operator direction, the 8–10 book physical-device walkthrough is now the first Stage 4 task and gate. Stage 2 and Stage 3 retain their completed clock checkboxes and original gate evidence; the prior 15:55 reopening is superseded. Stage 5 returns to its original 12-step demo gate. The row work remains unverified and Stage 4 remains open.
- Stage 4 begins with row outlines/count reconciliation, per-copy identity or Pass C task, and a usable row detail, then continues through Bing price evidence and per-copy valuation status. This planning correction changes no application code.
